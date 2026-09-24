"""
Операции после обработки тары:
  «Принять от изготовителя» / «Вывести с работы».

Сервис один — container_finish_stage.

Логика:
  • Источник — тара в статусе WAITING_MILLING / WAITING_PACKING.
  • Из неё списывается qty_total (обычно = всё содержимое).
  • Годные раскладываются по тарам-приёмникам, статус = next_status.
  • Задел раскладывается по тарам-приёмникам, статус = WAREHOUSE,
    склад = reserve_warehouse (по умолчанию RESERVE, если есть).
  • Источник → EMPTY.
"""
from decimal import Decimal

from django.db import transaction

from .models import (
    Container, ContainerEvent, ContainerEventType, ContainerLine,
    ContainerStatus, MovementType, Warehouse,
)
from .services import ServiceError, apply_movement


def _get_reserve_warehouse(fallback):
    """Возвращает склад RESERVE, если он есть. Иначе fallback."""
    wh = Warehouse.objects.filter(code="RESERVE").first()
    return wh or fallback


@transaction.atomic
def container_finish_stage(*, source, result_product, qty_total,
                           good_entries, reserve_entries,
                           next_status=ContainerStatus.READY_TO_SHIP,
                           warehouse, reserve_warehouse=None,
                           comment="", user=None):
    """
    Перераспределение содержимого тары после обработки.

    source              — Container (waiting_milling / waiting_packing)
    result_product      — Product (что получилось). Если None — берём из источника.
    qty_total           — Decimal, сколько берём из источника
    good_entries        — [{"container": Container|новый, "quantity": Decimal}]
    reserve_entries     — [{"container": Container|новый, "quantity": Decimal}]
    next_status         — статус для годных тар
    warehouse           — склад для годных тар
    reserve_warehouse   — склад для задела (если None — берём RESERVE, иначе warehouse)
    """
    # ── Проверки ──────────────────────────────────────────────
    if source.status not in (ContainerStatus.WAITING_MILLING,
                             ContainerStatus.WAITING_PACKING):
        raise ServiceError(
            f"Тара {source.code} не в стадии обработки "
            f"(статус: {source.get_status_display()}). "
            f"Нужно «Ждёт фрезеровки» или «Ждёт упаковки»."
        )

    qty_total = Decimal(str(qty_total))
    if qty_total <= 0:
        raise ServiceError("Нулевое количество")
    if qty_total > source.quantity:
        raise ServiceError(
            f"В таре {source.code} только {source.quantity}, "
            f"а списываем {qty_total}"
        )

    src_line = source.lines.select_related("product").first()
    if not src_line:
        raise ServiceError(f"Тара {source.code} пустая")

    if result_product is None:
        result_product = src_line.product

    if not warehouse:
        raise ServiceError("Не указан склад-приёмник")

    if reserve_warehouse is None:
        reserve_warehouse = _get_reserve_warehouse(warehouse)

    good_entries = good_entries or []
    reserve_entries = reserve_entries or []

    total_good = sum(
        (Decimal(str(e.get("quantity", "0"))) for e in good_entries),
        Decimal("0"),
    )
    total_reserve = sum(
        (Decimal(str(e.get("quantity", "0"))) for e in reserve_entries),
        Decimal("0"),
    )

    if total_good + total_reserve != qty_total:
        raise ServiceError(
            f"Не сходится: годные {total_good} + задел {total_reserve} "
            f"= {total_good + total_reserve}, а пришло {qty_total}"
        )

    allowed_next = {
        ContainerStatus.WAREHOUSE,
        ContainerStatus.WAITING_MILLING,
        ContainerStatus.WAITING_PACKING,
        ContainerStatus.READY_TO_SHIP,
    }
    if next_status not in allowed_next:
        raise ServiceError(f"Недопустимый следующий статус: {next_status}")

    # ── Списание из источника ─────────────────────────────────
    source_product = src_line.product
    mv_out = apply_movement(
        movement_type=MovementType.PRODUCE_OUT,
        product=source_product, quantity=qty_total,
        warehouse_from=source.warehouse, container=source,
        comment=f"Обработка {source.code} → {result_product.article}",
        user=user,
    )

    src_line.quantity = src_line.quantity - qty_total
    if src_line.quantity <= 0:
        src_line.delete()
        source.status = ContainerStatus.EMPTY
    else:
        src_line.save(update_fields=["quantity", "updated_at"])
    source.recalculate()
    if source.status == ContainerStatus.EMPTY:
        source.save(update_fields=["status", "updated_at"])

    ContainerEvent.objects.create(
        container=source,
        event_type=ContainerEventType.ISSUED,
        product=source_product, quantity=qty_total,
        movement=mv_out,
        comment=f"Списано при обработке: {qty_total} шт → {result_product.article}",
        created_by=user,
    )

    # ── Приход годных (если артикул изменился) ────────────────
    if total_good > 0 and result_product.id != source_product.id:
        apply_movement(
            movement_type=MovementType.PRODUCE_IN,
            product=result_product, quantity=total_good,
            warehouse_to=warehouse,
            comment=f"Приёмка: {total_good} шт {result_product.article}",
            user=user,
        )

    created_ids = []

    # ── Раскладка годных ──────────────────────────────────────
    for e in good_entries:
        c = e.get("container")
        if c is None:
            continue
        q = Decimal(str(e.get("quantity", "0")))
        if q <= 0:
            continue
        is_new = c.pk is None
        if is_new:
            c.product = result_product
            c.quantity = q
            c.status = next_status
            c.warehouse = warehouse
            c.save()
            ContainerLine.objects.create(
                container=c, product=result_product, quantity=q,
            )
            ContainerEvent.objects.create(
                container=c,
                event_type=ContainerEventType.CREATED,
                product=result_product, quantity=q,
                comment=f"Создана при обработке #{source.code}",
                created_by=user,
            )
        else:
            line, created = ContainerLine.objects.get_or_create(
                container=c, product=result_product,
                defaults={"quantity": q},
            )
            if not created:
                line.quantity = line.quantity + q
                line.save(update_fields=["quantity", "updated_at"])
            c.recalculate()
            c.status = next_status
            c.save(update_fields=["status", "updated_at"])
            ContainerEvent.objects.create(
                container=c,
                event_type=ContainerEventType.RETURNED,
                product=result_product, quantity=q,
                comment=f"Принято годных из #{source.code}",
                created_by=user,
            )
        created_ids.append(c.id)

    # ── Раскладка задела ──────────────────────────────────────
    for e in reserve_entries:
        c = e.get("container")
        if c is None:
            continue
        q = Decimal(str(e.get("quantity", "0")))
        if q <= 0:
            continue
        is_new = c.pk is None
        if is_new:
            c.product = result_product
            c.quantity = q
            c.status = ContainerStatus.WAREHOUSE
            c.warehouse = reserve_warehouse
            c.save()
            ContainerLine.objects.create(
                container=c, product=result_product, quantity=q,
            )
            ContainerEvent.objects.create(
                container=c,
                event_type=ContainerEventType.CREATED,
                product=result_product, quantity=q,
                comment=f"Задел: создана при обработке #{source.code}",
                created_by=user,
            )
        else:
            line, created = ContainerLine.objects.get_or_create(
                container=c, product=result_product,
                defaults={"quantity": q},
            )
            if not created:
                line.quantity = line.quantity + q
                line.save(update_fields=["quantity", "updated_at"])
            c.recalculate()
            c.status = ContainerStatus.WAREHOUSE
            c.save(update_fields=["status", "updated_at"])
            ContainerEvent.objects.create(
                container=c,
                event_type=ContainerEventType.RETURNED,
                product=result_product, quantity=q,
                comment=f"Задел: из #{source.code}",
                created_by=user,
            )
        created_ids.append(c.id)

    if comment:
        ContainerEvent.objects.create(
            container=source,
            event_type=ContainerEventType.ADJUSTED,
            product=source_product, quantity=Decimal("0"),
            comment=comment,
            created_by=user,
        )

    return source, created_ids
