"""
Отчёты на новой логике (Operation / OperationLine).

  GET /api/reports/production-ops/  — производство
  GET /api/reports/scrap-ops/       — брак по причинам / операторам / артикулам

Параметры (общие):
  date_from  — YYYY-MM-DD (по умолчанию: 1-е число текущего месяца)
  date_to    — YYYY-MM-DD (по умолчанию: без ограничения сверху)
  search     — текстовый поиск
"""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import Product
from warehouse.models import (
    Container,
    ContainerLine,
    Operation,
    OperationDirection,
    OperationLine,
    OperationType,
    ScrapReason,
)


def _canonical_maps():
    """Возвращает (canonical_map, canonical_products).

    canonical_map: {любой product_id: id главного}
    canonical_products: {canonical_id: Product главного}
    """
    from inventory.models import Product
    canonical_map = {}
    canonical_products = {}
    for p in Product.objects.all().only(
            "id", "alias_of_id", "article", "name",
            "product_type", "uom", "weight_g"):
        cid = p.alias_of_id or p.id
        canonical_map[p.id] = cid
        if p.alias_of_id is None:
            canonical_products[p.id] = p
    # Подстраховка: если canonical_id нет в canonical_products
    missing = set(canonical_map.values()) - set(canonical_products.keys())
    if missing:
        for p in Product.objects.filter(id__in=missing):
            canonical_products[p.id] = p
    return canonical_map, canonical_products


def _parse_period(request):
    now = timezone.localtime()
    df = request.query_params.get("date_from")
    dt = request.query_params.get("date_to")
    if df:
        date_from = timezone.make_aware(datetime.strptime(df, "%Y-%m-%d"))
    else:
        date_from = timezone.make_aware(datetime(now.year, now.month, 1))
    if dt:
        date_to = timezone.make_aware(
            datetime.strptime(dt, "%Y-%m-%d")) + timedelta(days=1)
    else:
        date_to = None
    return date_from, date_to


def _pct(part: Decimal, total: Decimal) -> str:
    if not total or total == 0:
        return "0"
    return str((part * Decimal("100") / total).quantize(Decimal("0.01")))


