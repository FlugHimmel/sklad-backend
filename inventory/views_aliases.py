"""
Управление дублями артикулов.

Один продукт может быть "дублем" другого (alias_of).
Все отчёты и агрегация идут по главному артикулу.
"""
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Product
from .serializers import ProductDetailSerializer


def _find_ultimate_main(product: Product) -> Product:
    """Возвращает самого верхнего главного в цепочке."""
    seen = set()
    current = product
    while current.alias_of_id:
        if current.id in seen:
            break  # защита от цикла (не должно быть)
        seen.add(current.id)
        current = current.alias_of
    return current


class ProductLinkAliasView(APIView):
    """POST /api/products/<int:pk>/link-alias/

    Привязать этот продукт как дубль к главному.

    Body:
      {"main_id": N}       — по ID
      {"main_article": "X"} — по артикулу
    """
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        try:
            product = Product.objects.get(pk=pk)
        except Product.DoesNotExist:
            return Response({"error": "Артикул не найден"},
                            status=status.HTTP_404_NOT_FOUND)

        main_id = request.data.get("main_id")
        main_article = (request.data.get("main_article") or "").strip()

        main = None
        if main_id:
            main = Product.objects.filter(pk=main_id).first()
        elif main_article:
            # Ищем по главному артикулу или по его дублю
            main = Product.objects.filter(article__iexact=main_article).first()
            if main is None:
                main = Product.objects.filter(
                    aliases__article__iexact=main_article
                ).first()

        if not main:
            return Response(
                {"error": "Главный артикул не найден"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if main.id == product.id:
            return Response(
                {"error": "Нельзя привязать артикул к самому себе"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Если main сам является дублем — идём к его главному
        main = _find_ultimate_main(main)

        # Если после разворота main == product — цикл
        if main.id == product.id:
            return Response(
                {"error": "Циклическая ссылка"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Если у product есть свои дубли — они переезжают под нового главного
        for child in product.aliases.all():
            child.alias_of = main
            child.save(update_fields=["alias_of", "updated_at"])

        product.alias_of = main
        product.save(update_fields=["alias_of", "updated_at"])

        return Response(ProductDetailSerializer(product).data)


class ProductUnlinkAliasView(APIView):
    """POST /api/products/<int:pk>/unlink-alias/

    Отвязать этот продукт от главного — он становится самостоятельным.
    """
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        try:
            product = Product.objects.get(pk=pk)
        except Product.DoesNotExist:
            return Response({"error": "Артикул не найден"},
                            status=status.HTTP_404_NOT_FOUND)

        product.alias_of = None
        product.save(update_fields=["alias_of", "updated_at"])

        return Response(ProductDetailSerializer(product).data)


class ProductResolveView(APIView):
    """GET /api/products/resolve/?article=X

    Найти продукт по любому артикулу (главному или дублю) и вернуть главный.
    Полезно для случаев когда оператор ввёл старый артикул руками.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        article = (request.query_params.get("article") or "").strip()
        if not article:
            return Response({"error": "article обязателен"},
                            status=status.HTTP_400_BAD_REQUEST)

        # Ищем: сначала прямой, потом через дубли
        p = Product.objects.filter(article__iexact=article).first()
        if p is None:
            p = Product.objects.filter(
                aliases__article__iexact=article
            ).first()

        if p is None:
            return Response(
                {"error": f"Артикул «{article}» не найден"},
                status=status.HTTP_404_NOT_FOUND,
            )

        main = _find_ultimate_main(p)
        return Response({
            "input_article": article,
            "product": ProductDetailSerializer(main).data,
            "resolved_from": (p.id if p.id != main.id else None),
        })
