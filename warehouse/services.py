from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from inventory.models import Product
from orders.models import OrderLine

from .models import (
    Container, ContainerEvent, ContainerEventType, ContainerLine,
    ContainerStatus, Movement, MovementType, Stock, Warehouse,
)

RESERVE_CODE = "RESERVE"


class ServiceError(Exception):
    pass


def _get_stock(warehouse, product):
    obj, _ = Stock.objects.get_or_create(warehouse=warehouse, product=product)
    return obj


def get_reserve_warehouse():
    return Warehouse.objects.filter(code=RESERVE_CODE).first()


def free_stock(product) -> Decimal:
    """Задел = сумма всех ContainerLine, где product=X и тара на RESERVE."""
    wh = get_reserve_warehouse()
    if not wh:
        return Decimal("0")
    agg = (
        ContainerLine.objects.filter(
            product=product,
            container__warehouse=wh,
        )
        .aggregate(s=Sum("quantity"))
    )
    return agg["s"] or Decimal("0")


def reserve_stock_for_product(product):
    wh = get_reserve_warehouse()
    if not wh:
        return ContainerLine.objects.none()
    return (
        ContainerLine.objects.filter(
            product=product,
            container__warehouse=wh,
            quantity__gt=0,
        )
        .select_related("container", "container__warehouse")
        .order_by("container__code")
    )


@transaction.atomic
def apply_movement(*, movement_type, product, quantity, warehouse_from=None,
                   warehouse_to=None, container=None, order=None,
                   comment="", user=None):
    quantity = Decimal(str(quantity))
    if movement_type != MovementType.ADJUST and quantity <= 0:
        raise ServiceError("Количество должно быть положительным")

    mv = Movement.objects.create(
        movement_type=movement_type, product=product, quantity=quantity,
        warehouse_from=warehouse_from, warehouse_to=warehouse_to,
        container=container, order=order,
        comment=comment, created_by=user,
    )

    if movement_type in (MovementType.IN, MovementType.PRODUCE_IN):
        if not warehouse_to:
            raise ServiceError("Не указан склад-приёмник")
        s = _get_stock(warehouse_to, product)
        s.quantity = s.quantity + quantity
        s.save(update_fields=["quantity", "updated_at"])
    elif movement_type in (MovementType.OUT, MovementType.PRODUCE_OUT, MovementType.SCRAP):
        if not warehouse_from:
            raise ServiceError("Не указан склад-источник")
        s = _get_stock(warehouse_from, product)
        s.quantity = s.quantity - quantity
        s.save(update_fields=["quantity", "updated_at"])
    elif movement_type == MovementType.TRANSFER:
        if not warehouse_from or not warehouse_to:
            raise ServiceError("Для перемещения нужны оба склада")
        sfrom = _get_stock(warehouse_from, product)
        sfrom.quantity = sfrom.quantity - quantity
        sfrom.save(update_fields=["quantity", "updated_at"])
        sto = _get_stock(warehouse_to, product)
        sto.quantity = sto.quantity + quantity
        sto.save(update_fields=["quantity", "updated_at"])
    elif movement_type == MovementType.ADJUST:
        if not warehouse_to:
            raise ServiceError("Для корректировки нужен склад")
        s = _get_stock(warehouse_to, product)
        s.quantity = s.quantity + quantity
        s.save(update_fields=["quantity", "updated_at"])
    else:
        raise ServiceError(f"Неизвестный тип движения: {movement_type}")

    return mv


@transaction.atomic
def container_create(*, product, quantity, warehouse, note="", user=None):
    quantity = Decimal(str(quantity))
    if quantity <= 0:
        raise ServiceError("Количество должно быть положительным")

    container = Container.objects.create(
        product=product, quantity=quantity, status=ContainerStatus.WAREHOUSE,
        warehouse=warehouse, note=note,
    )
    ContainerLine.objects.create(container=container, product=product, quantity=quantity)

    apply_movement(
        movement_type=MovementType.IN, product=product, quantity=quantity,
        warehouse_to=warehouse, container=container,
        comment=f"Приём тары {container.code}", user=user,
    )
    ContainerEvent.objects.create(
        container=container, event_type=ContainerEventType.CREATED,
        product=product, quantity=quantity, comment=note, created_by=user,
    )
    return container