# ===========================================================================
# Отчёт по производству
# ===========================================================================
class ProductionOpsReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date_from, date_to = _parse_period(request)
        qs = (Operation.objects
              .filter(created_at__gte=date_from)
              .select_related("created_by", "order")
              .prefetch_related("lines__container", "lines__product"))
        if date_to:
            qs = qs.filter(created_at__lt=date_to)

        op_type = request.query_params.get("operation_type")
        if op_type:
            qs = qs.filter(operation_type=op_type)

        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(comment__icontains=search)
                | Q(created_by__username__icontains=search)
                | Q(lines__product__article__icontains=search)
                | Q(lines__product__name__icontains=search)
                | Q(lines__container__code__icontains=search)
            ).distinct()

        qs = qs.order_by("-created_at")
        ops = list(qs)

        # CANONICAL-PROD: маппинг product_id → canonical_id
        canonical_map, canonical_products = _canonical_maps()

        total_from = Decimal("0")
        total_to = Decimal("0")
        total_scrap = Decimal("0")

        by_operator = {}
        by_product_from = defaultdict(lambda: Decimal("0"))
        by_product_to = defaultdict(lambda: Decimal("0"))
        by_product_scrap = defaultdict(lambda: Decimal("0"))
        by_type = defaultdict(lambda: {
            "count": 0, "from": Decimal("0"),
            "to": Decimal("0"), "scrap": Decimal("0"),
        })

        rows = []
        for op in ops:
            op_from = Decimal("0")
            op_to = Decimal("0")
            op_scrap = Decimal("0")
            rows_from, rows_to, rows_scrap = [], [], []

            for line in op.lines.all():
                if line.direction == OperationDirection.FROM:
                    op_from += line.qty
                    total_from += line.qty
                    cid = canonical_map.get(line.product_id, line.product_id)
                    by_product_from[cid] += line.qty
                    rows_from.append({
                        "container_id": line.container_id,
                        "container_code":
                            line.container.code if line.container else None,
                        "product_id": line.product_id,
                        "product_article": line.product.article,
                        "product_name": line.product.name,
                        "qty": str(line.qty),
                    })
                elif line.direction == OperationDirection.TO:
                    op_to += line.qty
                    total_to += line.qty
                    cid = canonical_map.get(line.product_id, line.product_id)
                    by_product_to[cid] += line.qty
                    rows_to.append({
                        "container_id": line.container_id,
                        "container_code":
                            line.container.code if line.container else None,
                        "product_id": line.product_id,
                        "product_article": line.product.article,
                        "product_name": line.product.name,
                        "qty": str(line.qty),
                    })
                elif line.direction == OperationDirection.SCRAP:
                    op_scrap += line.qty
                    total_scrap += line.qty
                    cid = canonical_map.get(line.product_id, line.product_id)
                    by_product_scrap[cid] += line.qty
                    rows_scrap.append({
                        "reason": line.scrap_reason,
                        "reason_display": line.get_scrap_reason_display(),
                        "product_id": line.product_id,
                        "product_article": line.product.article,
                        "qty": str(line.qty),
                    })

            op_key = op.created_by_id or 0
            if op_key not in by_operator:
                by_operator[op_key] = {
                    "operator_id": op.created_by_id,
                    "operator": (op.created_by.username
                                 if op.created_by else "(не указан)"),
                    "operations": 0,
                    "from_total": Decimal("0"),
                    "to_total": Decimal("0"),
                    "scrap_total": Decimal("0"),
                }
            o = by_operator[op_key]
            o["operations"] += 1
            o["from_total"] += op_from
            o["to_total"] += op_to
            o["scrap_total"] += op_scrap

            t = by_type[op.operation_type]
            t["count"] += 1
            t["from"] += op_from
            t["to"] += op_to
            t["scrap"] += op_scrap

            rows.append({
                "id": op.id,
                "created_at": op.created_at.isoformat(),
                "operation_type": op.operation_type,
                "operation_type_display": op.get_operation_type_display(),
                "created_by": (op.created_by.username
                               if op.created_by else None),
                "comment": op.comment,
                "from": rows_from,
                "to": rows_to,
                "scrap": rows_scrap,
                "from_total": str(op_from),
                "to_total": str(op_to),
                "scrap_total": str(op_scrap),
            })

        # Операторы
        by_operator_rows = []
        for o in by_operator.values():
            from_dec = o["from_total"]
            o["from_total"] = str(o["from_total"])
            o["to_total"] = str(o["to_total"])
            o["scrap_total"] = str(o["scrap_total"])
            o["scrap_percent"] = _pct(Decimal(o["scrap_total"]), from_dec)
            by_operator_rows.append(o)
        by_operator_rows.sort(key=lambda r: -float(r["from_total"]))

        # Продукты — используем канонические
        all_pids = set(by_product_from) | set(by_product_to) | set(by_product_scrap)
        by_product_rows = []
        for pid in all_pids:
            p = canonical_products.get(pid)
            if not p:
                continue
            by_product_rows.append({
                "product_id": pid,
                "article": p.article,
                "name": p.name,
                "product_type": p.product_type,
                "from_qty": str(by_product_from.get(pid, Decimal("0"))),
                "to_qty": str(by_product_to.get(pid, Decimal("0"))),
                "scrap_qty": str(by_product_scrap.get(pid, Decimal("0"))),
            })
        by_product_rows.sort(key=lambda r: -float(r["to_qty"]))

        # Типы
        type_labels = dict(OperationType.choices)
        type_rows = []
        for k, v in by_type.items():
            type_rows.append({
                "type": k,
                "type_display": type_labels.get(k, k),
                "operations": v["count"],
                "from_total": str(v["from"]),
                "to_total": str(v["to"]),
                "scrap_total": str(v["scrap"]),
            })

        return Response({
            "date_from": date_from.date().isoformat(),
            "date_to": (date_to - timedelta(days=1)).date().isoformat()
                       if date_to else None,
            "totals": {
                "operations": len(ops),
                "from_total": str(total_from),
                "to_total": str(total_to),
                "scrap_total": str(total_scrap),
                "scrap_percent": _pct(total_scrap, total_from),
            },
            "by_operator": by_operator_rows,
            "by_product": by_product_rows,
            "by_type": type_rows,
            "rows": rows[:500],
        })


