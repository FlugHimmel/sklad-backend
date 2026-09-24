"""
Сканирующая отгрузка тар со склада с полным удалением тары из БД.

Сценарий: кладовщик сканирует все тары, которые отгружаются.
Содержимое списывается со склада (Movement OUT), сама тара удаляется из
системы полностью. Штрихкод тары аннулируется — при следующем скане
та же тара уже не найдётся.

Каждая тара обрабатывается в своей транзакции: падение одной не откатывает
остальные.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import ProtectedError

from .models import (
    Container,
    ContainerEvent,
    ContainerLine,
    ContainerStatus,
    Movement,
    MovementType,
)
from .services import ServiceError, apply_movement


def _ship_one(container, user=None, comment=""):
    """
    Списывает содержимое одной тары и удаляет её из БД.
    Возвращает dict: {container_id, code, quantity, warehouse_name}.
    """
    if container.status != ContainerStatus.WAREHOUSE:
        raise ServiceError(
            f"Тара не на складе (текущий статус: {container.get_status_display()})"
        )
    if not container.lines.exists():
        raise ServiceError("Тара пустая")
    if not container.warehouse_id:
        raise ServiceError("У тары не указан склад")

    warehouse_name = container.warehouse.name if container.warehouse else ""

    total_qty = Decimal("0")
    for line in container.lines.all():
        if line.quantity <= 0:
            continue
        apply_movement(
            movement_type=MovementType.OUT,
            product=line.product,
            quantity=line.quantity,
            warehouse_from=container.warehouse,
            container=None,
            comment=comment or f"Отгрузка (сканирование) {container.code}",
            user=user,
        )
        total_qty += line.quantity

    code = container.code
    container_id = container.id

    # Отвязываем все движения этой тары, чтобы FK не мешал удалению
    # и чтобы история движений сохранилась.
    Movement.objects.filter(container=container).update(container=None)

    # Сначала сносим зависимые записи (на случай PROTECT).
    ContainerEvent.objects.filter(container=container).delete()
    ContainerLine.objects.filter(container=container).delete()

    try:
        container.delete()
    except ProtectedError as exc:
        names = ", ".join(str(o) for o in list(exc.protected_objects)[:5])
        raise ServiceError(f"Не удалось удалить тару (используется в: {names})")

    return {
        "container_id": container_id,
        "code": code,
        "quantity": str(total_qty),
        "warehouse_name": warehouse_name,
    }


@transaction.atomic
def _ship_one_tx(container, user=None, comment=""):
    return _ship_one(container, user=user, comment=comment)


def ship_and_delete_by_codes(*, codes, user=None, comment=""):
    """
    Массовая отгрузка+удаление тар по списку кодов.

    Возвращает кортеж:
      (shipped:list[dict], not_found:list[str],
       errors:list[dict], skipped:list[dict])
    """
    shipped = []
    not_found = []
    errors = []
    skipped = []
    seen = set()

    for raw in codes or []:
        code = str(raw).strip()
        if not code or code in seen:
            continue
        seen.add(code)

        container = Container.objects.filter(code=code).first()
        if not container:
            not_found.append(code)
            continue

        if container.status != ContainerStatus.WAREHOUSE:
            skipped.append({
                "code": code,
                "reason": f"не на складе (статус: {container.get_status_display()})",
            })
            continue

        try:
            info = _ship_one_tx(container, user=user, comment=comment)
            shipped.append(info)
        except ServiceError as exc:
            errors.append({"code": code, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            errors.append({"code": code, "error": f"Внутренняя ошибка: {exc}"})

    return shipped, not_found, errors, skipped
