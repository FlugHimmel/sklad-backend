from django.db.models import ProtectedError
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import BOM, BOMLine, Category, Product
from .serializers import (
    BOMLineSerializer,
    BOMSerializer,
    CategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ("name", "code")
    ordering_fields = ("name",)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            self.perform_destroy(instance)
        except ProtectedError as e:
            names = ", ".join(
                f"{obj._meta.verbose_name} «{obj}»"
                for obj in list(e.protected_objects)[:5]
            )
            return Response(
                {"error": f"Нельзя удалить: используется в {names}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {"error": f"Ошибка удаления: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductViewSet(viewsets.ModelViewSet):
    queryset = (
        Product.objects.select_related("category", "mo1", "mo2", "mo3", "mo4").all()
    )
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ("article", "name")
    ordering_fields = ("article", "name", "created_at")

    def get_serializer_class(self):
        if self.action == "list":
            return ProductListSerializer
        return ProductDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        ptype = self.request.query_params.get("product_type")
        if ptype:
            qs = qs.filter(product_type=ptype)
        active = self.request.query_params.get("is_active")
        if active in ("1", "true", "True"):
            qs = qs.filter(is_active=True)
        return qs

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            self.perform_destroy(instance)
        except ProtectedError as e:
            names = ", ".join(
                f"{obj._meta.verbose_name} «{obj}»"
                for obj in list(e.protected_objects)[:5]
            )
            return Response(
                {
                    "error": (
                        f"Нельзя удалить «{instance.article}» — "
                        f"он используется в: {names}. "
                        f"Попробуйте «Деактивировать»."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {"error": f"Ошибка удаления: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="casting-parts")
    def casting_parts(self, request, pk=None):
        """
        Возвращает детали, которые можно сделать из этой отливки (Mo1..Mo4).
        Если у отливки ни один слот не задан — возвращает пустой список.
        """
        product = self.get_object()
        ids = [product.mo1_id, product.mo2_id, product.mo3_id, product.mo4_id]
        ids = [i for i in ids if i]
        qs = Product.objects.filter(id__in=ids, is_active=True).order_by("article")
        return Response(
            {
                "casting_id": product.id,
                "casting_article": product.article,
                "parts": ProductListSerializer(qs, many=True).data,
            }
        )


class BOMViewSet(viewsets.ModelViewSet):
    queryset = (
        BOM.objects.select_related("product")
        .prefetch_related("lines__component")
        .all()
    )
    serializer_class = BOMSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ("product__article", "product__name")


class BOMLineViewSet(viewsets.ModelViewSet):
    queryset = BOMLine.objects.select_related("bom", "component").all()
    serializer_class = BOMLineSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        bom_id = self.request.query_params.get("bom")
        if bom_id:
            qs = qs.filter(bom_id=bom_id)
        return qs