# ===========================================================================
# Отчёт по браку
# ===========================================================================
class ScrapOpsReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date_from, date_to = _parse_period(request)

        qs = (OperationLine.objects
              .filter(direction=OperationDirection.SCRAP,
                      operation__created_at__gte=date_from)
              .select_related("operation", "operation__created_by", "product"))
        if date_to:
            qs = qs.filter(operation__created_at__lt=date_to)

        reason = request.query_params.get("reason")
        if reason:
            qs = qs.filter(scrap_reason=reason)

        op_id = request.query_params.get("operator")
        if op_id:
            qs = qs.filter(operation__created_by_id=op_id)

        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(product__article__icontains=search)
                | Q(product__name__icontains=search)
                | Q(operation__comment__icontains=search)
                | Q(operation__created_by__username__icontains=search)
            )

        qs = qs.order_by("-operation__created_at")
        lines = list(qs)

        # CANONICAL-SCRAP: маппинг product_id → canonical_id
        canonical_map, canonical_products = _canonical_maps()

        total = sum((l.qty for l in lines), Decimal("0"))

        by_reason = defaultdict(lambda: Decimal("0"))
        by_operator = {}
        by_product = defaultdict(lambda: Decimal("0"))

        rows = []
        op_ids = set()
        for line in lines:
            op = line.operation
            op_ids.add(op.id)
            by_reason[line.scrap_reason] += line.qty
            cid = canonical_map.get(line.product_id, line.product_id)
            by_product[cid] += line.qty

            op_key = op.created_by_id or 0
            op_name = op.created_by.username if op.created_by else "(не указан)"
            if op_key not in by_operator:
                by_operator[op_key] = {
                    "operator_id": op.created_by_id,
                    "operator": op_name,
                    "qty": Decimal("0"),
                    "operations": set(),
                    "by_reason": defaultdict(lambda: Decimal("0")),
                }
            by_operator[op_key]["qty"] += line.qty
            by_operator[op_key]["operations"].add(op.id)
            by_operator[op_key]["by_reason"][line.scrap_reason] += line.qty

            rows.append({
                "id": line.id,
                "operation_id": op.id,
                "created_at": op.created_at.isoformat(),
                "operation_type_display": op.get_operation_type_display(),
                "operator": op_name,
                "product_id": line.product_id,
                "product_article": line.product.article,
                "product_name": line.product.name,
                "reason": line.scrap_reason,
                "reason_display": line.get_scrap_reason_display(),
                "qty": str(line.qty),
                "comment": op.comment,
            })

        reason_labels = dict(ScrapReason.choices)
        by_reason_rows = [
            {"reason": k, "label": reason_labels.get(k, k), "qty": str(v)}
            for k, v in sorted(by_reason.items(), key=lambda x: -x[1])
        ]

        by_operator_rows = []
        for v in by_operator.values():
            by_operator_rows.append({
                "operator_id": v["operator_id"],
                "operator": v["operator"],
                "operations": len(v["operations"]),
                "qty": str(v["qty"]),
                "by_reason": {rk: str(rv)
                              for rk, rv in v["by_reason"].items()},
            })
        by_operator_rows.sort(key=lambda r: -float(r["qty"]))

        by_product_rows = []
        for pid, qty in by_product.items():
            p = canonical_products.get(pid)
            if not p:
                continue
            by_product_rows.append({
                "product_id": pid,
                "article": p.article,
                "name": p.name,
                "qty": str(qty),
            })
        by_product_rows.sort(key=lambda r: -float(r["qty"]))

        return Response({
            "date_from": date_from.date().isoformat(),
            "date_to": (date_to - timedelta(days=1)).date().isoformat()
                       if date_to else None,
            "totals": {
                "scrap_total": str(total),
                "operations": len(op_ids),
            },
            "by_reason": by_reason_rows,
            "by_operator": by_operator_rows,
            "by_product": by_product_rows,
            "rows": rows[:1000],
        })

