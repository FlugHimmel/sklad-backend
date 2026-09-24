"""
Смена состояния тары: одиночная и массовая.

POST /api/containers/<pk>/set-packed/         — одна тара
POST /api/containers/bulk-set-packed/         — много тар
"""
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Container, ContainerEvent, ContainerEventType
from .serializers import ContainerDetailSerializer


def _do_set_packed(c, packed: bool, user):
    """Меняет состояние. Возвращает None если ок, иначе строку ошибки."""
    if c.shipped_at is not None:
        return "Тара уже отгружена"
    if packed and (c.warehouse is None or c.warehouse.code != "MAIN"):
        return "Только на основном складе"
    if c.lines.count() == 0:
        return "Тара пустая"

    old = c.packed_at is not None
    if packed == old:
        return None  # уже так

    c.packed_at = timezone.now() if packed else None
    c.save(update_fields=["packed_at", "updated_at"])

    ContainerEvent.objects.create(
        container=c,
        event_type=ContainerEventType.ADJUSTED,
        comment=("Отмечена как упакованная" if packed
                 else "Снята отметка упаковки"),
        created_by=user,
    )
    return None


class ContainerSetPackedView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            c = Container.objects.get(pk=pk)
        except Container.DoesNotExist:
            return Response({"error": "Тара не найдена"},
                            status=status.HTTP_404_NOT_FOUND)

        packed = bool(request.data.get("packed", False))
        err = _do_set_packed(c, packed, request.user)
        if err:
            return Response({"error": err},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(ContainerDetailSerializer(c).data)


class ContainerBulkSetPackedView(APIView):
    """POST /api/containers/bulk-set-packed/
    Body: {"ids": [1,2,3], "packed": true}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ids = request.data.get("ids") or []
        packed = bool(request.data.get("packed", False))

        if not isinstance(ids, list) or not ids:
            return Response({"error": "ids обязателен (непустой список)"},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(ids) > 500:
            return Response({"error": "Максимум 500 тар за раз"},
                            status=status.HTTP_400_BAD_REQUEST)

        updated = []
        skipped = []
        errors = []

        containers = {c.id: c for c in Container.objects.filter(pk__in=ids)}
        for cid in ids:
            c = containers.get(cid)
            if not c:
                errors.append({"id": cid, "error": "не найдена"})
                continue
            err = _do_set_packed(c, packed, request.user)
            if err == "already set" or err is None:
                updated.append(c.code)
            else:
                skipped.append({"code": c.code, "reason": err})

        return Response({
            "updated": len(updated),
            "codes": updated[:50],
            "skipped": skipped[:20],
            "errors": errors[:20],
        })
