"""
Редактирование содержимого тары: изменение количества строки,
добавление новой строки, перекладывание между тарами.
"""
from decimal import Decimal

from django.db import transaction

from inventory.models import Product

from .models import (
    Container,
    ContainerEvent,
    ContainerEventType,
    ContainerLine,
    ContainerStatus,
    MovementType,
)
from .services import ServiceError, apply_movement


def _ensure_editable(container):
    if container.status not in (ContainerStatus.WAREHOUSE, ContainerStatus.EMPTY):
        raise ServiceError(
            f"Тару нельзя править — статус «{container.get_status_display()}»"
        )
    if not container.warehouse_id:
        raise ServiceError("У тары не указан склад")


@transaction.atomic
def set_line_quantity(*, container, line, quantity, user=None, comment=""):
    """Устанавливает НОВОЕ количество для строки. 0 — удаляет строку."""
    _ensure_editable(container)
    if line.container_id != container.id:
        raise ServiceError("Строка не принадлежит этой таре")

    quantity = Decimal(str(quantity))
    if quantity < 0:
        raise ServiceError("Количество не может быть отрицательным")

    old_qty = Decimal(str(line.quantity))
    if quantity == old_qty:
        return container

    diff = quantity - old_qty
    product = line.product

    if diff > 0:
        apply_movement(
            movement_type=MovementType.IN,
            product=product,
            quantity=diff,
            warehouse_to=container.warehouse,
            container=container,
            comment=comment or f"Пополнение тары {container.code}",
            user=user,
        )
    elif diff < 0:
        apply_movement(
            movement_type=MovementType.OUT,
            product=product,
            quantity=-diff,
            warehouse_from=container.warehouse,
            container=container,
            comment=comment or f"Изъятие из тары {container.code}",
            user=user,
        )

    if quantity == 0:
        line.delete()
    else:
        line.quantity = quantity
        line.save(update_fields=["quantity", "updated_at"])

    container.recalculate()
    if not container.lines.exists() and container.status == ContainerStatus.WAREHOUSE:
        container.status = ContainerStatus.EMPTY
        container.save(update_fields=["status", "updated_at"])

    ContainerEvent.objects.create(
        container=container,
        event_type=ContainerEventType.ADJUSTED,
        product=product,
        quantity=quantity,
        comment=comment or f"{product.article}: {old_qty} → {quantity}",
        created_by=user,
    )
    return container


@transaction.atomic
def add_line(*, container, product, quantity, user=None, comment=""):
    """Добавляет в тару артикул. Если строка уже есть — увеличивает."""
    _ensure_editable(container)

    quantity = Decimal(str(quantity))
    if quantity <= 0:
        raise ServiceError("Количество должно быть положительным")

    line = ContainerLine.objects.filter(container=container, product=product).first()
    if line:
        line.quantity = Decimal(str(line.quantity)) + quantity
        line.save(update_fields=["quantity", "updated_at"])
    else:
        ContainerLine.objects.create(
            container=container, product=product, quantity=quantity,
        )

    apply_movement(
        movement_type=MovementType.IN,
        product=product,
        quantity=quantity,
        warehouse_to=container.warehouse,
        container=container,
        comment=comment or f"Пополнение тары {container.code}",
        user=user,
    )

    if container.status == ContainerStatus.EMPTY:
        container.status = ContainerStatus.WAREHOUSE
        container.save(update_fields=["status", "updated_at"])

    container.recalculate()
    ContainerEvent.objects.create(
        container=container,
        event_type=ContainerEventType.ADJUSTED,
        product=product,
        quantity=quantity,
        comment=comment or f"Добавлено: {product.article} +{quantity}",
        created_by=user,
    )
    return container


@transaction.atomic
def transfer_between_containers(*, container_from, container_to, lines,
                                 user=None, comment=""):
    """
    Перекладывает артикулы из одной тары в другую.
    lines = [{"product_id": N, "quantity": "X"}, ...]
    Обе тары должны быть на складе (или пустые).
    Движения идут по складам тар.
    """
    if container_from.id == container_to.id:
        raise ServiceError("Нельзя переложить в ту же тару")

    for c in (container_from, container_to):
        if c.status not in (ContainerStatus.WAREHOUSE, ContainerStatus.EMPTY):
            raise ServiceError(
                f"Тара {c.code}: статус «{c.get_status_display()}» — нельзя"
            )
        if not c.warehouse_id:
            raise ServiceError(f"Тара {c.code}: не указан склад")

    moved = []
    for ln in lines or []:
        pid = ln.get("product_id")
        qty = Decimal(str(ln.get("quantity", "0")))
        if qty <= 0:
            continue
        try:
            product = Product.objects.get(pk=pid)
        except Product.DoesNotExist:
            raise ServiceError(f"Артикул #{pid} не найден")

        src_line = ContainerLine.objects.filter(
            container=container_from, product=product
        ).first()
        if not src_line or src_line.quantity < qty:
            have = src_line.quantity if src_line else Decimal("0")
            raise ServiceError(
                f"В таре {container_from.code} только {have} шт "
                f"{product.article}, запрошено {qty}"
            )

        if container_from.warehouse_id == container_to.warehouse_id:
            apply_movement(
                movement_type=MovementType.TRANSFER,
                product=product, quantity=qty,
                warehouse_from=container_from.warehouse,
                warehouse_to=container_to.warehouse,
                container=None,
                comment=comment or (
                    f"Переложено {container_from.code} → {container_to.code}"
                ),
                user=user,
            )
        else:
            apply_movement(
                movement_type=MovementType.OUT,
                product=product, quantity=qty,
                warehouse_from=container_from.warehouse,
                container=None,
                comment=comment or f"Переложено из {container_from.code}",
                user=user,
            )
            apply_movement(
                movement_type=MovementType.IN,
                product=product, quantity=qty,
                warehouse_to=container_to.warehouse,
                container=None,
                comment=comment or f"Переложено в {container_to.code}",
                user=user,
            )

        src_line.quantity = src_line.quantity - qty
        if src_line.quantity <= 0:
            src_line.delete()
        else:
            src_line.save(update_fields=["quantity", "updated_at"])

        dst_line = ContainerLine.objects.filter(
            container=container_to, product=product
        ).first()
        if dst_line:
            dst_line.quantity = dst_line.quantity + qty
            dst_line.save(update_fields=["quantity", "updated_at"])
        else:
            ContainerLine.objects.create(
                container=container_to, product=product, quantity=qty,
            )

        moved.append({
            "product_id": product.id,
            "article": product.article,
            "quantity": str(qty),
        })

    if container_to.status == ContainerStatus.EMPTY and container_to.lines.exists():
        container_to.status = ContainerStatus.WAREHOUSE
        container_to.save(update_fields=["status", "updated_at"])

    if not container_from.lines.exists():
        container_from.status = ContainerStatus.EMPTY
        container_from.save(update_fields=["status", "updated_at"])

    container_from.recalculate()
    container_to.recalculate()

    ContainerEvent.objects.create(
        container=container_from,
        event_type=ContainerEventType.ADJUSTED,
        comment=comment or f"Переложено в {container_to.code}",
        created_by=user,
    )
    ContainerEvent.objects.create(
        container=container_to,
        event_type=ContainerEventType.ADJUSTED,
        comment=comment or f"Получено из {container_from.code}",
        created_by=user,
    )
    return moved