# ===========================================================================
# Что готово к отгрузке
# ===========================================================================
class ReadyToShipReportView(APIView):
    """
    GET /api/reports/ready-to-ship/

    READY-V2: возвращает сводку по продуктам — сколько упаковано,
    сколько мест, вес нетто и вес брутто (с поддоном и бортами).

    Вес брутто = нетто + 23 кг (поддон) + 2×7 кг (борта) = нетто + 37 кг
    — считается НА КАЖДОЕ МЕСТО (тару).
    """
    permission_classes = [IsAuthenticated]

    # Вес поддона и одного борта — если будет меняться, вынести в настройки
    PALLET_KG = Decimal("23")
    BOARD_KG = Decimal("7")
    BOARDS_PER_PLACE = 2

    def get(self, request):
        qs = (
            ContainerLine.objects
            .filter(
                container__packed_at__isnull=False,
                container__shipped_at__isnull=True,
                container__warehouse__code="MAIN",
                quantity__gt=0,
            )
            .select_related("container", "product")
        )

        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(product__article__icontains=search)
                | Q(product__name__icontains=search)
                | Q(container__code__icontains=search)
            )

        # CATEGORY-FILTER: рабочие колёса / компоненты
        category = (request.query_params.get("category") or "all").strip()
        if category == "wheels":
            qs = qs.filter(product__name__icontains="колесо")
        elif category == "components":
            qs = qs.exclude(product__name__icontains="колесо")

        # Группируем по продукту
        by_product = {}

        for cl in qs:
            pid = cl.product_id
            product = cl.product
            if pid not in by_product:
                by_product[pid] = {
                    "product_id": pid,
                    "article": product.article,
                    "name": product.name,
                    "product_type": product.product_type,
                    "uom": product.uom,
                    "weight_g": str(product.weight_g or "0"),
                    "total_qty": Decimal("0"),
                    "places": 0,
                    "netto_kg": Decimal("0"),
                    "brutto_kg": Decimal("0"),
                    "containers": [],
                    "_seen_containers": set(),
                }

            rec = by_product[pid]
            rec["total_qty"] += cl.quantity

            # Вес нетто по строке
            wg = Decimal(str(product.weight_g or "0"))
            line_netto = (wg * Decimal(str(cl.quantity))) / Decimal("1000")
            rec["netto_kg"] += line_netto

            # Учитываем тару как место (один раз на тару)
            cid = cl.container_id
            if cid not in rec["_seen_containers"]:
                rec["_seen_containers"].add(cid)
                rec["places"] += 1

            # Инфа о таре — для раскрытия
            rec["containers"].append({
                "container_id": cl.container_id,
                "container_code": cl.container.code,
                "quantity": str(cl.quantity),
                "netto_kg": str(line_netto.quantize(Decimal("0.001"))),
                "packed_at": (cl.container.packed_at.isoformat()
                              if cl.container.packed_at else None),
            })

        # Считаем брутто по количеству мест
        per_place_extra = (self.PALLET_KG
                           + self.BOARD_KG * self.BOARDS_PER_PLACE)

        rows = []
        for rec in by_product.values():
            brutto = rec["netto_kg"] + per_place_extra * rec["places"]
            rec["total_qty"] = str(rec["total_qty"])
            rec["netto_kg"] = str(rec["netto_kg"].quantize(Decimal("0.001")))
            rec["brutto_kg"] = str(brutto.quantize(Decimal("0.001")))
            rec.pop("_seen_containers", None)
            rows.append(rec)

        rows.sort(key=lambda r: r["article"])

        # Итоги
        total_places = sum(r["places"] for r in rows)
        total_qty = sum((Decimal(r["total_qty"]) for r in rows), Decimal("0"))
        total_netto = sum((Decimal(r["netto_kg"]) for r in rows), Decimal("0"))
        total_brutto = sum((Decimal(r["brutto_kg"]) for r in rows), Decimal("0"))

        return Response({
            "count": len(rows),
            "totals": {
                "places": total_places,
                "qty": str(total_qty),
                "netto_kg": str(total_netto.quantize(Decimal("0.001"))),
                "brutto_kg": str(total_brutto.quantize(Decimal("0.001"))),
            },
            "constants": {
                "pallet_kg": str(self.PALLET_KG),
                "board_kg": str(self.BOARD_KG),
                "boards_per_place": self.BOARDS_PER_PLACE,
                "per_place_extra_kg": str(per_place_extra),
            },
            "rows": rows,
        })
