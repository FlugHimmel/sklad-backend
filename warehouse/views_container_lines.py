"""
API: правка содержимого тары + перекладывание между тарами.
"""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import Product

from .models import Container, ContainerLine
from .serializers import ContainerDetailSerializer
from .services import ServiceError
from .services_container_lines import (
    add_line,
    set_line_quantity,
    transfer_between_containers,
)


def _get_container(pk):
    try:
        return Container.objects.get(pk=pk)
    except Container.DoesNotExist:
        return None


class ContainerLineSetQuantityView(APIView):
    """POST /api/containers/{pk}/lines/{line_id}/set/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, line_id):
        container = _get_container(pk)
        if container is None:
            return Response({"error": "Тара не найдена"},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            line = ContainerLine.objects.get(pk=line_id, container=container)
        except ContainerLine.DoesNotExist:
            return Response({"error": "Строка не найдена"},
                            status=status.HTTP_404_NOT_FOUND)

        qty = request.data.get("quantity")
        if qty is None:
            return Response({"error": "quantity обязателен"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            set_line_quantity(
                container=container, line=line, quantity=qty,
                user=request.user, comment=request.data.get("comment", ""),
            )
        except ServiceError as exc:
            return Response({"error": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)

        container.refresh_from_db()
        return Response(ContainerDetailSerializer(container).data)


class ContainerLineAddView(APIView):
    """POST /api/containers/{pk}/lines/add/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        container = _get_container(pk)
        if container is None:
            return Response({"error": "Тара не найдена"},
                            status=status.HTTP_404_NOT_FOUND)

        pid = request.data.get("product_id")
        qty = request.data.get("quantity")
        if not pid or qty is None:
            return Response({"error": "product_id и quantity обязательны"},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            product = Product.objects.get(pk=pid)
        except Product.DoesNotExist:
            return Response({"error": "Артикул не найден"},
                            status=status.HTTP_404_NOT_FOUND)
        try:
            add_line(
                container=container, product=product, quantity=qty,
                user=request.user, comment=request.data.get("comment", ""),
            )
        except ServiceError as exc:
            return Response({"error": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)

        container.refresh_from_db()
        return Response(ContainerDetailSerializer(container).data)


class ContainerTransferToView(APIView):
    """
    POST /api/containers/{pk}/transfer-to/

    Body:
      {
        "to_code": "TARA-000XXX",     # или
        "to_container_id": N,
        "lines": [{"product_id": N, "quantity": "X"}],
        "comment": ""
      }

    Возвращает:
      {
        "from": {...},   # сериализатор тары-источника после операции
        "to": {...},     # сериализатор тары-приёмника после операции
        "moved": [{"product_id": N, "article": "...", "quantity": "X"}]
      }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        container_from = _get_container(pk)
        if container_from is None:
            return Response({"error": "Тара-источник не найдена"},
                            status=status.HTTP_404_NOT_FOUND)

        to_id = request.data.get("to_container_id")
        to_code = (request.data.get("to_code") or "").strip()

        container_to = None
        if to_id:
            container_to = _get_container(to_id)
        elif to_code:
            container_to = Container.objects.filter(code=to_code).first()

        if container_to is None:
            return Response({"error": "Тара-приёмник не найдена"},
                            status=status.HTTP_404_NOT_FOUND)

        lines = request.data.get("lines") or []
        if not lines:
            return Response({"error": "lines пуст"},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            moved = transfer_between_containers(
                container_from=container_from,
                container_to=container_to,
                lines=lines,
                user=request.user,
                comment=request.data.get("comment", ""),
            )
        except ServiceError as exc:
            return Response({"error": str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)

        container_from.refresh_from_db()
        container_to.refresh_from_db()
        return Response({
            "from": ContainerDetailSerializer(container_from).data,
            "to": ContainerDetailSerializer(container_to).data,
            "moved": moved,
        })
