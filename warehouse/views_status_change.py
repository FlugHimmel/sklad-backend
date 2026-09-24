"""
Смена статуса тары вручную.
POST /api/containers/{pk}/set-status/
body: {"status": "waiting_milling" | "waiting_packing" | "ready_to_ship" | "warehouse"}
"""
from rest_framework import status as drf_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Container, ContainerEvent, ContainerEventType, ContainerStatus,
)
from .serializers import ContainerDetailSerializer


ALLOWED_NEW_STATUSES = {
    ContainerStatus.WAREHOUSE,
    ContainerStatus.WAITING_MILLING,
    ContainerStatus.WAITING_PACKING,
    ContainerStatus.READY_TO_SHIP,
}


class ContainerSetStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            c = Container.objects.get(pk=pk)
        except Container.DoesNotExist:
            return Response({"error": "Тара не найдена"},
                            status=drf_status.HTTP_404_NOT_FOUND)

        new_status = str(request.data.get("status", "")).strip()
        if new_status not in ALLOWED_NEW_STATUSES:
            return Response(
                {"error": f"Недопустимый статус: «{new_status}». "
                          f"Можно: {sorted(ALLOWED_NEW_STATUSES)}"},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        if new_status == c.status:
            return Response({"error": "Такой статус уже стоит"},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        if c.status == ContainerStatus.SHIPPED:
            return Response({"error": "Отгруженную тару менять нельзя"},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        old_display = c.get_status_display()
        c.status = new_status
        c.save(update_fields=["status", "updated_at"])

        ContainerEvent.objects.create(
            container=c,
            event_type=ContainerEventType.STATUS_CHANGED,
            comment=f"Статус: {old_display} → {c.get_status_display()}",
            created_by=request.user,
        )

        return Response(ContainerDetailSerializer(c).data)
