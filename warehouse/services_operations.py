"""
Логика производственных операций.

Одна операция = одна запись в журнале.
Внутри — строки: from (взял), to (положил), scrap (брак).

Баланс: SUM(from.qty) == SUM(to.qty) + SUM(scrap.qty).

Эффекты:
  from  — списывает из ContainerLine (если стало 0 — строка удаляется)
  to    — зачисляет в ContainerLine (создаётся при необходимости)
  scrap — просто запись, физически ничего не меняется
  packing — ставит packed_at на тарах-приёмниках
  ship    — ставит shipped_at на тарах-приёмниках

Всё в транзакции. Ошибки — через OperationError.
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from warehouse.models import (
    Container,
    ContainerLine,
    Operation,
    OperationLine,
    OperationDirection,
    OperationType,
    ScrapReason,
)


class OperationError(Exception):
    """Ошибка бизнес-логики операции. Ловится во views, отдаётся 400."""
    pass


def _dec(value) -> Decimal:
    """Аккуратный парсинг числа. Пусто → 0. Мусор → ошибка."""
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise OperationError(f"Не могу распарсить число: {value!r}")


@transaction.atomic
def create_operation(
    *,
    operation_type: str,
    from_lines: list,
    to_lines: list,
    scrap_lines: list,
    user=None,
    comment: str = "",
    order=None,
) -> Operation:
    """
    Создать операцию.

    from_lines  = [{"container_id": int, "product_id": int, "qty": "50"}]
    to_lines    = [{"container_id": int, "product_id": int, "qty": "48"}]
    scrap_lines = [{"product_id": int, "qty": "2", "reason": "setup"}]

    Правила:
      * все источники (from) — одного артикула
      * баланс: SUM(from) == SUM(to) + SUM(scrap)
      * тара-приёмник ≠ тара-источник, КРОМЕ:
          - operation_type = move
          - возврат остатка того же артикула в ту же тару
      * одна пара (тара-приёмник, артикул) может встречаться только 1 раз
    """
    # ---- 0. Тип операции ----
    if operation_type not in OperationType.values:
        raise OperationError(f"Неизвестный тип операции: {operation_type!r}")

    # ---- 1. Валидация входных списков ----
    if not from_lines:
        raise OperationError("Не указано, откуда брали (from).")
    if not to_lines and not scrap_lines:
        raise OperationError("Не указано, куда положили или что ушло в брак.")

    # ---- 2. Разбор from ----
    from_parsed = []
    from_products = set()
    for i, fl in enumerate(from_lines, 1):
        cid = fl.get("container_id")
        pid = fl.get("product_id")
        qty = _dec(fl.get("qty"))
        if not cid:
            raise OperationError(f"Строка from #{i}: не указана тара.")
        if not pid:
            raise OperationError(f"Строка from #{i}: не указан артикул.")
        if qty <= 0:
            raise OperationError(f"Строка from #{i}: количество должно быть > 0.")
        from_products.add(pid)
        from_parsed.append((int(cid), int(pid), qty))

    if len(from_products) > 1:
        raise OperationError(
            "Все источники (from) должны быть одного артикула. "
            f"А у тебя: {sorted(from_products)}."
        )

    # ---- 3. Разбор to + проверка дубликатов ----
    to_parsed = []
    seen_to_pairs = {}
    for i, tl in enumerate(to_lines, 1):
        cid = tl.get("container_id")
        pid = tl.get("product_id")
        qty = _dec(tl.get("qty"))
        if not cid:
            raise OperationError(f"Строка to #{i}: не указана тара.")
        if not pid:
            raise OperationError(f"Строка to #{i}: не указан артикул.")
        if qty <= 0:
            raise OperationError(f"Строка to #{i}: количество должно быть > 0.")
        key = (int(cid), int(pid))
        if key in seen_to_pairs:
            c = Container.objects.filter(pk=cid).first()
            code = c.code if c else f"id={cid}"
            raise OperationError(
                f"Тара {code} для этого артикула указана дважды. "
                f"Если нужно положить в одну тару — сложи количества и оставь одну строку. "
                f"Если в разные — выбери разные тары."
            )
        seen_to_pairs[key] = True
        to_parsed.append((int(cid), int(pid), qty))

    # ---- 4. Разбор scrap ----
    scrap_parsed = []
    for i, sl in enumerate(scrap_lines, 1):
        qty = _dec(sl.get("qty"))
        if qty <= 0:
            continue
        reason = sl.get("reason") or ""
        if not reason:
            raise OperationError(f"Брак #{i}: не указана причина.")
        if reason not in ScrapReason.values:
            raise OperationError(
                f"Брак #{i}: неизвестная причина {reason!r}. "
                f"Допустимо: {list(ScrapReason.values)}"
            )
        pid = sl.get("product_id")
        if not pid:
            pid = next(iter(from_products))
        scrap_parsed.append((int(pid), qty, reason))

    # ---- 5. Баланс ----
    sum_from = sum(q for _, _, q in from_parsed)
    sum_to = sum(q for _, _, q in to_parsed)
    sum_scrap = sum(q for _, q, _ in scrap_parsed)
    if sum_from != sum_to + sum_scrap:
        raise OperationError(
            f"Баланс не сходится: взято {sum_from} ≠ "
            f"положено {sum_to} + брак {sum_scrap} = {sum_to + sum_scrap}."
        )

    # ---- 6. Тара-приёмник ≠ тара-источник (кроме MOVE и возврата) ----
    from_cids = {cid for cid, _, _ in from_parsed}
    if operation_type != OperationType.MOVE:
        for cid, pid, _ in to_parsed:
            if cid in from_cids and pid not in from_products:
                raise OperationError(
                    f"Тара-приёмник (id={cid}) — это тара-источник, "
                    f"но артикул в неё кладётся другой. "
                    f"Возврат остатка разрешён только того же артикула."
                )

    # ---- 7. Проверка: в тарах-источниках достаточно содержимого ----
    for cid, pid, qty in from_parsed:
        try:
            cl = ContainerLine.objects.select_for_update().get(
                container_id=cid, product_id=pid,
            )
        except ContainerLine.DoesNotExist:
            raise OperationError(
                f"В таре id={cid} нет артикула id={pid} — нечего списывать."
            )
        if cl.quantity < qty:
            raise OperationError(
                f"В таре {cl.container.code} только {cl.quantity} шт "
                f"артикула {cl.product.article}, а списать пытаемся {qty}."
            )

    # ---- 8. Создаём Operation ----
    op = Operation.objects.create(
        operation_type=operation_type,
        comment=comment or "",
        created_by=user,
        order=order,
    )

    # ---- 9. Строки from + эффект (списание) ----
    for cid, pid, qty in from_parsed:
        OperationLine.objects.create(
            operation=op,
            direction=OperationDirection.FROM,
            container_id=cid,
            product_id=pid,
            qty=qty,
        )
        cl = ContainerLine.objects.select_for_update().get(
            container_id=cid, product_id=pid,
        )
        new_qty = cl.quantity - qty
        if new_qty <= 0:
            cl.delete()
        else:
            cl.quantity = new_qty
            cl.save(update_fields=["quantity", "updated_at"])

    # ---- 10. Строки to + эффект (зачисление) ----
    for cid, pid, qty in to_parsed:
        OperationLine.objects.create(
            operation=op,
            direction=OperationDirection.TO,
            container_id=cid,
            product_id=pid,
            qty=qty,
        )
        cl, _ = ContainerLine.objects.select_for_update().get_or_create(
            container_id=cid,
            product_id=pid,
            defaults={"quantity": Decimal("0")},
        )
        cl.quantity = cl.quantity + qty
        cl.save(update_fields=["quantity", "updated_at"])

    # ---- 11. Строки scrap (только запись) ----
    for pid, qty, reason in scrap_parsed:
        OperationLine.objects.create(
            operation=op,
            direction=OperationDirection.SCRAP,
            container=None,
            product_id=pid,
            qty=qty,
            scrap_reason=reason,
        )

    # ---- 11.5. Упаковка → отметка packed_at на тарах-приёмниках ----
    if operation_type == OperationType.PACKING:
        to_cids = {cid for cid, _, _ in to_parsed}
        if to_cids:
            now = timezone.now()
            Container.objects.filter(id__in=to_cids).update(
                packed_at=now, updated_at=now)

    # ---- 11.6. Отгрузка → отметка shipped_at (задел на будущее) ----
    if operation_type == OperationType.SHIP:
        to_cids = {cid for cid, _, _ in to_parsed}
        if to_cids:
            now = timezone.now()
            Container.objects.filter(id__in=to_cids).update(
                shipped_at=now, updated_at=now)

    # ---- 12. Пересчёт затронутых тар ----
    touched = {cid for cid, _, _ in from_parsed} | {cid for cid, _, _ in to_parsed}
    for cid in touched:
        c = Container.objects.get(id=cid)
        c.recalculate()

    return op


@transaction.atomic
def rollback_operation(op: Operation) -> None:
    """
    Откат операции: возвращаем тары в исходное состояние, удаляем Operation.
    Для брака — ничего не откатываем (физически уже нет).
    Для упаковки — сбрасываем packed_at.
    """
    lines = list(op.lines.select_related("container", "product").all())

    for line in lines:
        if line.direction == OperationDirection.FROM:
            if line.container_id is None:
                continue
            cl, _ = ContainerLine.objects.select_for_update().get_or_create(
                container_id=line.container_id,
                product_id=line.product_id,
                defaults={"quantity": Decimal("0")},
            )
            cl.quantity = cl.quantity + line.qty
            cl.save(update_fields=["quantity", "updated_at"])

        elif line.direction == OperationDirection.TO:
            if line.container_id is None:
                continue
            try:
                cl = ContainerLine.objects.select_for_update().get(
                    container_id=line.container_id,
                    product_id=line.product_id,
                )
            except ContainerLine.DoesNotExist:
                continue
            new_qty = cl.quantity - line.qty
            if new_qty <= 0:
                cl.delete()
            else:
                cl.quantity = new_qty
                cl.save(update_fields=["quantity", "updated_at"])

    # Если это была упаковка — сбрасываем packed_at у тар-приёмников
    if op.operation_type == OperationType.PACKING:
        to_cids = {l.container_id for l in lines
                   if l.direction == OperationDirection.TO and l.container_id}
        if to_cids:
            Container.objects.filter(id__in=to_cids).update(packed_at=None)

    # Если это была отгрузка — сбрасываем shipped_at
    if op.operation_type == OperationType.SHIP:
        to_cids = {l.container_id for l in lines
                   if l.direction == OperationDirection.TO and l.container_id}
        if to_cids:
            Container.objects.filter(id__in=to_cids).update(shipped_at=None)

    touched = {l.container_id for l in lines if l.container_id}
    op.delete()

    for cid in touched:
        c = Container.objects.get(id=cid)
        c.recalculate()
