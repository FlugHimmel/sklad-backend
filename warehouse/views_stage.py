"""
Вьюха для операции «Принять от изготовителя» / «Вывести с работы».
POST /api/containers/{pk}/finish-stage/
"""
from decimal import Decimal

from rest_framework import status as drf_status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from inventory.models import Product

from .models import Container, ContainerStatus, Warehouse
from .serializers import (
    ContainerDetailSerializer, ContainerFinishStageSerializer,
)
from .services import ServiceError
from .services_stage import container_finish_stage


class ContainerFinishStageView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            source = Container.objects.get(pk=pk)
        except Container.DoesNotExist:
            return Response(
                {"error": "Тара не найдена"},
                status=drf_status.HTTP_404_NOT_FOUND,
            )

        ser = ContainerFinishStageSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        # Результат (деталь)
        result_product = None
        if data.get("result_product_id"):
            try:
                result_product = Product.objects.get(
                    pk=data["result_product_id"])
            except Product.DoesNotExist:
                return Response(
                    {"error": "Деталь не найдена"},
                    status=drf_status.HTTP_404_NOT_FOUND,
                )

        # Склад-приёмник
        try:
            warehouse = Warehouse.objects.get(pk=data["warehouse_id"])
        except Warehouse.DoesNotExist:
            return Response(
                {"error": "Склад не найден"},
                status=drf_status.HTTP_404_NOT_FOUND,
            )

        # Склад для задела (необязательно)
        reserve_warehouse = None
        if data.get("reserve_warehouse_id"):
            try:
                reserve_warehouse = Warehouse.objects.get(
                    pk=data["reserve_warehouse_id"])
            except Warehouse.DoesNotExist:
                return Response(
                    {"error": "Склад задела не найден"},
                    status=drf_status.HTTP_404_NOT_FOUND,
                )

        def build_entries(raw):
            entries = []
            for item in raw or []:
                cid = item.get("container_id")
                qty = item.get("quantity")
                if qty is None:
                    continue
                if cid is None:
                    c = Container()
                else:
                    try:
                        c = Container.objects.get(pk=cid)
                    except Container.DoesNotExist:
                        return None, f"Тара-приёмник {cid} не найдена"
                entries.append({
                    "container": c,
                    "quantity": Decimal(str(qty)),
                })
            return entries, None

        good_entries, err = build_entries(data.get("good_entries"))
        if err:
            return Response({"error": err},
                            status=drf_status.HTTP_404_NOT_FOUND)

        reserve_entries, err = build_entries(data.get("reserve_entries"))
        if err:
            return Response({"error": err},
                            status=drf_status.HTTP_404_NOT_FOUND)

        next_status = (data.get("next_status")
                       or ContainerStatus.READY_TO_SHIP)

        try:
            source, ids = container_finish_stage(
                source=source,
                result_product=result_product,
                qty_total=data["qty_total"],
                good_entries=good_entries,
                reserve_entries=reserve_entries,
                next_status=next_status,
                warehouse=warehouse,
                reserve_warehouse=reserve_warehouse,
                comment=data.get("comment", ""),
                user=request.user,
            )
        except ServiceError as exc:
            return Response(
                {"error": str(exc)},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "source": ContainerDetailSerializer(source).data,
            "result_container_ids": ids,
        }, status=drf_status.HTTP_200_OK)
