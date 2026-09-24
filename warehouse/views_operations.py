"""
Views для новых операций (Operation / OperationLine).
Не трогают существующий views.py.

Изоляция по ролям (Вариант X — по складам тар):
  * foundry (литейка) видит только операции, где участвует тара из ZLK
  * admin видит MAIN + RESERVE (исключая чисто-ZLK операции)
  * user и прочие роли видят всё
"""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from warehouse.models import (
    Container,
    Operation,
    OperationLine,
    OperationType,
    ScrapReason,
)
from warehouse.serializers_operations import OperationSerializer
from warehouse.services_operations import (
    create_operation,
    rollback_operation,
    OperationError,
)


def _filter_operations_for_user(qs, user):
    """
    Фильтр операций по роли пользователя.
      * foundry — только если хоть одна тара в операции на складе ZLK
      * admin   — исключаем операции, где ВСЕ тары на ZLK
      * user и все остальные — без фильтра (видят всё)
    """
    role = getattr(user, 'role', None)
    if role == 'foundry':
        return qs.filter(
            lines__container__warehouse__code='ZLK'
        ).distinct()
    if role == 'admin':
        return qs.exclude(
            lines__container__warehouse__code='ZLK'
        ).distinct()
    return qs


# ---------------------------------------------------------------------------
# Meta — справочники для фронта (типы операций, причины брака)
# ---------------------------------------------------------------------------
class OperationMetaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "operation_types": [
                {"value": v, "label": l} for v, l in OperationType.choices
            ],
            "scrap_reasons": [
                {"value": v, "label": l} for v, l in ScrapReason.choices
            ],
        })


# ---------------------------------------------------------------------------
# POST /api/operations/ — создать операцию
# GET  /api/operations/ — список операций
# ---------------------------------------------------------------------------
class OperationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (Operation.objects
              .select_related("created_by", "order")
              .prefetch_related("lines__container", "lines__product")
              .order_by("-created_at"))
        qs = _filter_operations_for_user(qs, request.user)

        ot = request.query_params.get("operation_type")
        if ot:
            qs = qs.filter(operation_type=ot)
        try:
            limit = int(request.query_params.get("limit", "100"))
        except ValueError:
            limit = 100
        limit = max(1, min(500, limit))
        total = qs.count()
        data = OperationSerializer(qs[:limit], many=True).data
        return Response({"rows": data, "count": total})

    def post(self, request):
        data = request.data or {}
        try:
            op = create_operation(
                operation_type=data.get("operation_type", ""),
                from_lines=data.get("from") or [],
                to_lines=data.get("to") or [],
                scrap_lines=data.get("scrap") or [],
                user=request.user,
                comment=data.get("comment", "") or "",
            )
        except OperationError as exc:
            return Response({"error": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)

        op = (Operation.objects
              .select_related("created_by", "order")
              .prefetch_related("lines__container", "lines__product")
              .get(pk=op.pk))
        return Response(OperationSerializer(op).data,
                        status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# GET /api/operations/{id}/ — детали
# ---------------------------------------------------------------------------
class OperationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        qs = (Operation.objects
              .select_related("created_by", "order")
              .prefetch_related("lines__container", "lines__product"))
        qs = _filter_operations_for_user(qs, request.user)
        try:
            op = qs.get(pk=pk)
        except Operation.DoesNotExist:
            return Response({"error": "Операция не найдена"},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(OperationSerializer(op).data)


# ---------------------------------------------------------------------------
# POST /api/operations/{id}/rollback/ — откат операции
# ---------------------------------------------------------------------------
class OperationRollbackView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        qs = _filter_operations_for_user(Operation.objects.all(), request.user)
        try:
            op = qs.get(pk=pk)
        except Operation.DoesNotExist:
            return Response({"error": "Операция не найдена"},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            rollback_operation(op)
        except OperationError as exc:
            return Response({"error": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": "ok", "deleted_id": pk})


# ---------------------------------------------------------------------------
# GET /api/containers/{id}/op-history/ — история тары через операции
# ---------------------------------------------------------------------------
class ContainerOpHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            c = Container.objects.get(pk=pk)
        except Container.DoesNotExist:
            return Response({"error": "Тара не найдена"},
                            status=status.HTTP_404_NOT_FOUND)

        lines = (OperationLine.objects
                 .filter(container=c)
                 .select_related("operation",
                                 "operation__created_by",
                                 "product")
                 .order_by("-operation__created_at", "-id"))

        rows = []
        for l in lines:
            op = l.operation
            rows.append({
                "operation_id": op.id,
                "operation_type": op.operation_type,
                "operation_type_display": op.get_operation_type_display(),
                "created_at": op.created_at.isoformat(),
                "created_by_username": (op.created_by.username
                                        if op.created_by else None),
                "direction": l.direction,
                "direction_display": l.get_direction_display(),
                "product_id": l.product_id,
                "product_article": l.product.article,
                "product_name": l.product.name,
                "qty": str(l.qty),
                "scrap_reason": l.scrap_reason or "",
                "scrap_reason_display": (l.get_scrap_reason_display()
                                         if l.scrap_reason else ""),
                "comment": op.comment or "",
            })

        return Response({
            "container_id": c.id,
            "container_code": c.code,
            "rows": rows,
            "count": len(rows),
        })
