from django.db import transaction
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Max, ProtectedError, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.views import IsAdminRole

from company_settings.models import CompanySettings
from inventory.models import Product
from orders.models import Order
from orders.services import order_fulfillment_data

from . import services
from .models import (
    Container, ContainerEvent, ContainerEventType, ContainerLine,
    ContainerStatus, Inventory, InventoryLine, InventoryStatus,
    Movement, MovementType, ProductionRun, ScrapEntry, Stock, Warehouse,
)
from .pdf import (
    generate_container_label_pdf,
    generate_container_labels_pdf,
    generate_packing_list_pdf,
    generate_transfer_note_pdf,
)
from .serializers import (
    ContainerDetailSerializer, ContainerEventSerializer, ContainerLineSerializer,
    ContainerListSerializer, InventorySerializer, MovementSerializer,
    ProductionRunCreateSerializer, ProductionRunDetailSerializer,
    ProductionRunListSerializer, StockSerializer, WarehouseSerializer,
)
from .services import (
    ServiceError, apply_movement, free_stock, get_reserve_warehouse,
    reserve_stock_for_product,
)

User = get_user_model()


def _user_is_foundry(user) -> bool:
    """True, если юзер — Литейка. Видит только склад Завод."""
    return getattr(user, "role", None) == "foundry"


def _get_zlk_warehouse():
    return Warehouse.objects.filter(code="ZLK").first()


# ============================================================================
# Склады / остатки
# ============================================================================
class WarehouseViewSet(viewsets.ModelViewSet):
    queryset = Warehouse.objects.all()
    serializer_class = WarehouseSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ("name", "code")


class StockViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Stock.objects.select_related("warehouse", "product").all()
    serializer_class = StockSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ("product__article", "product__name")
    ordering_fields = ("product__article", "quantity")

    def get_queryset(self):
        qs = super().get_queryset()
        wid = self.request.query_params.get("warehouse")
        if wid:
            qs = qs.filter(warehouse_id=wid)
        pid = self.request.query_params.get("product")
        if pid:
            qs = qs.filter(product_id=pid)
        return qs


