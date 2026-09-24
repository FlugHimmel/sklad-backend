from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import BOMLineViewSet, BOMViewSet, CategoryViewSet, ProductViewSet
from .views_aliases import (
    ProductLinkAliasView,
    ProductResolveView,
    ProductUnlinkAliasView,
)

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="categories")
router.register("products", ProductViewSet, basename="products")
router.register("boms", BOMViewSet, basename="boms")
router.register("bom-lines", BOMLineViewSet, basename="bom-lines")

# ВАЖНО: явные пути — ДО router.urls, иначе 'resolve' матчнется как pk
urlpatterns = [
    path("products/resolve/",
         ProductResolveView.as_view(),
         name="product-resolve"),
    path("products/<int:pk>/link-alias/",
         ProductLinkAliasView.as_view(),
         name="product-link-alias"),
    path("products/<int:pk>/unlink-alias/",
         ProductUnlinkAliasView.as_view(),
         name="product-unlink-alias"),
] + router.urls
