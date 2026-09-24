"""
Пустые тары (болванки ШК):
- список пустых тар для выбора в производстве
- массовое создание пустых тар
"""
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Container, ContainerStatus, Warehouse
from .serializers import ContainerListSerializer


class EmptyContainersView(APIView):
    """
    GET /api/containers/empty/
    Список пустых тар (болванок ШК), которые ещё не использованы.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .views import _user_is_foundry  # noqa

        qs = (Container.objects
              .filter(quantity=0)
              .filter(status__in=[ContainerStatus.EMPTY,
                                  ContainerStatus.WAREHOUSE])
              .select_related("warehouse")
              .order_by("code"))

        # Изоляция по роли
        if _user_is_foundry(request.user):
            qs = qs.filter(warehouse__code="ZLK")
        else:
            qs = qs.exclude(warehouse__code="ZLK")

        wh = request.query_params.get("warehouse")
        if wh and not _user_is_foundry(request.user):
            qs = qs.filter(warehouse_id=wh)

        search = request.query_params.get("search")
        if search:
            qs = qs.filter(code__icontains=search)

        items = list(qs[:1000])
        data = ContainerListSerializer(items, many=True).data
        return Response({"count": len(data), "items": data})


class BulkCreateEmptyView(APIView):
    """
    POST /api/containers/bulk-create-empty/
    Body: {"count": 200, "warehouse_id": N, "note": "Болванки 15.09"}
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        count = request.data.get("count")
        wh_id = request.data.get("warehouse_id")
        note = (request.data.get("note") or "").strip()

        try:
            count = int(count)
        except (TypeError, ValueError):
            return Response({"error": "count должен быть числом"},
                            status=status.HTTP_400_BAD_REQUEST)

        if count < 1 or count > 500:
            return Response({"error": "count от 1 до 500"},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            wh = Warehouse.objects.get(pk=wh_id)
        except Warehouse.DoesNotExist:
            return Response({"error": "Склад не найден"},
                            status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            created = []
            for _ in range(count):
                c = Container.objects.create(
                    status=ContainerStatus.EMPTY,
                    warehouse=wh,
                    note=note,
                )
                created.append(c)

        return Response({
            "count": len(created),
            "created": [{"id": c.id, "code": c.code} for c in created],
        })