# ============================================================================
# Тара
# ============================================================================
class ContainerViewSet(viewsets.ModelViewSet):
    queryset = (Container.objects.select_related("product", "warehouse", "order")
                .prefetch_related("events", "lines", "lines__product").all())
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ("code", "product__article", "product__name",
                     "lines__product__article", "lines__product__name")
    ordering_fields = ("created_at", "code")

    def get_serializer_class(self):
        if self.action == "list":
            return ContainerListSerializer
        return ContainerDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()

        # ── Изоляция по роли ────────────────────────────────────
        # Литейка видит ТОЛЬКО тары на складе Завод.
        # Админ и обычные юзеры видят всё, КРОМЕ Завод (это склад литейки).
        # Явный фильтр ?warehouse= уважается только для не-литейки.
        if _user_is_foundry(self.request.user):
            zlk = _get_zlk_warehouse()
            qs = qs.filter(warehouse=zlk) if zlk else qs.none()
        else:
            qs = qs.exclude(warehouse__code="ZLK")
            wid = self.request.query_params.get("warehouse")
            if wid:
                qs = qs.filter(warehouse_id=wid)

        st = self.request.query_params.get("status")
        if st:
            qs = qs.filter(status=st)

        # PACKED-FILTER: фильтр по упаковке
        packed = self.request.query_params.get("packed")
        if packed == "1":
            qs = qs.filter(packed_at__isnull=False, shipped_at__isnull=True)
        elif packed == "0":
            qs = qs.filter(packed_at__isnull=True, shipped_at__isnull=True)

        code = self.request.query_params.get("code")
        if code:
            qs = qs.filter(code=code)
        return qs

    def perform_create(self, serializer):
        user = self.request.user
        warehouse = serializer.validated_data["warehouse"]

        # Литейка может создавать тары только на складе Завод
        if _user_is_foundry(user) and warehouse.code != "ZLK":
            raise ServiceError(
                "Литейка может создавать тары только на складе Завод"
            )

        container = services.container_create(
            product=serializer.validated_data["product"],
            quantity=serializer.validated_data["quantity"],
            warehouse=warehouse,
            note=serializer.validated_data.get("note", ""),
            user=user,
        )
        serializer.instance = container

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
        except ServiceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            self.perform_destroy(instance)
        except ProtectedError as e:
            names = ", ".join(f"{obj._meta.verbose_name} «{obj}»"
                              for obj in list(e.protected_objects)[:5])
            return Response({"error": f"Нельзя удалить тару: используется в {names}"},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": f"Ошибка удаления: {e}"},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        container = self.get_object()
        try:
            services.container_issue(container=container, user=request.user,
                                     comment=request.data.get("comment", ""))
        except ServiceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ContainerDetailSerializer(container).data)

    @action(detail=True, methods=["post"], url_path="return")
    def return_from_production(self, request, pk=None):
        container = self.get_object()
        pid = request.data.get("product_id")
        qty = request.data.get("quantity")
        if not pid or qty is None:
            return Response({"error": "product_id и quantity обязательны"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            product = Product.objects.get(pk=pid)
        except Product.DoesNotExist:
            return Response({"error": "Артикул не найден"}, status=status.HTTP_404_NOT_FOUND)
        try:
            services.container_return(container=container, result_product=product,
                result_quantity=qty, user=request.user,
                comment=request.data.get("comment", ""))
        except ServiceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        data = ContainerDetailSerializer(container).data
        data["can_print_packing_list"] = True
        return Response(data)

    @action(detail=True, methods=["post"])
    def ship(self, request, pk=None):
        container = self.get_object()
        order = None
        oid = request.data.get("order_id")
        if oid:
            try:
                order = Order.objects.get(pk=oid)
            except Order.DoesNotExist:
                return Response({"error": "Заказ не найден"}, status=status.HTTP_404_NOT_FOUND)
        try:
            services.container_ship(container=container, order=order, user=request.user,
                comment=request.data.get("comment", ""))
        except ServiceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        data = ContainerDetailSerializer(container).data
        data["can_print_packing_list"] = True
        return Response(data)

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        container = self.get_object()
        wid = request.data.get("warehouse_id")
        if not wid:
            return Response({"error": "warehouse_id обязателен"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            wh = Warehouse.objects.get(pk=wid)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад не найден"}, status=status.HTTP_404_NOT_FOUND)
        try:
            services.container_move(container=container, warehouse_to=wh,
                user=request.user, comment=request.data.get("comment", ""))
        except ServiceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ContainerDetailSerializer(container).data)

    @action(detail=False, methods=["get"], url_path="reserve-stock")
    def reserve_stock(self, request):
        pid = request.query_params.get("product_id")
        if not pid:
            return Response({"error": "product_id обязателен"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            product = Product.objects.get(pk=pid)
        except Product.DoesNotExist:
            return Response({"error": "Артикул не найден"}, status=status.HTTP_404_NOT_FOUND)

        lines = reserve_stock_for_product(product)
        result = [{
            "container_id": line.container_id,
            "container_code": line.container.code,
            "warehouse_name": line.container.warehouse.name if line.container.warehouse else "",
            "quantity": str(line.quantity),
        } for line in lines]
        total = sum((Decimal(r["quantity"]) for r in result), Decimal("0"))
        return Response({
            "product_id": product.id,
            "product_article": product.article,
            "product_name": product.name,
            "uom": product.uom,
            "total": str(total),
            "rows": result,
        })

    @action(detail=True, methods=["get"], url_path="label-pdf")
    def label_pdf(self, request, pk=None):
        container = self.get_object()
        pdf_bytes = generate_container_label_pdf(container)
        # Фиксируем факт печати
        container.label_printed_at = timezone.now()
        container.save(update_fields=["label_printed_at", "updated_at"])
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="label-{container.code}.pdf"'
        return response

    @action(detail=False, methods=["post"], url_path="bulk-transfer")
    def bulk_transfer(self, request):
        """Партийное перемещение тары между двумя складами.

        Body: {
          "codes": ["TARA-000001", ...],
          "from_warehouse_id": N,
          "to_warehouse_id": M,
          "comment": "Приёмка от Завода"
        }
        Проверяет, что все тары сейчас на складе-источнике.
        Возвращает: {"moved": [{code, id}], "not_found": [...],
                     "errors": [{code, error}]}
        """
        codes = request.data.get("codes") or []
        from_id = request.data.get("from_warehouse_id")
        to_id = request.data.get("to_warehouse_id")
        comment = request.data.get("comment", "")

        if not codes:
            return Response({"error": "codes пуст"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not from_id or not to_id:
            return Response(
                {"error": "from_warehouse_id и to_warehouse_id обязательны"},
                status=status.HTTP_400_BAD_REQUEST)
        if int(from_id) == int(to_id):
            return Response({"error": "Склады совпадают"},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            wh_from = Warehouse.objects.get(pk=from_id)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад-источник не найден"},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            wh_to = Warehouse.objects.get(pk=to_id)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад-приёмник не найден"},
                            status=status.HTTP_404_NOT_FOUND)

        moved, not_found, errors = [], [], []
        for raw in codes:
            code = str(raw).strip()
            if not code:
                continue
            c = Container.objects.filter(code=code).first()
            if not c:
                not_found.append(code)
                continue
            if c.warehouse_id != wh_from.id:
                errors.append({
                    "code": code,
                    "error": f"Сейчас не на складе «{wh_from.name}»",
                })
                continue
            try:
                services.container_move(
                    container=c, warehouse_to=wh_to,
                    user=request.user, comment=comment,
                )
                moved.append({"code": code, "id": c.id})
            except ServiceError as exc:
                errors.append({"code": code, "error": str(exc)})

        return Response({
            "moved": moved,
            "not_found": not_found,
            "errors": errors,
            "from_warehouse": {"id": wh_from.id, "name": wh_from.name},
            "to_warehouse": {"id": wh_to.id, "name": wh_to.name},
        })

    @action(detail=False, methods=["post"], url_path="transfer-note-pdf")
    def transfer_note_pdf(self, request):
        """PDF-накладная на перемещение партии.
        Body: {"codes": [...], "from_warehouse_id": N, "to_warehouse_id": M}
        PDF не меняет данные — можно вызывать до или после bulk-transfer.
        """
        codes = request.data.get("codes") or []
        from_id = request.data.get("from_warehouse_id")
        to_id = request.data.get("to_warehouse_id")
        if not codes or not from_id or not to_id:
            return Response(
                {"error": "codes, from_warehouse_id, to_warehouse_id обязательны"},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            wh_from = Warehouse.objects.get(pk=from_id)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад-источник не найден"},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            wh_to = Warehouse.objects.get(pk=to_id)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад-приёмник не найден"},
                            status=status.HTTP_404_NOT_FOUND)

        containers = list(
            Container.objects.filter(code__in=codes)
            .select_related("product", "warehouse")
            .order_by("code")
        )
        if not containers:
            return Response({"error": "Ни одной тары не найдено"},
                            status=status.HTTP_404_NOT_FOUND)

        cs = CompanySettings.objects.first()
        company = {
            "ownership_note": cs.ownership_note if cs else "",
            "packing_list_title": cs.packing_list_title if cs else "",
            "customer_name": cs.customer_name if cs else "",
            "customer_address": cs.customer_address if cs else "",
            "supplier_name": cs.supplier_name if cs else "",
            "supplier_address": cs.supplier_address if cs else "",
        }
        pdf_bytes = generate_transfer_note_pdf(
            containers, wh_from, wh_to, company, user=request.user,
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="transfer-{len(containers)}.pdf"'
        )
        return response

    @action(detail=False, methods=["post"], url_path="bulk-labels-pdf")
    def bulk_labels_pdf(self, request):
        """Печать этикеток пачкой.
        Body: {"ids": [1,2,3]} — конкретные контейнеры, или
              {"filter": "warehouse"} / {"filter": "main"} и т.п.
        Отдаёт один PDF (по странице на тару).
        Проставляет label_printed_at у всех напечатанных.
        """
        ids = request.data.get("ids") or []
        if not ids:
            return Response({"error": "ids обязателен (список ID тар)"},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(ids) > 200:
            return Response({"error": "Максимум 200 тар за раз"},
                            status=status.HTTP_400_BAD_REQUEST)
        containers = list(
            Container.objects.filter(pk__in=ids)
            .select_related("product", "warehouse", "order")
            .prefetch_related("lines__product")
        )
        if not containers:
            return Response({"error": "Ни одной тары не найдено"},
                            status=status.HTTP_404_NOT_FOUND)

        # Сохраняем порядок запроса
        by_id = {c.id: c for c in containers}
        ordered = [by_id[i] for i in ids if i in by_id]

        pdf_bytes = generate_container_labels_pdf(ordered)
        now = timezone.now()
        Container.objects.filter(pk__in=[c.id for c in ordered]).update(
            label_printed_at=now, updated_at=now)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="labels-batch-{len(ordered)}.pdf"'
        )
        return response

    @action(detail=True, methods=["get"], url_path="packing-list-pdf")
    def packing_list_pdf(self, request, pk=None):
        container = self.get_object()
        cs = CompanySettings.objects.first()
        if not cs:
            return Response({"error": "CompanySettings не настроены"},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        art_before = request.query_params.get("art_before")
        if not art_before:
            event = (
                container.events.filter(event_type="returned",
                                        movement__production_run__isnull=False)
                .select_related("movement__production_run__source_product")
                .order_by("-created_at").first()
            )
            if event and event.movement and event.movement.production_run:
                art_before = event.movement.production_run.source_product.article
        company = cs.as_dict_for(request.user)
        pdf_bytes = generate_packing_list_pdf(container, company, art_before_override=art_before)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="packing-{container.code}.pdf"'
        return response

    @action(detail=False, methods=["post"], url_path="multi-create")
    def multi_create(self, request):
        """Создание тары с несколькими артикулами сразу.

        Body: {
          "warehouse_id": N,
          "note": "...",
          "lines": [
            {"product_id": 1, "quantity": "100"},
            {"product_id": 2, "quantity": "50"}
          ]
        }

        Создаёт:
          * Container с auto-кодом
          * ContainerLine для каждой строки
          * Movement (in) для каждой строки
          * ContainerEvent CREATED один на тару
        Возвращает сериализованный контейнер.
        """
        wid = request.data.get("warehouse_id")
        note = request.data.get("note") or ""
        lines = request.data.get("lines") or []

        if not wid:
            return Response({"error": "warehouse_id обязателен"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not lines:
            return Response({"error": "Нужна хотя бы одна строка"},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            wh = Warehouse.objects.get(pk=wid)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад не найден"},
                            status=status.HTTP_404_NOT_FOUND)

        # Литейка может создавать только на складе Завод
        if _user_is_foundry(request.user) and wh.code != "ZLK":
            return Response(
                {"error": "Литейка может создавать тары только на складе Завод"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Валидация строк
        clean_lines = []
        for i, ln in enumerate(lines):
            pid = ln.get("product_id")
            qty_raw = ln.get("quantity")
            if not pid or qty_raw is None:
                return Response(
                    {"error": f"Строка {i+1}: нужны product_id и quantity"},
                    status=status.HTTP_400_BAD_REQUEST)
            try:
                qty = Decimal(str(qty_raw))
            except Exception:
                return Response(
                    {"error": f"Строка {i+1}: некорректное количество"},
                    status=status.HTTP_400_BAD_REQUEST)
            if qty <= 0:
                return Response(
                    {"error": f"Строка {i+1}: количество должно быть > 0"},
                    status=status.HTTP_400_BAD_REQUEST)
            try:
                product = Product.objects.get(pk=pid)
            except Product.DoesNotExist:
                return Response(
                    {"error": f"Строка {i+1}: артикул {pid} не найден"},
                    status=status.HTTP_404_NOT_FOUND)
            clean_lines.append({"product": product, "quantity": qty})

        try:
            with transaction.atomic():
                container = Container.objects.create(
                    status=ContainerStatus.WAREHOUSE,
                    warehouse=wh,
                    note=note,
                )
                for ln in clean_lines:
                    ContainerLine.objects.create(
                        container=container,
                        product=ln["product"],
                        quantity=ln["quantity"],
                    )
                    apply_movement(
                        movement_type=MovementType.IN,
                        product=ln["product"],
                        quantity=ln["quantity"],
                        warehouse_to=wh,
                        container=container,
                        comment=f"Приём тары {container.code}",
                        user=request.user,
                    )
                container.recalculate()
                ContainerEvent.objects.create(
                    container=container,
                    event_type=ContainerEventType.CREATED,
                    product=container.product,
                    quantity=container.quantity,
                    comment=note,
                    created_by=request.user,
                )
        except ServiceError as exc:
            return Response({"error": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(
            ContainerDetailSerializer(container).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="bulk-move")
    def bulk_move(self, request):
        """Пакетное перемещение тары по кодам.
        Body: {"codes": ["TARA-001", "TARA-002"], "warehouse_id": N}
        Возвращает: {"moved": [...], "not_found": [...], "errors": [...]}
        """
        codes = request.data.get("codes") or []
        wid = request.data.get("warehouse_id")
        if not wid:
            return Response({"error": "warehouse_id обязателен"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not codes:
            return Response({"error": "codes пуст"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            wh = Warehouse.objects.get(pk=wid)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад не найден"},
                            status=status.HTTP_404_NOT_FOUND)

        moved, not_found, errors = [], [], []
        for raw in codes:
            code = str(raw).strip()
            if not code:
                continue
            c = Container.objects.filter(code=code).first()
            if not c:
                not_found.append(code)
                continue
            if c.warehouse_id == wh.id:
                errors.append({"code": code, "error": "Уже на этом складе"})
                continue
            try:
                services.container_move(container=c, warehouse_to=wh,
                    user=request.user, comment=request.data.get("comment", ""))
                moved.append({"code": code, "warehouse": wh.name})
            except ServiceError as exc:
                errors.append({"code": code, "error": str(exc)})
        return Response({
            "moved": moved, "not_found": not_found, "errors": errors,
            "warehouse_id": wh.id, "warehouse_name": wh.name,
        })

    @action(detail=False, methods=["get"], url_path="by-code")
    def by_code(self, request):
        code = request.query_params.get("code")
        if not code:
            return Response({"error": "code обязателен"}, status=status.HTTP_400_BAD_REQUEST)
        container = (Container.objects.select_related("product", "warehouse", "order")
                     .filter(code=code).first())
        if not container:
            return Response({"error": "Тара не найдена"}, status=status.HTTP_404_NOT_FOUND)
        return Response(ContainerDetailSerializer(container).data)


class ContainerEventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ContainerEvent.objects.select_related("container", "product", "order").all()
    serializer_class = ContainerEventSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ("created_at",)

    def get_queryset(self):
        qs = super().get_queryset()
        cid = self.request.query_params.get("container")
        if cid:
            qs = qs.filter(container_id=cid)
        return qs


# ============================================================================
# Движения / инвентаризация
# ============================================================================
class MovementViewSet(viewsets.ModelViewSet):
    queryset = Movement.objects.select_related(
        "product", "container", "order", "warehouse_from", "warehouse_to").all()
    serializer_class = MovementSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ("product__article", "product__name", "container__code", "comment")
    ordering_fields = ("created_at",)

    def get_queryset(self):
        qs = super().get_queryset()
        mt = self.request.query_params.get("movement_type")
        if mt:
            qs = qs.filter(movement_type=mt)
        pid = self.request.query_params.get("product")
        if pid:
            qs = qs.filter(product_id=pid)
        return qs

    @action(detail=False, methods=["post"], url_path="manual")
    def manual_movement(self, request):
        """Ручной приход / расход без тары и ШК.

        Body: {
          "direction": "in" | "out",
          "product_id": N,
          "warehouse_id": M,
          "quantity": "10.5",
          "comment": "..."
        }

        Создаёт Movement типа in/out и обновляет Stock.
        Тара не создаётся, container=None.
        """
        direction = (request.data.get("direction") or "").strip().lower()
        pid = request.data.get("product_id")
        wid = request.data.get("warehouse_id")
        qty = request.data.get("quantity")
        comment = (request.data.get("comment") or "").strip()

        if direction not in ("in", "out"):
            return Response({"error": "direction должен быть in или out"},
                            status=status.HTTP_400_BAD_REQUEST)
        if not pid or not wid or qty is None:
            return Response(
                {"error": "product_id, warehouse_id, quantity обязательны"},
                status=status.HTTP_400_BAD_REQUEST)

        try:
            product = Product.objects.get(pk=pid)
        except Product.DoesNotExist:
            return Response({"error": "Артикул не найден"},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            warehouse = Warehouse.objects.get(pk=wid)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад не найден"},
                            status=status.HTTP_404_NOT_FOUND)

        try:
            qty_dec = Decimal(str(qty))
        except Exception:
            return Response({"error": "Некорректное quantity"},
                            status=status.HTTP_400_BAD_REQUEST)
        if qty_dec <= 0:
            return Response({"error": "Количество должно быть > 0"},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            if direction == "in":
                mv = services.apply_movement(
                    movement_type=MovementType.IN,
                    product=product, quantity=qty_dec,
                    warehouse_to=warehouse,
                    comment=comment or "Ручной приход",
                    user=request.user,
                )
            else:
                # Проверяем, что хватает остатка
                stock = Stock.objects.filter(
                    warehouse=warehouse, product=product).first()
                available = stock.quantity if stock else Decimal("0")
                if qty_dec > available:
                    return Response(
                        {"error": f"На складе только {available} — нельзя списать {qty_dec}"},
                        status=status.HTTP_400_BAD_REQUEST)
                mv = services.apply_movement(
                    movement_type=MovementType.OUT,
                    product=product, quantity=qty_dec,
                    warehouse_from=warehouse,
                    comment=comment or "Ручной расход",
                    user=request.user,
                )
        except ServiceError as exc:
            return Response({"error": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(MovementSerializer(mv).data,
                        status=status.HTTP_201_CREATED)


class InventoryViewSet(viewsets.ModelViewSet):
    queryset = (Inventory.objects
                .select_related("warehouse", "created_by")
                .prefetch_related("lines__product").all())
    serializer_class = InventorySerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Создаёт инвентаризацию и сразу заполняет линии из остатков Stock."""
        wid = request.data.get("warehouse")
        comment = request.data.get("comment", "")
        if not wid:
            return Response({"error": "warehouse обязателен"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            wh = Warehouse.objects.get(pk=wid)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад не найден"},
                            status=status.HTTP_404_NOT_FOUND)

        inv = Inventory.objects.create(
            warehouse=wh, status=InventoryStatus.DRAFT,
            comment=comment, created_by=request.user,
        )

        # Все ненулевые остатки на складе
        stocks = (Stock.objects
                  .select_related("product")
                  .filter(warehouse=wh)
                  .order_by("product__article"))
        for s in stocks:
            InventoryLine.objects.create(
                inventory=inv, product=s.product,
                quantity_theory=s.quantity,
                quantity_fact=s.quantity,
            )

        return Response(InventorySerializer(inv).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"],
            url_path=r"lines/(?P<line_id>\d+)/set")
    def set_line(self, request, pk=None, line_id=None):
        """Сохранить фактическое количество по строке (только в draft)."""
        inv = self.get_object()
        if inv.status not in (InventoryStatus.DRAFT,
                              InventoryStatus.IN_PROGRESS):
            return Response({"error": "Инвентаризация уже закрыта"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            line = inv.lines.get(pk=line_id)
        except InventoryLine.DoesNotExist:
            return Response({"error": "Строка не найдена"},
                            status=status.HTTP_404_NOT_FOUND)

        fact = request.data.get("quantity_fact")
        if fact is not None:
            try:
                line.quantity_fact = Decimal(str(fact))
            except Exception:
                return Response({"error": "Некорректное quantity_fact"},
                                status=status.HTTP_400_BAD_REQUEST)
        comment = request.data.get("comment")
        if comment is not None:
            line.comment = str(comment)[:255]
        line.save()
        if inv.status == InventoryStatus.DRAFT:
            inv.status = InventoryStatus.IN_PROGRESS
            inv.save(update_fields=["status"])

        return Response(InventoryLineSerializer(line).data)

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        """Применить: создать adjust-движения и обновить Stock."""
        inv = self.get_object()
        if inv.status == InventoryStatus.COMPLETED:
            return Response({"error": "Уже применена"},
                            status=status.HTTP_400_BAD_REQUEST)
        if inv.status == InventoryStatus.CANCELLED:
            return Response({"error": "Отменена"},
                            status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            adjusted = 0
            for line in inv.lines.select_related("product"):
                diff = (line.quantity_fact or Decimal("0")) - \
                       (line.quantity_theory or Decimal("0"))
                if diff == 0:
                    continue
                # adjust принимает quantity как разницу (может быть отрицательной)
                apply_movement(
                    movement_type=MovementType.ADJUST,
                    product=line.product, quantity=diff,
                    warehouse_to=inv.warehouse,
                    comment=f"Инвентаризация #{inv.pk}",
                    user=request.user,
                )
                adjusted += 1
            inv.status = InventoryStatus.COMPLETED
            inv.completed_at = timezone.now()
            inv.save(update_fields=["status", "completed_at"])

        return Response({
            "status": inv.status,
            "completed_at": inv.completed_at.isoformat(),
            "lines_adjusted": adjusted,
        })

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        inv = self.get_object()
        if inv.status == InventoryStatus.COMPLETED:
            return Response({"error": "Уже применена — нельзя отменить"},
                            status=status.HTTP_400_BAD_REQUEST)
        inv.status = InventoryStatus.CANCELLED
        inv.completed_at = timezone.now()
        inv.save(update_fields=["status", "completed_at"])
        return Response({"status": inv.status})

    @action(detail=True, methods=["post"], url_path="refresh")
    def refresh_lines(self, request, pk=None):
        """Пересчитать «По учёту» из текущего Stock (если что-то поменялось)."""
        inv = self.get_object()
        if inv.status not in (InventoryStatus.DRAFT,
                              InventoryStatus.IN_PROGRESS):
            return Response({"error": "Инвентаризация уже закрыта"},
                            status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            for line in inv.lines.select_related("product"):
                s = Stock.objects.filter(
                    warehouse=inv.warehouse, product=line.product
                ).first()
                line.quantity_theory = s.quantity if s else Decimal("0")
                line.save(update_fields=["quantity_theory"])
        return Response(InventorySerializer(inv).data)


# ============================================================================
# Производство
# ============================================================================
class ProductionRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (ProductionRun.objects.select_related(
        "source_product", "product", "order", "created_by", "operator")
        .prefetch_related("movements", "scrap_entries").all())
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ("source_product__article", "product__article", "comment",
                     "scrap_entries__reason")
    ordering_fields = ("created_at", "qty_good")

    def get_serializer_class(self):
        if self.action == "list":
            return ProductionRunListSerializer
        return ProductionRunDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        pid = self.request.query_params.get("product")
        if pid:
            qs = qs.filter(product_id=pid)
        sid = self.request.query_params.get("source_product")
        if sid:
            qs = qs.filter(source_product_id=sid)
        w = self.request.query_params.get("scrap_written_off")
        if w in ("1", "true"):
            qs = qs.filter(scrap_written_off=True)
        elif w in ("0", "false"):
            qs = qs.filter(scrap_written_off=False)
        return qs


class ProductionRunCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ser = ProductionRunCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        sources = []
        for item in data["source_containers"]:
            cid = item.get("container_id")
            qty = item.get("quantity")
            if cid is None or qty is None:
                return Response({"error": "Каждый источник: container_id и quantity"},
                                status=status.HTTP_400_BAD_REQUEST)
            try:
                c = Container.objects.get(pk=cid)
            except Container.DoesNotExist:
                return Response({"error": f"Тара {cid} не найдена"},
                                status=status.HTTP_404_NOT_FOUND)
            sources.append({"container": c, "quantity": Decimal(str(qty))})

        try:
            result_product = Product.objects.get(pk=data["result_product_id"])
        except Product.DoesNotExist:
            return Response({"error": "Деталь не найдена"}, status=status.HTTP_404_NOT_FOUND)

        try:
            warehouse = Warehouse.objects.get(pk=data["warehouse_id"])
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад не найден"}, status=status.HTTP_404_NOT_FOUND)

        order = None
        if data.get("order_id"):
            try:
                order = Order.objects.get(pk=data["order_id"])
            except Order.DoesNotExist:
                return Response({"error": "Заказ не найден"}, status=status.HTTP_404_NOT_FOUND)

        operator = None
        if data.get("operator_id"):
            try:
                operator = User.objects.get(pk=data["operator_id"])
            except User.DoesNotExist:
                return Response({"error": "Оператор не найден"}, status=status.HTTP_404_NOT_FOUND)

        result_containers = []
        for item in data.get("result_containers") or []:
            cid = item.get("container_id")
            qty = item.get("quantity")
            if qty is None:
                return Response({"error": "Каждый приёмник: quantity обязателен"},
                                status=status.HTTP_400_BAD_REQUEST)
            if cid is None:
                c = Container()
            else:
                try:
                    c = Container.objects.get(pk=cid)
                except Container.DoesNotExist:
                    return Response({"error": f"Тара-приёмник {cid} не найдена"},
                                    status=status.HTTP_404_NOT_FOUND)
            result_containers.append({"container": c, "quantity": Decimal(str(qty))})

        try:
            run, container_ids = services.production_run(
                source_containers=sources,
                result_product=result_product,
                qty_good=data["qty_good"],
                qty_scrap=data.get("qty_scrap") or Decimal("0"),
                scrap_reason=data.get("scrap_reason", ""),
                scrap_entries=data.get("scrap_entries") or [],
                reserve_picks=data.get("reserve_picks") or [],
                operator=operator, result_containers=result_containers,
                warehouse=warehouse, order=order, comment=data.get("comment", ""),
                user=request.user,
            )
        except ServiceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        out = ProductionRunDetailSerializer(run).data
        out["result_container_ids"] = container_ids
        out["can_print_packing_list"] = bool(container_ids)
        return Response(out, status=status.HTTP_201_CREATED)


class FreeStockView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, product_id):
        try:
            product = Product.objects.get(pk=product_id)
        except Product.DoesNotExist:
            return Response({"error": "Артикул не найден"}, status=status.HTTP_404_NOT_FOUND)
        fs = free_stock(product)
        return Response({
            "product_id": product.id, "product_article": product.article,
            "product_name": product.name, "free_stock": str(fs), "uom": product.uom,
        })


# ============================================================================
# Брак
# ============================================================================
class ScrapWriteoffView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        pid = request.data.get("product_id")
        qty = request.data.get("quantity")
        wid = request.data.get("warehouse_id")
        if not pid or qty is None or not wid:
            return Response({"error": "Нужны product_id, quantity, warehouse_id"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            product = Product.objects.get(pk=pid)
        except Product.DoesNotExist:
            return Response({"error": "Артикул не найден"}, status=status.HTTP_404_NOT_FOUND)
        try:
            warehouse = Warehouse.objects.get(pk=wid)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад не найден"}, status=status.HTTP_404_NOT_FOUND)
        try:
            mv = services.scrap_writeoff(product=product, quantity=qty,
                warehouse=warehouse, comment=request.data.get("comment", ""), user=request.user)
        except ServiceError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(MovementSerializer(mv).data, status=status.HTTP_201_CREATED)


class ScrapSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ProductionRun.objects.filter(qty_scrap__gt=0).select_related("source_product")
        date_from, date_to = _parse_period(request)
        if date_from:
            qs = qs.filter(created_at__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__lte=date_to)

        agg = {}
        for run in qs:
            key = run.source_product_id
            if key not in agg:
                agg[key] = {
                    "source_product_id": run.source_product_id,
                    "source_product_article": run.source_product.article,
                    "source_product_name": run.source_product.name,
                    "qty_scrap": Decimal("0"), "runs_count": 0,
                }
            agg[key]["qty_scrap"] += run.qty_scrap
            agg[key]["runs_count"] += 1

        rows = sorted(agg.values(), key=lambda r: r["source_product_article"])
        for r in rows:
            r["qty_scrap"] = str(r["qty_scrap"])
        return Response({"rows": rows})


class ScrapReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (ProductionRun.objects.filter(qty_scrap__gt=0)
              .select_related("source_product", "product", "operator")
              .prefetch_related("scrap_entries").order_by("-created_at"))
        date_from, date_to = _parse_period(request)
        if date_from:
            qs = qs.filter(created_at__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__lte=date_to)
        operator_id = request.query_params.get("operator")
        if operator_id:
            qs = qs.filter(operator_id=operator_id)
        reason = request.query_params.get("reason")
        if reason:
            qs = qs.filter(scrap_entries__reason__icontains=reason).distinct()

        rows = []
        total_scrap = Decimal("0")
        for r in qs:
            op_name = None
            if r.operator:
                full = r.operator.get_full_name().strip()
                op_name = full or r.operator.username
            entries = list(r.scrap_entries.all())
            reasons_list = [
                {"reason": e.reason, "quantity": str(e.quantity)} for e in entries
            ]
            reasons_joined = ", ".join(
                f"{e.reason} — {e.quantity}" for e in entries
            ) if entries else (r.scrap_reason or "")
            rows.append({
                "id": r.id, "created_at": r.created_at.isoformat(),
                "operator": op_name, "operator_id": r.operator_id,
                "source_product_article": r.source_product.article,
                "source_product_name": r.source_product.name,
                "product_article": r.product.article, "product_name": r.product.name,
                "qty_source_used": str(r.qty_source_used), "qty_good": str(r.qty_good),
                "qty_scrap": str(r.qty_scrap),
                "scrap_reason": reasons_joined or r.scrap_reason,
                "scrap_reasons": reasons_list,
                "comment": r.comment, "scrap_written_off": r.scrap_written_off,
            })
            total_scrap += r.qty_scrap

        by_reason, by_operator = {}, {}
        for run in qs:
            entries = list(run.scrap_entries.all())
            if entries:
                for e in entries:
                    by_reason[e.reason] = by_reason.get(e.reason, Decimal("0")) + e.quantity
            else:
                key = run.scrap_reason or "(без причины)"
                by_reason[key] = by_reason.get(key, Decimal("0")) + run.qty_scrap
            op_key = None
            if run.operator:
                full = run.operator.get_full_name().strip()
                op_key = full or run.operator.username
            op_key = op_key or "(не указан)"
            by_operator[op_key] = by_operator.get(op_key, Decimal("0")) + run.qty_scrap

        return Response({
            "date_from": date_from.date().isoformat() if date_from else None,
            "date_to": (date_to - timedelta(days=1)).date().isoformat() if date_to else None,
            "total_scrap": str(total_scrap), "total_runs": len(rows),
            "by_reason": [{"reason": k, "qty": str(v)}
                          for k, v in sorted(by_reason.items(), key=lambda x: -x[1])],
            "by_operator": [{"operator": k, "qty": str(v)}
                            for k, v in sorted(by_operator.items(), key=lambda x: -x[1])],
            "rows": rows,
        })


def _parse_period(request):
    now = timezone.localtime()
    df = request.query_params.get("date_from")
    dt = request.query_params.get("date_to")
    if df:
        date_from = timezone.make_aware(datetime.strptime(df, "%Y-%m-%d"))
    else:
        date_from = timezone.make_aware(datetime(now.year, now.month, 1))
    if dt:
        date_to = timezone.make_aware(datetime.strptime(dt, "%Y-%m-%d")) + timedelta(days=1)
    else:
        date_to = None
    return date_from, date_to


# ============================================================================
# Отчёты
# ============================================================================
class StockReportView(APIView):
    """# STOCK-V2
    Отчёт по остаткам — считается из ContainerLine (реальные тары),
    а не из устаревшей таблицы Stock.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from warehouse.models import ContainerLine

        # Собираем остатки: (warehouse, product) -> sum(quantity)
        from collections import defaultdict
        agg = defaultdict(lambda: Decimal("0"))

        lines = (ContainerLine.objects
                 .filter(quantity__gt=0,
                         container__shipped_at__isnull=True)
                 .select_related("container", "container__warehouse", "product"))
        for l in lines:
            if not l.container.warehouse_id:
                continue
            key = (l.container.warehouse_id, l.product_id)
            agg[key] += l.quantity

        wid = request.query_params.get("warehouse")

        rows = []
        for (warehouse_id, product_id), qty in agg.items():
            if qty <= 0:
                continue
            if wid and str(warehouse_id) != str(wid):
                continue
            try:
                from warehouse.models import Warehouse as _WH
                wh = _WH.objects.get(pk=warehouse_id)
            except _WH.DoesNotExist:
                continue
            try:
                product = Product.objects.get(pk=product_id)
            except Product.DoesNotExist:
                continue

            below_min = (product.min_stock > 0
                         and qty < product.min_stock)
            fs = free_stock(product)

            # in_containers — сколько в тарах (всё, что тут — уже в тарах)
            rows.append({
                "warehouse": wh.name,
                "product_id": product.id,
                "article": product.article,
                "name": product.name,
                "product_type": product.product_type,
                "uom": product.uom,
                "quantity": str(qty),
                "in_containers": str(qty),
                "free_stock": str(fs),
                "min_stock": str(product.min_stock),
                "below_min": below_min,
            })

        rows.sort(key=lambda r: (r["warehouse"], r["article"]))
        return Response({"rows": rows, "count": len(rows)})

class MovementReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Movement.objects.select_related(
            "product", "container", "order", "warehouse_from", "warehouse_to",
            "created_by").all()

        # ── Tab-пресеты ─────────────────────────────────
        tab = (request.query_params.get("tab") or "").strip()
        if tab == "incoming":
            qs = qs.filter(movement_type__in=["in", "produce_in", "adjust"])
        elif tab == "outgoing":
            qs = qs.filter(movement_type__in=["out", "produce_out", "scrap"])
        elif tab == "transfer":
            qs = qs.filter(movement_type="transfer")

        # ── Явный фильтр (одно значение) ────────────────
        mt = request.query_params.get("movement_type")
        if mt:
            qs = qs.filter(movement_type=mt)

        # ── Список типов через запятую ──────────────────
        mts = request.query_params.get("movement_types")
        if mts:
            items = [s.strip() for s in mts.split(",") if s.strip()]
            if items:
                qs = qs.filter(movement_type__in=items)

        # ── По продукту ─────────────────────────────────
        pid = request.query_params.get("product")
        if pid:
            qs = qs.filter(product_id=pid)

        # ── С ШК / без ШК ───────────────────────────────
        has_container = request.query_params.get("has_container")
        if has_container == "1":
            qs = qs.filter(container__isnull=False)
        elif has_container == "0":
            qs = qs.filter(container__isnull=True)

        # ── Поиск ───────────────────────────────────────
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(product__article__icontains=search)
                | Q(product__name__icontains=search)
                | Q(container__code__icontains=search)
                | Q(comment__icontains=search)
            )

        # ── Даты ────────────────────────────────────────
        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        if df:
            try:
                d_from = timezone.make_aware(
                    datetime.strptime(df, "%Y-%m-%d")
                )
                qs = qs.filter(created_at__gte=d_from)
            except ValueError:
                pass
        if dt:
            try:
                d_to = timezone.make_aware(
                    datetime.strptime(dt, "%Y-%m-%d")
                ) + timedelta(days=1)
                qs = qs.filter(created_at__lt=d_to)
            except ValueError:
                pass

        qs = qs.order_by("-created_at")

        incoming = qs.filter(
            movement_type__in=["in", "produce_in", "adjust"]
        ).aggregate(s=Sum("quantity"))["s"] or Decimal("0")
        outgoing = qs.filter(
            movement_type__in=["out", "produce_out", "scrap"]
        ).aggregate(s=Sum("quantity"))["s"] or Decimal("0")

        total = qs.count()
        limit = 5000
        rows = MovementSerializer(qs[:limit], many=True).data
        return Response({
            "rows": rows,
            "count": total,
            "incoming_total": str(incoming),
            "outgoing_total": str(outgoing),
        })


RUS_MONTHS_FULL = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                   "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


class MonthlySummaryView(APIView):
    """# MONTHLY-V3 (performance)
    Сводная по месяцам — на основе OperationLine + ContainerLine.

    Логика:
      * received (приход) — Movement IN за месяц (приход тары на склад)
      * produce_out (в МО) — OperationLine FROM за месяц (что ушло в работу)
      * produced (сделано) — OperationLine TO для деталей за месяц
      * shipped (отгружено) — Movement OUT за месяц
      * scrap — OperationLine SCRAP за месяц
      * balance_end — фактический остаток ContainerLine на конец месяца

    Оптимизация (2026-09-24):
      Раньше balance считался через _balance_at() — 2 SQL на каждый
      (товар × месяц) = ~20 000 запросов, 17 сек.
      Теперь: 2 запроса на init + running, дальше всё в Python.
      Результат — те же цифры, ~10 SQL, время <1 сек.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from collections import defaultdict
        from warehouse.models import OperationLine, OperationDirection, ContainerLine

        try:
            months_back = int(request.query_params.get("months", "6"))
        except ValueError:
            months_back = 6
        months_back = max(1, min(24, months_back))

        now = timezone.localtime()
        current_month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0)
        months = []
        cursor = current_month_start
        for _ in range(months_back):
            months.append(cursor)
            cursor = (cursor - timedelta(days=1)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
        months.reverse()

        # CANONICAL-MONTHLY: маппинг product_id → canonical_id
        canonical_map = {}       # {любой id: id главного}
        all_prods_qs = Product.objects.all().only(
            "id", "alias_of_id", "article", "name",
            "product_type", "uom", "is_active")
        for p in all_prods_qs:
            canonical_map[p.id] = p.alias_of_id or p.id

        # Список главных — используется для вывода
        all_products = list(
            Product.objects.filter(is_active=True, alias_of__isnull=True)
            .order_by("article")
        )

        # ── Bucket прихода/расхода по (year, month, canonical_id) ──────
        bucket = defaultdict(lambda: {
            "received": Decimal("0"),      # Movement IN
            "shipped": Decimal("0"),       # Movement OUT
            "produce_out": Decimal("0"),   # OperationLine FROM
            "produced": Decimal("0"),      # OperationLine TO (для деталей)
            "scrap": Decimal("0"),         # OperationLine SCRAP
        })

        start = months[0]

        # Movements — приход / отгрузка (без изменений)
        for m in (Movement.objects
                  .filter(created_at__gte=start)
                  .select_related("product", "container")):
            cid = canonical_map.get(m.product_id, m.product_id)
            key = (m.created_at.year, m.created_at.month, cid)
            if m.movement_type in ("in", "produce_in", "adjust"):
                if m.container_id is None:
                    continue
                if (m.comment or "").startswith("Ревизия"):
                    continue
                bucket[key]["received"] += m.quantity
            elif m.movement_type == "out":
                bucket[key]["shipped"] += m.quantity

        # OperationLines — операции
        # SEMANTIC-FIX: produced/produce_out считаются ТОЛЬКО для операций,
        # где источник — отливка (casting). Перекладывания детали→деталь
        # (фрезеровка, упаковка, перемещение) больше не считаются как
        # "произведено" и не идут в "ушло в работу".
        from itertools import groupby
        ops_qs = (OperationLine.objects
               .filter(operation__created_at__gte=start)
               .select_related("operation", "product")
               .order_by("operation_id", "id"))
        for _op_id, op_lines_iter in groupby(ops_qs, key=lambda l: l.operation_id):
            op_lines = list(op_lines_iter)
            op_dt = op_lines[0].operation.created_at
            has_casting_from = any(
                l.direction == OperationDirection.FROM and
                l.product.product_type == "casting"
                for l in op_lines
            )
            for l in op_lines:
                cid = canonical_map.get(l.product_id, l.product_id)
                key = (op_dt.year, op_dt.month, cid)
                if l.direction == OperationDirection.FROM:
                    if l.product.product_type == "casting":
                        bucket[key]["produce_out"] += l.qty
                elif l.direction == OperationDirection.TO:
                    if l.product.product_type == "part" and has_casting_from:
                        bucket[key]["produced"] += l.qty
                elif l.direction == OperationDirection.SCRAP:
                    bucket[key]["scrap"] += l.qty

        # ── BALANCE-AT (переписано) ───────────────────────────────────
        # init_balance: сумма всех движений ДО первого месяца, по канонам.
        # Один запрос с GROUP BY.
        init_balance = defaultdict(Decimal)
        init_qs = (Movement.objects
                   .filter(created_at__lt=start,
                           movement_type__in=["in", "produce_in", "adjust",
                                              "out", "produce_out", "scrap"])
                   .values("product_id", "movement_type")
                   .annotate(total=Sum("quantity")))
        for row in init_qs:
            cid = canonical_map.get(row["product_id"], row["product_id"])
            if row["movement_type"] in ("in", "produce_in", "adjust"):
                init_balance[cid] += row["total"]
            else:
                init_balance[cid] -= row["total"]

        # Все движения начиная с первого месяца — только нужные поля.
        # Один запрос. Дальше накапливаем в Python.
        moves_in_range = list(
            Movement.objects
            .filter(created_at__gte=start,
                    movement_type__in=["in", "produce_in", "adjust",
                                       "out", "produce_out", "scrap"])
            .values("created_at", "product_id", "movement_type", "quantity")
            .order_by("created_at")
        )

        moves_by_month = defaultdict(list)
        for row in moves_in_range:
            dt = row["created_at"]
            moves_by_month[(dt.year, dt.month)].append(row)

        # Идём по месяцам: снимок баланса на НАЧАЛО каждого месяца.
        balance_at_start = {}  # {(y, m): {canon_id: Decimal}}
        running = defaultdict(Decimal)
        for cid, v in init_balance.items():
            running[cid] = v

        for month_start in months:
            key = (month_start.year, month_start.month)
            balance_at_start[key] = dict(running)
            for row in moves_by_month.get(key, []):
                cid = canonical_map.get(row["product_id"], row["product_id"])
                if row["movement_type"] in ("in", "produce_in", "adjust"):
                    running[cid] += row["quantity"]
                else:
                    running[cid] -= row["quantity"]

        # ── ContainerLine для текущего месяца: 2 запроса вместо ~1600 ──
        cl_total_by_canon = defaultdict(Decimal)
        for r in (ContainerLine.objects
                  .values("product_id")
                  .annotate(total=Sum("quantity"))):
            cid = canonical_map.get(r["product_id"], r["product_id"])
            cl_total_by_canon[cid] += r["total"]

        cl_shipped_by_canon = defaultdict(Decimal)
        for r in (ContainerLine.objects
                  .filter(container__shipped_at__isnull=False)
                  .values("product_id")
                  .annotate(total=Sum("quantity"))):
            cid = canonical_map.get(r["product_id"], r["product_id"])
            cl_shipped_by_canon[cid] += r["total"]

        # ── Собираем результат ─────────────────────────────────────────
        result_months = []
        for idx, month_start in enumerate(months):
            y, m = month_start.year, month_start.month
            is_current_month = (month_start == months[-1])

            md = {
                "year": y, "month": m,
                "label": f"{RUS_MONTHS_FULL[m]} {y}",
                "by_product": [],
                "totals": {"produced": Decimal("0"), "shipped": Decimal("0"),
                           "scrap": Decimal("0"), "received": Decimal("0"),
                           "produce_out": Decimal("0")},
            }

            start_snap = balance_at_start.get((y, m), {})
            if idx + 1 < len(months):
                nm = months[idx + 1]
                end_snap = balance_at_start.get((nm.year, nm.month), {})
            else:
                end_snap = None  # текущий месяц — ContainerLine

            for p in all_products:
                b = bucket.get((y, m, p.id), {
                    "received": Decimal("0"), "shipped": Decimal("0"),
                    "produce_out": Decimal("0"), "produced": Decimal("0"),
                    "scrap": Decimal("0"),
                })

                bal_start = start_snap.get(p.id, Decimal("0"))

                if is_current_month:
                    bal_end = (cl_total_by_canon.get(p.id, Decimal("0"))
                               - cl_shipped_by_canon.get(p.id, Decimal("0")))
                    if bal_end < 0:
                        bal_end = Decimal("0")
                else:
                    bal_end = (end_snap or {}).get(p.id, Decimal("0"))

                md["by_product"].append({
                    "product_id": p.id, "article": p.article, "name": p.name,
                    "product_type": p.product_type, "uom": p.uom,
                    "balance_start": str(bal_start),
                    "received": str(b["received"]),
                    "produced": str(b["produced"]),
                    "shipped": str(b["shipped"]),
                    "scrap": str(b["scrap"]),
                    "produce_out": str(b["produce_out"]),
                    "prod_scrap": str(b["scrap"]),
                    "balance_end": str(bal_end),
                })

                for k in ("produced", "shipped", "scrap", "received", "produce_out"):
                    md["totals"][k] += b[k]

            # ── Группировка отливка → дети ─────────────────────────────
            by_pid = {r["product_id"]: r for r in md["by_product"]}
            castings = []
            for p in all_products:
                if p.product_type != "casting":
                    continue
                row = by_pid.get(p.id)
                if not row:
                    continue
                child_ids = []
                seen_canon = set()
                for i in (1, 2, 3, 4, 5, 6, 7, 8):
                    raw_cid = getattr(p, f"mo{i}_id", None)
                    if not raw_cid:
                        continue
                    # ALIAS-FIX: mo-поля могут вести на alias,
                    # by_pid содержит только главных → резолвим и дедупим
                    canon_cid = canonical_map.get(raw_cid, raw_cid)
                    if canon_cid in seen_canon:
                        continue
                    seen_canon.add(canon_cid)
                    child_ids.append(canon_cid)
                children = []
                sum_prod = Decimal("0")
                sum_ship = Decimal("0")
                # SCRAP-MERGE: брак отливки + брак детей в одну цифру
                sum_scrap = Decimal(row["scrap"])
                sum_bs = Decimal("0")
                sum_be = Decimal("0")
                for cid in child_ids:
                    c = by_pid.get(cid)
                    if not c:
                        continue
                    children.append({
                        "product_id": c["product_id"],
                        "article": c["article"], "name": c["name"],
                        "balance_start": c["balance_start"],
                        "produced": c["produced"], "shipped": c["shipped"],
                        "scrap": c["scrap"], "balance_end": c["balance_end"],
                    })
                    sum_prod += Decimal(c["produced"])
                    sum_ship += Decimal(c["shipped"])
                    sum_scrap += Decimal(c["scrap"])
                    sum_bs += Decimal(c["balance_start"])
                    sum_be += Decimal(c["balance_end"])
                castings.append({
                    "product_id": p.id, "article": p.article, "name": p.name,
                    "balance_start": row["balance_start"],
                    "received": row["received"],
                    "produce_out": row["produce_out"],
                    "balance_end": row["balance_end"],
                    "parts_balance_start": str(sum_bs),
                    "parts_produced": str(sum_prod),
                    "parts_shipped": str(sum_ship),
                    "parts_scrap": str(sum_scrap),
                    "parts_balance_end": str(sum_be),
                    "children": children,
                })
            md["castings"] = castings

            for k in md["totals"]:
                md["totals"][k] = str(md["totals"][k])
            result_months.append(md)

        return Response({"months": result_months})


class OrderFulfillmentReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Order.objects.prefetch_related("lines__product", "lines__casting").all()
        sf = request.query_params.get("status")
        if sf:
            qs = qs.filter(status=sf)
        result = [{"order_id": o.id, "number": o.number, "status": o.status,
                   "status_display": o.get_status_display(),
                   "lines": order_fulfillment_data(o)} for o in qs]
        return Response({"orders": result})

# ============================================================================
# Версия данных — для polling-обновления между клиентами
# ============================================================================
from hashlib import sha256 as _sha256


class VersionView(APIView):
    """Отдаёт короткую метку, которая меняется при любом изменении данных.
    Клиент опрашивает раз в ~10 сек и при смене метки показывает плашку
    «Данные могли обновиться» с кнопкой «Обновить».
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from orders.models import Order
        from inventory.models import Product

        parts = []

        m = Movement.objects.aggregate(m=Max("id"), t=Max("created_at"))
        parts.append(f"mv:{m['m'] or 0}:{m['t'].isoformat() if m['t'] else ''}")

        c = Container.objects.aggregate(m=Max("id"), t=Max("updated_at"))
        parts.append(f"ct:{c['m'] or 0}:{c['t'].isoformat() if c['t'] else ''}")

        p = ProductionRun.objects.aggregate(m=Max("id"), t=Max("created_at"))
        parts.append(f"pr:{p['m'] or 0}:{p['t'].isoformat() if p['t'] else ''}")

        e = ContainerEvent.objects.aggregate(m=Max("id"), t=Max("created_at"))
        parts.append(f"ce:{e['m'] or 0}:{e['t'].isoformat() if e['t'] else ''}")

        o = Order.objects.aggregate(m=Max("id"), t=Max("updated_at"))
        parts.append(f"or:{o['m'] or 0}:{o['t'].isoformat() if o['t'] else ''}")

        pd = Product.objects.aggregate(m=Max("id"), t=Max("updated_at"))
        parts.append(f"pd:{pd['m'] or 0}:{pd['t'].isoformat() if pd['t'] else ''}")

        raw = "|".join(parts)
        marker = _sha256(raw.encode()).hexdigest()[:16]
        return Response({
            "marker": marker,
            "ts": timezone.now().isoformat(),
        })


# ============================================================================
# Опасная зона — сброс оперативных данных
# ============================================================================
class AdminResetDataView(APIView):
    """Полный сброс оперативных данных. Только для админов.

    Body: {"confirm": "СБРОС", "keep_warehouses": false}
    Требует явного подтверждения словом «СБРОС».
    """
    permission_classes = [IsAuthenticated, IsAdminRole]

    def post(self, request):
        confirm = (request.data.get("confirm") or "").strip().upper()
        if confirm != "СБРОС":
            return Response(
                {"error": "Не подтверждено. Введите слово «СБРОС»."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        keep = bool(request.data.get("keep_warehouses", False))
        deleted = services.reset_operational_data(
            user=request.user, keep_warehouses=keep)
        return Response({"deleted": deleted, "status": "ok"})


# ============================================================================
# Отчёт по производству — по периодам, операторам, отливкам, браку
# ============================================================================
class ProductionSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from collections import defaultdict

        # ── Период ─────────────────────────────────────────
        date_from, date_to = _parse_period(request)
        qs = (ProductionRun.objects
              .filter(created_at__gte=date_from)
              .select_related("operator", "source_product", "product")
              .prefetch_related("scrap_entries"))
        if date_to:
            qs = qs.filter(created_at__lt=date_to)

        operator_id = request.query_params.get("operator")
        if operator_id:
            qs = qs.filter(operator_id=operator_id)

        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(source_product__article__icontains=search)
                | Q(source_product__name__icontains=search)
                | Q(product__article__icontains=search)
                | Q(product__name__icontains=search)
                | Q(operator__username__icontains=search)
                | Q(operator__first_name__icontains=search)
                | Q(operator__last_name__icontains=search)
            )

        qs = qs.order_by("-created_at")

        runs = list(qs)

        # ── Общие итоги ────────────────────────────────────
        total_source = Decimal("0")
        total_good = Decimal("0")
        total_scrap = Decimal("0")

        # По причинам
        by_reason = defaultdict(lambda: Decimal("0"))

        # По операторам: {op_id: {...}}
        by_operator = {}

        # По отливкам: {casting_id: {...}}
        by_casting = {}

        for run in runs:
            src_qty = Decimal(str(run.qty_source_used or 0))
            good_qty = Decimal(str(run.qty_good or 0))
            scrap_qty = Decimal(str(run.qty_scrap or 0))
            total_source += src_qty
            total_good += good_qty
            total_scrap += scrap_qty

            # Оператор
            op_key = run.operator_id or 0
            if op_key not in by_operator:
                full = ""
                if run.operator:
                    full = run.operator.get_full_name().strip() or run.operator.username
                by_operator[op_key] = {
                    "operator_id": run.operator_id,
                    "operator": full or "(не указан)",
                    "runs": 0,
                    "qty_source_used": Decimal("0"),
                    "qty_good": Decimal("0"),
                    "qty_scrap": Decimal("0"),
                    "scrap_by_reason": defaultdict(lambda: Decimal("0")),
                }
            op = by_operator[op_key]
            op["runs"] += 1
            op["qty_source_used"] += src_qty
            op["qty_good"] += good_qty
            op["qty_scrap"] += scrap_qty

            # Отливка
            cast_key = run.source_product_id or 0
            if cast_key not in by_casting:
                by_casting[cast_key] = {
                    "casting_id": run.source_product_id,
                    "casting_article": run.source_product.article if run.source_product else "—",
                    "casting_name": run.source_product.name if run.source_product else "—",
                    "runs": 0,
                    "qty_source_used": Decimal("0"),
                    "qty_good": Decimal("0"),
                    "qty_scrap": Decimal("0"),
                }
            cast = by_casting[cast_key]
            cast["runs"] += 1
            cast["qty_source_used"] += src_qty
            cast["qty_good"] += good_qty
            cast["qty_scrap"] += scrap_qty

            # Причины брака
            entries = list(run.scrap_entries.all())
            if entries:
                for e in entries:
                    q = Decimal(str(e.quantity or 0))
                    by_reason[e.reason] += q
                    op["scrap_by_reason"][e.reason] += q
            elif scrap_qty > 0:
                # legacy без ScrapEntry — в «без причины»
                key = run.scrap_reason or "(без причины)"
                by_reason[key] += scrap_qty
                op["scrap_by_reason"][key] += scrap_qty

        # ── Форматирование ─────────────────────────────────
        def _perc(part, total):
            if not total or total == 0:
                return "0"
            return str((part * Decimal("100") / total).quantize(Decimal("0.01")))

        totals = {
            "runs": len(runs),
            "qty_source_used": str(total_source),
            "qty_good": str(total_good),
            "qty_scrap": str(total_scrap),
            "scrap_percent": _perc(total_scrap, total_source),
        }

        by_reason_rows = [
            {"reason": k, "qty": str(v)}
            for k, v in sorted(by_reason.items(), key=lambda x: -x[1])
        ]

        op_rows = []
        for op in by_operator.values():
            op_rows.append({
                "operator_id": op["operator_id"],
                "operator": op["operator"],
                "runs": op["runs"],
                "qty_source_used": str(op["qty_source_used"]),
                "qty_good": str(op["qty_good"]),
                "qty_scrap": str(op["qty_scrap"]),
                "scrap_percent": _perc(op["qty_scrap"], op["qty_source_used"]),
                "scrap_by_reason": {
                    k: str(v) for k, v in sorted(
                        op["scrap_by_reason"].items(), key=lambda x: -x[1])
                },
            })
        op_rows.sort(key=lambda r: -float(r["qty_source_used"]))

        cast_rows = []
        for c in by_casting.values():
            cast_rows.append({
                "casting_id": c["casting_id"],
                "casting_article": c["casting_article"],
                "casting_name": c["casting_name"],
                "runs": c["runs"],
                "qty_source_used": str(c["qty_source_used"]),
                "qty_good": str(c["qty_good"]),
                "qty_scrap": str(c["qty_scrap"]),
                "yield_percent": _perc(c["qty_good"], c["qty_source_used"]),
                "scrap_percent": _perc(c["qty_scrap"], c["qty_source_used"]),
            })
        cast_rows.sort(key=lambda r: -float(r["qty_source_used"]))

        # Детализация операций (до 1000 строк)
        detail_rows = []
        for run in runs[:1000]:
            op_name = ""
            if run.operator:
                op_name = run.operator.get_full_name().strip() or run.operator.username
            reasons = "; ".join(
                f"{e.reason} — {e.quantity}" for e in run.scrap_entries.all()
            ) or run.scrap_reason
            detail_rows.append({
                "id": run.id,
                "created_at": run.created_at.isoformat(),
                "operator": op_name or "(не указан)",
                "source_product_article": run.source_product.article if run.source_product else "",
                "source_product_name": run.source_product.name if run.source_product else "",
                "product_article": run.product.article if run.product else "",
                "product_name": run.product.name if run.product else "",
                "qty_source_used": str(run.qty_source_used),
                "qty_good": str(run.qty_good),
                "qty_scrap": str(run.qty_scrap),
                "scrap_reasons": reasons,
                "comment": run.comment,
            })

        return Response({
            "date_from": date_from.date().isoformat(),
            "date_to": (date_to - timedelta(days=1)).date().isoformat() if date_to else None,
            "totals": totals,
            "by_reason": by_reason_rows,
            "by_operator": op_rows,
            "by_casting": cast_rows,
            "rows": detail_rows,
        })

