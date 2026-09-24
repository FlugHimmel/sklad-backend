from decimal import Decimal

from django.db.models import Q, Sum

from inventory.models import BOM, Product
from warehouse.models import Container, ContainerLine, Stock


def _to_dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _resolve_casting(product, cache=None):
    """Ищет артикул литья для детали:
    1. По активному BOM (компонент).
    2. По обратной Mo-связи: Product(product_type='casting', mo1..mo4=деталь).
    Кэш — словарь {product_id: Product|None} для устранения N+1.
    """
    if cache is not None and product.id in cache:
        return cache[product.id]
    result = None
    bom = (
        BOM.objects.filter(product=product, is_active=True)
        .prefetch_related("lines__component")
        .first()
    )
    if bom:
        first_line = bom.lines.first()
        if first_line:
            result = first_line.component
    if result is None:
        for slot in ("mo1", "mo2", "mo3", "mo4"):
            found = Product.objects.filter(
                product_type="casting", **{slot: product}
            ).first()
            if found:
                result = found
                break
    if cache is not None:
        cache[product.id] = result
    return result


def _stock_total(product) -> Decimal:
    total = (
        Stock.objects.filter(product=product).aggregate(s=Sum("quantity"))["s"]
        or Decimal("0")
    )
    return _to_dec(total)


def _packed_total(product) -> Decimal:
    """
    Сколько продукта лежит в УПАКОВАННЫХ тарах на складе MAIN.
    Это и есть «готово к отгрузке».
    """
    from django.db.models import Sum as _Sum
    agg = (
        ContainerLine.objects
        .filter(
            product=product,
            container__packed_at__isnull=False,
            container__shipped_at__isnull=True,
            container__warehouse__code="MAIN",
        )
        .aggregate(s=_Sum("quantity"))
    )
    return _to_dec(agg["s"])


def _packed_info(product) -> dict:
    """Информация об упакованных тарах продукта (на складе MAIN, не отгружено).
    Возвращает {qty, places, weight_kg, containers: [{code, qty, packed_at}]}.
    """
    if product is None:
        return {"qty": Decimal("0"), "places": 0,
                "weight_kg": Decimal("0"), "containers": []}

    from warehouse.models import ContainerLine

    lines = (ContainerLine.objects
             .filter(
                 product=product,
                 container__packed_at__isnull=False,
                 container__shipped_at__isnull=True,
                 container__warehouse__code="MAIN",
                 quantity__gt=0,
             )
             .select_related("container")
             .order_by("-container__packed_at"))

    qty = Decimal("0")
    weight_kg = Decimal("0")
    places = 0
    containers = []
    seen = set()
    wg = Decimal(str(product.weight_g)) if product.weight_g else Decimal("0")

    for cl in lines:
        qty += cl.quantity
        weight_kg += (wg * Decimal(str(cl.quantity))) / Decimal("1000")
        if cl.container_id not in seen:
            seen.add(cl.container_id)
            places += 1
            containers.append({
                "container_id": cl.container_id,
                "container_code": cl.container.code,
                "quantity": str(cl.quantity),
                "packed_at": (cl.container.packed_at.isoformat()
                              if cl.container.packed_at else None),
            })

    return {
        "qty": qty,
        "places": places,
        "weight_kg": weight_kg,
        "containers": containers,
    }


def _line_dict(line, casting_cache=None) -> dict:
    """Расчёт одной строки заказа: сколько готово, сколько литья надо,
    сколько есть, сколько не хватает.
    Задел = quantity_done − quantity_planned (автоматически).
    """
    planned = _to_dec(line.quantity_planned)
    done = _to_dec(line.quantity_done)
    shipped = _to_dec(line.quantity_shipped)

    # Задел — авто: насколько факт перекрывает план (может быть отрицательным).
    reserve = done - planned

    # Сколько ещё надо сделать (не меньше 0).
    remaining_work = max(Decimal("0"), planned - done)

    stock_finished = _stock_total(line.product)

    casting_product = line.casting or _resolve_casting(
        line.product, cache=casting_cache
    )

    casting_article = casting_product.article if casting_product else None
    casting_name = casting_product.name if casting_product else None
    casting_needed = None
    casting_stock = None
    casting_shortage = None
    casting_ok = None

    if casting_product:
        casting_needed = remaining_work
        casting_stock = _stock_total(casting_product)
        casting_shortage = max(Decimal("0"), remaining_work - casting_stock)
        casting_ok = casting_shortage == 0

    packed_info = _packed_info(line.product)
    packed_qty = packed_info["qty"]
    packed_places = packed_info["places"]
    packed_weight_kg = packed_info["weight_kg"]
    packed_containers = packed_info["containers"]

    to_ship_now = min(packed_qty, max(Decimal("0"), planned - shipped))
    short_to_plan = max(Decimal("0"), planned - shipped - packed_qty)

    return {
        "line_id": line.id,
        "product_id": line.product_id,
        "product_article": line.product.article,
        "product_name": line.name or line.product.name,
        "quantity_planned": planned,
        "quantity_done": done,
        "quantity_shipped": shipped,
        "quantity_packed": packed_qty,
        "packed_places": packed_places,
        "packed_weight_kg": str(packed_weight_kg.quantize(Decimal("0.001"))),
        "packed_containers": packed_containers,
        "to_ship_now": to_ship_now,
        "short_to_plan": short_to_plan,
        "reserve": reserve,
        "ready_total": done,
        "remaining": remaining_work,
        "stock_finished": stock_finished,
        "casting_article": casting_article,
        "casting_name": casting_name,
        "casting_needed": casting_needed,
        "casting_stock": casting_stock,
        "casting_shortage": casting_shortage,
        "casting_ok": casting_ok,
    }


def order_fulfillment_data(order) -> list:
    """По строкам заказа — состояние выполнения."""
    cache = {}
    return [
        _line_dict(line, casting_cache=cache)
        for line in order.lines.select_related("product", "casting").all()
    ]


def flat_lines_queryset(*, status=None, search=None, date_from=None,
                        date_to=None, order_id=None):
    """Плоский список строк заказов с фильтрами (для сводной таблицы)."""
    from .models import OrderLine

    qs = (
        OrderLine.objects
        .select_related("order", "product", "casting")
        .order_by("-order__created_at", "order_id", "sequence", "id")
    )
    if order_id:
        qs = qs.filter(order_id=order_id)
    if status:
        qs = qs.filter(order__status=status)
    if search:
        qs = qs.filter(
            Q(order__number__icontains=search)
            | Q(product__article__icontains=search)
            | Q(product__name__icontains=search)
            | Q(casting__article__icontains=search)
            | Q(name__icontains=search)
        )
    if date_from:
        qs = qs.filter(order__due_date__gte=date_from)
    if date_to:
        qs = qs.filter(order__due_date__lte=date_to)
    return qs
