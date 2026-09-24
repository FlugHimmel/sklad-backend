import re
from datetime import date, datetime

from django.db import transaction
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from inventory.models import Product
from .parser import parse_paste, STATUS_MAP

from .models import Order, OrderLine, OrderStatus, OrderStatusHistory
from .serializers import (
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderLineSerializer,
    OrderListSerializer,
)
from .services import (
    _line_dict as order_fulfillment_data_single,
    flat_lines_queryset,
    order_fulfillment_data,
)


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.prefetch_related(
        "lines__product", "lines__casting", "status_history"
    ).all()
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ("number", "customer")
    ordering_fields = ("created_at", "due_date", "number")

    def get_serializer_class(self):
        if self.action == "list":
            return OrderListSerializer
        return OrderDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        st = self.request.query_params.get("status")
        if st:
            qs = qs.filter(status=st)
        kind = self.request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        return qs

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        ser = OrderCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        if Order.objects.filter(number=data["number"]).exists():
            return Response({"error": f"Заказ с номером {data['number']} уже существует"},
                            status=status.HTTP_400_BAD_REQUEST)

        order = Order.objects.create(
            number=data["number"],
            kind=data.get("kind") or "production",
            customer=data.get("customer") or "",
            status=data.get("status") or "new",
            due_date=data.get("due_date"),
            comment=data.get("comment") or "",
            created_by=request.user,
        )

        for line in data.get("lines") or []:
            OrderLine.objects.create(
                order=order,
                product=line["product"],
                casting=line.get("casting"),
                name=line.get("name") or line["product"].name,
                quantity_planned=line["quantity_planned"],
                comment=line.get("comment") or "",
            )

        out = OrderDetailSerializer(order).data
        return Response(out, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        order = self.get_object()
        order.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"], url_path="lines")
    def lines(self, request):
        """Плоский список всех строк всех заказов — для сводной таблицы.

        Query params: status, search, date_from (YYYY-MM-DD), date_to,
                      order (ID заказа). Лимит 2000 строк.
        """
        qs = flat_lines_queryset(
            status=request.query_params.get("status"),
            search=request.query_params.get("search"),
            date_from=request.query_params.get("date_from"),
            date_to=request.query_params.get("date_to"),
            order_id=request.query_params.get("order"),
        )

        total = qs.count()
        qs = qs[:2000]

        cache = {}
        rows = []
        for line in qs:
            d = order_fulfillment_data_single(line, casting_cache=cache)
            d.update({
                "order_id": line.order_id,
                "order_number": line.order.number,
                "order_status": line.order.status,
                "order_status_display": line.order.get_status_display(),
                "order_due_date": (
                    line.order.due_date.isoformat()
                    if line.order.due_date else None
                ),
                "order_customer": line.order.customer,
                "sequence": line.sequence,
                "machine": line.machine,
                "ready_date": (
                    line.ready_date.isoformat() if line.ready_date else None
                ),
                "shipped_date": (
                    line.shipped_date.isoformat()
                    if line.shipped_date else None
                ),
                "places": line.places,
                "weight_g": (
                    str(line.weight_g) if line.weight_g is not None else None
                ),
                "name": line.name,
            })
            rows.append(d)

        # Приведение Decimal к str — DRF умеет, но так чище
        for r in rows:
            for k, v in list(r.items()):
                if hasattr(v, "quantize"):
                    r[k] = str(v)

        return Response({"rows": rows, "count": total, "shown": len(rows)})

    @action(detail=True, methods=["get"])
    def fulfillment(self, request, pk=None):
        order = self.get_object()
        return Response({
            "order_id": order.id,
            "number": order.number,
            "status": order.status,
            "lines": order_fulfillment_data(order),
        })

    @action(detail=False, methods=["post"], url_path="import-paste")
    def import_paste(self, request):
        """Импорт заказов из вставленного текста (копипаста из Excel).
        Body: {
          "text": "<сырой текст>",
          "mode": "skip_existing" | "append_lines",   # по умолчанию skip
          "kind": "production"  # по умолчанию
        }
        Возвращает отчёт: что создано, что пропущено, что не распознано.
        """
        text = request.data.get("text") or ""
        mode = request.data.get("mode") or "skip_existing"
        default_kind = request.data.get("kind") or "production"

        if not text.strip():
            return Response({"error": "Пустой текст"},
                            status=status.HTTP_400_BAD_REQUEST)

        # ── Парсим текст ────────────────────────────────────
        try:
            parsed = parse_paste(text)
        except Exception as e:
            return Response({"error": f"Ошибка разбора: {e}"},
                            status=status.HTTP_400_BAD_REQUEST)

        if not parsed["orders"]:
            return Response({
                "error": "Ни одного заказа не распознано. "
                         "Проверьте, что в шапке есть столбцы «Заказ», «Литье», «MO», «к-во»",
                "header": parsed.get("header"),
                "skipped_lines": parsed.get("skipped_lines", []),
            }, status=status.HTTP_400_BAD_REQUEST)

        # ── Создаём ─────────────────────────────────────────
        created_orders = []
        skipped_orders = []
        errors = []
        created_drafts = []
        total_lines = 0

        with transaction.atomic():
            for od in parsed["orders"]:
                number = od["number"].strip()
                if not number:
                    errors.append({"order": "(без номера)",
                                   "error": "Пустой номер заказа"})
                    continue

                # Номер ищем как есть, плюс по нормализованному
                existing = Order.objects.filter(number=number).first()
                if existing is None:
                    norm = re.sub(r"\s+", " ", number).strip().lower()
                    for cand in Order.objects.all():
                        if re.sub(r"\s+", " ", cand.number).strip().lower() == norm:
                            existing = cand
                            break

                order_obj = existing
                if existing is not None:
                    if mode == "skip_existing":
                        skipped_orders.append({"number": number,
                                               "reason": "Заказ уже существует"})
                        continue
                    elif mode == "append_lines":
                        # добавляем строки к существующему
                        pass
                    else:
                        skipped_orders.append({"number": number,
                                               "reason": f"Неизвестный режим {mode}"})
                        continue
                else:
                    order_obj = Order.objects.create(
                        number=number,
                        kind=od.get("kind") or default_kind,
                        customer=od.get("customer") or "",
                        status=od.get("status") or "new",
                        due_date=od.get("due_date"),
                        comment=od.get("comment") or "",
                        created_by=request.user,
                    )
                    created_orders.append(number)

                # Строки
                seq = 0
                for line in od["lines"]:
                    seq += 1
                    art_det = (line.get("product_article") or "").strip()
                    product = _find_product(art_det)
                    if not product and art_det:
                        # Вариант А: автосоздание черновика
                        product = Product.objects.create(
                            article=art_det,
                            name=(line.get("name") or art_det)[:255],
                            product_type="part",
                            is_active=False,
                        )
                        created_drafts.append(art_det)
                    if not product:
                        errors.append({
                            "order": number,
                            "line": f"п/п {line.get('sequence') or seq}",
                            "error": f"Не указан артикул детали",
                        })
                        continue
                    art_cast = (line.get("casting_article") or "").strip()
                    casting = _find_product(art_cast)
                    if not casting and art_cast:
                        casting = Product.objects.create(
                            article=art_cast,
                            name=f"Отливка {art_cast}",
                            product_type="casting",
                            is_active=False,
                        )
                        created_drafts.append(art_cast)

                    OrderLine.objects.create(
                        order=order_obj,
                        product=product,
                        casting=casting,
                        name=line.get("name") or product.name,
                        quantity_planned=line.get("quantity_planned") or 0,
                        sequence=line.get("sequence") or seq,
                        machine=line.get("machine") or "",
                        ready_date=line.get("ready_date"),
                        shipped_date=line.get("shipped_date"),
                        reserve_qty=line.get("reserve_qty") or 0,
                        places=line.get("places"),
                        weight_g=line.get("weight_g"),
                        comment="",
                    )
                    total_lines += 1

        return Response({
            "created_orders": created_orders,
            "skipped_orders": skipped_orders,
            "errors": errors,
            "created_drafts": list(dict.fromkeys(created_drafts)),  # без дублей
            "summary": {
                "orders_created": len(created_orders),
                "orders_skipped": len(skipped_orders),
                "lines_created": total_lines,
                "errors": len(errors),
                "drafts_created": len(set(created_drafts)),
            },
            "header": parsed.get("header"),
        })

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        order = self.get_object()
        new_status = request.data.get("status")
        if new_status not in dict(OrderStatus.choices):
            return Response({"error": "invalid status"},
                            status=status.HTTP_400_BAD_REQUEST)
        old_status = order.status
        if old_status == new_status:
            return Response({"status": new_status, "changed": False})
        order.status = new_status
        order.save(update_fields=["status", "updated_at"])
        OrderStatusHistory.objects.create(
            order=order, status_from=old_status, status_to=new_status,
            changed_by=request.user, comment=request.data.get("comment", ""),
        )
        return Response({"status": new_status, "changed": True})

    @action(detail=True, methods=["post"], url_path="add-line")
    def add_line(self, request, pk=None):
        order = self.get_object()
        ser = OrderLineSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        line = ser.save(order=order)
        if not line.name:
            line.name = line.product.name
            line.save(update_fields=["name"])
        return Response(OrderLineSerializer(line).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path=r"lines/(?P<line_id>\d+)/update")
    def update_line(self, request, pk=None, line_id=None):
        order = self.get_object()
        try:
            line = order.lines.get(pk=line_id)
        except OrderLine.DoesNotExist:
            return Response({"error": "Строка не найдена"}, status=status.HTTP_404_NOT_FOUND)
        qty = request.data.get("quantity_planned")
        if qty is not None:
            line.quantity_planned = qty
        name = request.data.get("name")
        if name is not None:
            line.name = name
        prod_id = request.data.get("product_id")
        if prod_id:
            try:
                line.product = Product.objects.get(pk=prod_id)
            except Product.DoesNotExist:
                return Response({"error": "Артикул не найден"}, status=status.HTTP_404_NOT_FOUND)
        comment = request.data.get("comment")
        if comment is not None:
            line.comment = comment
        line.save()
        return Response(OrderLineSerializer(line).data)

    @action(detail=True, methods=["delete"], url_path=r"lines/(?P<line_id>\d+)/delete")
    def delete_line(self, request, pk=None, line_id=None):
        order = self.get_object()
        try:
            line = order.lines.get(pk=line_id)
        except OrderLine.DoesNotExist:
            return Response({"error": "Строка не найдена"}, status=status.HTTP_404_NOT_FOUND)
        line.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

def _find_product(article):
    """Ищет Product по артикулу (без учёта регистра и пробелов).
    При составном «2472131/6065032» — берём первый."""
    if not article:
        return None
    art = str(article).strip()
    if not art or art == "—":
        return None
    # Составной артикул — берём первый
    if "/" in art:
        art = art.split("/")[0].strip()
    p = Product.objects.filter(article__iexact=art).first()
    if p:
        return p
    # Нормализация: убираем пробелы
    norm = art.replace(" ", "")
    for cand in Product.objects.all():
        if cand.article.replace(" ", "").lower() == norm.lower():
            return cand
    return None

