"""
Наполнение существующей тары-болванки (ревизия).

POST /api/containers/<int:pk>/fill/
Body: {
  "lines": [{"product_id": 144, "quantity": "93"}, ...],
  "warehouse_id": 1,      // опционально
  "packed": true,         // опционально — сразу пометить упакованной
  "note": "..."
}
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import Product

from .models import (
    Container, ContainerEvent, ContainerEventType, ContainerLine,
    ContainerStatus, MovementType, Warehouse,
)
from .serializers import ContainerDetailSerializer
from .services import ServiceError, apply_movement


@transaction.atomic
def _fill_container(*, container, lines_data, warehouse=None,
                    packed=False, note="", user=None):
    if container.lines.exists():
        raise ServiceError(
            f"Тара {container.code} не пустая — в ней уже что-то лежит. "
            f"Для ревизии нужна пустая тара."
        )

    if not lines_data:
        raise ServiceError("Не указано ни одного артикула")

    parsed = []
    for i, l in enumerate(lines_data, 1):
        pid = l.get("product_id")
        qty_raw = l.get("quantity")
        if pid is None:
            raise ServiceError(f"Строка #{i}: не указан артикул")
        try:
            qty = Decimal(str(qty_raw))
        except (InvalidOperation, ValueError, TypeError):
            raise ServiceError(f"Строка #{i}: некорректное количество")
        if qty <= 0:
            raise ServiceError(f"Строка #{i}: количество должно быть > 0")

        try:
            product = Product.objects.get(pk=pid)
        except Product.DoesNotExist:
            raise ServiceError(f"Строка #{i}: артикул id={pid} не найден")

        parsed.append((product, qty))

    target_warehouse = warehouse if warehouse is not None else container.warehouse
    if target_warehouse is None:
        raise ServiceError("Не указан склад")

    for product, qty in parsed:
        ContainerLine.objects.create(
            container=container, product=product, quantity=qty,
        )
        apply_movement(
            movement_type=MovementType.IN,
            product=product, quantity=qty,
            warehouse_to=target_warehouse,
            container=container,
            comment=f"Ревизия: заполнение {container.code}",
            user=user,
        )

    container.status = ContainerStatus.WAREHOUSE
    container.warehouse = target_warehouse
    if note:
        container.note = note
    # Упакована — только если указано и склад MAIN
    if packed and target_warehouse.code == "MAIN":
        container.packed_at = timezone.now()
    else:
        container.packed_at = None
    container.save(update_fields=[
        "status", "warehouse", "note", "packed_at", "updated_at",
    ])
    container.recalculate()

    ContainerEvent.objects.create(
        container=container,
        event_type=ContainerEventType.CREATED,
        product=container.product,
        quantity=container.quantity,
        comment=note or "Ревизия: заполнение тары",
        created_by=user,
    )
    return container


class ContainerFillView(APIView):
    """POST /api/containers/<int:pk>/fill/ — наполнить пустую тару."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            container = Container.objects.get(pk=pk)
        except Container.DoesNotExist:
            return Response({"error": "Тара не найдена"},
                            status=status.HTTP_404_NOT_FOUND)

        lines_data = request.data.get("lines") or []
        note = (request.data.get("note") or "").strip()
        packed = bool(request.data.get("packed", False))

        warehouse = None
        wh_id = request.data.get("warehouse_id")
        if wh_id is not None:
            try:
                warehouse = Warehouse.objects.get(pk=wh_id)
            except Warehouse.DoesNotExist:
                return Response({"error": f"Склад id={wh_id} не найден"},
                                status=status.HTTP_404_NOT_FOUND)

        try:
            _fill_container(
                container=container,
                lines_data=lines_data,
                warehouse=warehouse,
                packed=packed,
                note=note,
                user=request.user,
            )
        except ServiceError as exc:
            return Response({"error": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(ContainerDetailSerializer(container).data,
                        status=status.HTTP_200_OK)