@transaction.atomic
def container_issue(*, container, user=None, comment=""):
    if container.status != ContainerStatus.WAREHOUSE:
        raise ServiceError(f"Тара {container.code} не на складе")
    if not container.lines.exists():
        raise ServiceError("Тара пустая")
    if not container.warehouse_id:
        raise ServiceError("В таре не указан склад")

    total = sum((l.quantity for l in container.lines.all()), Decimal("0"))
    main_product = container.lines.first().product

    mv = apply_movement(
        movement_type=MovementType.PRODUCE_OUT, product=main_product,
        quantity=total, warehouse_from=container.warehouse,
        container=container,
        comment=comment or f"Выдача в производство {container.code}",
        user=user,
    )
    container.status = ContainerStatus.IN_PRODUCTION
    container.save(update_fields=["status", "updated_at"])
    ContainerEvent.objects.create(
        container=container, event_type=ContainerEventType.ISSUED,
        product=main_product, quantity=total, movement=mv,
        comment=comment, created_by=user,
    )
    return container


@transaction.atomic
def container_return(*, container, result_product, result_quantity, user=None, comment=""):
    if container.status != ContainerStatus.IN_PRODUCTION:
        raise ServiceError(f"Тара {container.code} не в производстве")
    if not container.warehouse_id:
        raise ServiceError("В таре не указан склад")

    result_quantity = Decimal(str(result_quantity))
    if result_quantity <= 0:
        raise ServiceError("Количество должно быть положительным")

    mv = apply_movement(
        movement_type=MovementType.PRODUCE_IN, product=result_product,
        quantity=result_quantity, warehouse_to=container.warehouse,
        container=container,
        comment=comment or f"Возврат из производства {container.code}", user=user,
    )
    container.lines.all().delete()
    ContainerLine.objects.create(container=container, product=result_product,
                                 quantity=result_quantity)
    container.product = result_product
    container.quantity = result_quantity
    container.status = ContainerStatus.WAREHOUSE
    container.save(update_fields=["product", "quantity", "status", "updated_at"])

    ContainerEvent.objects.create(
        container=container, event_type=ContainerEventType.RETURNED,
        product=result_product, quantity=result_quantity,
        movement=mv, comment=comment, created_by=user,
    )
    return container


@transaction.atomic
def container_ship(*, container, order=None, user=None, comment=""):
    if container.status != ContainerStatus.WAREHOUSE:
        raise ServiceError(f"Тара {container.code} не на складе")
    if not container.lines.exists():
        raise ServiceError("Тара пустая")
    if not container.warehouse_id:
        raise ServiceError("В таре не указан склад")

    for line in container.lines.all():
        apply_movement(
            movement_type=MovementType.OUT, product=line.product,
            quantity=line.quantity, warehouse_from=container.warehouse,
            container=container, order=order,
            comment=comment or f"Отгрузка {container.code}", user=user,
        )
        if order:
            ol = OrderLine.objects.filter(order=order, product=line.product).first()
            if ol:
                ol.quantity_shipped = (ol.quantity_shipped or Decimal("0")) + line.quantity
                ol.save(update_fields=["quantity_shipped"])

    container.status = ContainerStatus.SHIPPED
    container.order = order
    container.save(update_fields=["status", "order", "updated_at"])
    first = container.lines.first()
    ContainerEvent.objects.create(
        container=container, event_type=ContainerEventType.SHIPPED,
        product=first.product if first else None,
        quantity=container.quantity, order=order,
        comment=comment, created_by=user,
    )
    return container


