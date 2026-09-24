"""
Единая лента событий склада.

Объединяет:
  * Movement — приход, отгрузка, перемещение, списание, ручные операции
  * OperationLine — из операций: взял / положил / брак

Возвращает единый список с типом события, тарой, артикулом, кол-вом.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from warehouse.models import Movement, OperationLine, OperationDirection


# Группы типов
GROUP_IN = "in"        # приход
GROUP_OUT = "out"      # расход/отгрузка
GROUP_MOVE = "move"    # перемещение


def _movement_group(mt: str) -> str:
    if mt in ("in", "produce_in", "adjust"):
        return GROUP_IN
    if mt in ("out", "produce_out", "scrap"):
        return GROUP_OUT
    if mt == "transfer":
        return GROUP_MOVE
    return "other"


def _movement_group_label(mt: str) -> str:
    if mt == "in":
        return "Приход"
    if mt == "produce_in":
        return "Изготовлено"
    if mt == "adjust":
        return "Корректировка"
    if mt == "out":
        return "Отгрузка"
    if mt == "produce_out":
        return "Израсходовано"
    if mt == "scrap":
        return "Списание в брак"
    if mt == "transfer":
        return "Перемещение"
    return mt


class EventsView(APIView):
    """GET /api/reports/events/

    Query:
      date_from, date_to — YYYY-MM-DD
      search             — по коду тары, артикулу, наименованию, комментарию
      group              — in | out | move | all (по умолчанию all)
      container          — ID тары (опционально)
      product            — ID продукта (опционально)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # ── Период ─────────────────────────────────────────────
        now = timezone.localtime()
        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")

        if df:
            try:
                d_from = timezone.make_aware(datetime.strptime(df, "%Y-%m-%d"))
            except ValueError:
                d_from = timezone.make_aware(datetime(now.year, now.month, 1))
        else:
            d_from = timezone.make_aware(datetime(now.year, now.month, 1))

        if dt:
            try:
                d_to = timezone.make_aware(
                    datetime.strptime(dt, "%Y-%m-%d")
                ) + timedelta(days=1)
            except ValueError:
                d_to = None
        else:
            d_to = None

        search = (request.query_params.get("search") or "").strip()
        group = (request.query_params.get("group") or "all").strip()
        container_id = request.query_params.get("container")
        product_id = request.query_params.get("product")

        # ── Движения ───────────────────────────────────────────
        mv_qs = (Movement.objects
                 .filter(created_at__gte=d_from)
                 .select_related(
                     "product", "container", "order",
                     "warehouse_from", "warehouse_to", "created_by"))
        if d_to:
            mv_qs = mv_qs.filter(created_at__lt=d_to)
        if container_id:
            mv_qs = mv_qs.filter(container_id=container_id)
        if product_id:
            mv_qs = mv_qs.filter(product_id=product_id)

        movements = list(mv_qs)

        # ── Операции ───────────────────────────────────────────
        op_qs = (OperationLine.objects
                 .filter(operation__created_at__gte=d_from)
                 .select_related(
                     "operation", "operation__created_by",
                     "container", "product"))
        if d_to:
            op_qs = op_qs.filter(operation__created_at__lt=d_to)
        if container_id:
            op_qs = op_qs.filter(container_id=container_id)
        if product_id:
            op_qs = op_qs.filter(product_id=product_id)

        op_lines = list(op_qs)

        # ── Собираем единый список ────────────────────────────
        events = []

        for m in movements:
            g = _movement_group(m.movement_type)
            events.append({
                "id": f"mv-{m.id}",
                "kind": "movement",
                "group": g,
                "group_label": _movement_group_label(m.movement_type),
                "created_at": m.created_at.isoformat(),
                "product_id": m.product_id,
                "product_article": m.product.article if m.product else "",
                "product_name": m.product.name if m.product else "",
                "quantity": str(m.quantity),
                "container_id": m.container_id,
                "container_code": m.container.code if m.container else "",
                "warehouse_from": (m.warehouse_from.name
                                   if m.warehouse_from else ""),
                "warehouse_to": (m.warehouse_to.name
                                 if m.warehouse_to else ""),
                "user": (m.created_by.username
                         if m.created_by else ""),
                "comment": m.comment or "",
            })

        for l in op_lines:
            op = l.operation
            if l.direction == OperationDirection.FROM:
                g = "take"
                lbl = "Взял"
            elif l.direction == OperationDirection.TO:
                g = "put"
                lbl = "Положил"
            elif l.direction == OperationDirection.SCRAP:
                g = "scrap"
                lbl = "Брак"
            else:
                g = "other"
                lbl = l.direction

            events.append({
                "id": f"op-{l.id}",
                "kind": "operation",
                "group": g,
                "group_label": lbl,
                "created_at": op.created_at.isoformat(),
                "product_id": l.product_id,
                "product_article": l.product.article if l.product else "",
                "product_name": l.product.name if l.product else "",
                "quantity": str(l.qty),
                "container_id": l.container_id,
                "container_code": l.container.code if l.container else "",
                "warehouse_from": "",
                "warehouse_to": "",
                "user": (op.created_by.username
                         if op.created_by else ""),
                "comment": op.comment or "",
                "operation_type": op.get_operation_type_display(),
                "scrap_reason": (l.get_scrap_reason_display()
                                 if l.scrap_reason else ""),
            })

        # ── Фильтр по группе ───────────────────────────────────
        if group == "in":
            events = [e for e in events if e["group"] == "in"]
        elif group == "out":
            events = [e for e in events
                      if e["group"] in ("out", "scrap")]
        elif group == "move":
            events = [e for e in events if e["group"] == "move"]
        elif group == "operations":
            events = [e for e in events if e["kind"] == "operation"]

        # ── Поиск ──────────────────────────────────────────────
        if search:
            s = search.lower()
            events = [
                e for e in events
                if s in (e["container_code"] or "").lower()
                or s in (e["product_article"] or "").lower()
                or s in (e["product_name"] or "").lower()
                or s in (e["comment"] or "").lower()
                or s in (e["user"] or "").lower()
                or s in (e.get("operation_type") or "").lower()
            ]

        # ── Сортировка ─────────────────────────────────────────
        events.sort(key=lambda e: e["created_at"], reverse=True)

        # ── Итоги ──────────────────────────────────────────────
        total_in = sum(
            (Decimal(e["quantity"]) for e in events
             if e["group"] in ("in", "put")),
            Decimal("0"),
        )
        total_out = sum(
            (Decimal(e["quantity"]) for e in events
             if e["group"] in ("out", "scrap", "take")),
            Decimal("0"),
        )

        # ── Пагинация на фронте ────────────────────────────────
        total = len(events)
        limit = 5000
        events = events[:limit]

        return Response({
            "date_from": d_from.date().isoformat(),
            "date_to": (d_to - timedelta(days=1)).date().isoformat()
                       if d_to else None,
            "count": total,
            "shown": len(events),
            "incoming_total": str(total_in),
            "outgoing_total": str(total_out),
            "rows": events,
        })