@transaction.atomic
def container_move(*, container, warehouse_to, user=None, comment=""):
    if not container.warehouse_id:
        raise ServiceError("В таре не указан склад-источник")
    if container.warehouse_id == warehouse_to.id:
        raise ServiceError("Склады совпадают")

    for line in container.lines.all():
        if line.quantity <= 0:
            continue
        apply_movement(
            movement_type=MovementType.TRANSFER, product=line.product,
            quantity=line.quantity, warehouse_from=container.warehouse,
            warehouse_to=warehouse_to, container=container,
            comment=comment or f"Перемещение {container.code}", user=user,
        )
    container.warehouse = warehouse_to
    container.save(update_fields=["warehouse", "updated_at"])
    first = container.lines.first()
    ContainerEvent.objects.create(
        container=container, event_type=ContainerEventType.MOVED,
        product=first.product if first else None,
        quantity=container.quantity,
        comment=comment, created_by=user,
    )
    return container


# ============================================================================
# Сброс оперативных данных
# ============================================================================
PROTECTED_WAREHOUSE_CODES = ["MAIN", "RESERVE", "ZLK"]


def reset_operational_data(*, user=None, keep_warehouses=False) -> dict:
    """Удаляет ВСЕ оперативные данные, оставляет:
      * номенклатуру (Product, Category, BOM, BOMLine)
      * склады MAIN, RESERVE, ZLK (или все, если keep_warehouses=True)
      * пользователей, реквизиты, категории товаров
    """
    from orders.models import Order, OrderLine, OrderStatusHistory
    from warehouse.models import (
        Container, ContainerEvent, ContainerLine,
        Inventory, InventoryLine,
        Movement, Operation, OperationLine, Stock, Warehouse,
    )
    from django.db import transaction

    deleted = {}

    with transaction.atomic():
        # Аудит
        try:
            from audit.models import AuditLog
            deleted["AuditLog"] = AuditLog.objects.all().delete()[0]
        except Exception:
            pass

        # Старые партии (пока модель жива — надо чистить)
        try:
            from warehouse.models import ProductionRun, ProductionRunResult, ScrapEntry
            deleted["ScrapEntry"] = ScrapEntry.objects.all().delete()[0]
            deleted["ProductionRunResult"] = ProductionRunResult.objects.all().delete()[0]
            deleted["ProductionRun"] = ProductionRun.objects.all().delete()[0]
        except Exception:
            pass

        # Новые операции
        deleted["OperationLine"] = OperationLine.objects.all().delete()[0]
        deleted["Operation"] = Operation.objects.all().delete()[0]

        # Тара
        deleted["ContainerEvent"] = ContainerEvent.objects.all().delete()[0]
        deleted["ContainerLine"] = ContainerLine.objects.all().delete()[0]
        deleted["Container"] = Container.objects.all().delete()[0]

        # Движения, остатки, инвентаризация
        deleted["Movement"] = Movement.objects.all().delete()[0]
        deleted["InventoryLine"] = InventoryLine.objects.all().delete()[0]
        deleted["Inventory"] = Inventory.objects.all().delete()[0]
        deleted["Stock"] = Stock.objects.all().delete()[0]

        # Заказы
        deleted["OrderStatusHistory"] = OrderStatusHistory.objects.all().delete()[0]
        deleted["OrderLine"] = OrderLine.objects.all().delete()[0]
        deleted["Order"] = Order.objects.all().delete()[0]

        # Накладные
        try:
            from warehouse.models import ShipmentNote, ShipmentNoteLine
            deleted["ShipmentNoteLine"] = ShipmentNoteLine.objects.all().delete()[0]
            deleted["ShipmentNote"] = ShipmentNote.objects.all().delete()[0]
        except Exception:
            pass

        # Лишние склады
        if not keep_warehouses:
            try:
                extra_wh = Warehouse.objects.exclude(
                    code__in=PROTECTED_WAREHOUSE_CODES
                )
                deleted["Warehouse(лишние)"] = extra_wh.delete()[0]
            except Exception:
                pass

    # Гарантируем наличие MAIN / RESERVE / ZLK
    Warehouse.objects.get_or_create(
        code="MAIN", defaults={"name": "Основной склад"})
    Warehouse.objects.get_or_create(
        code="RESERVE", defaults={"name": "Задел у станков"})
    Warehouse.objects.get_or_create(
        code="ZLK", defaults={"name": "Завод / Литейка"})

    return deleted
