from rest_framework import serializers

from .models import BOM, BOMLine, Category, Product


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = "__all__"


class ProductListSerializer(serializers.ModelSerializer):
    product_type_display = serializers.CharField(
        source="get_product_type_display", read_only=True
    )
    uom_display = serializers.CharField(source="get_uom_display", read_only=True)
    alias_of_article = serializers.CharField(
        source="alias_of.article", read_only=True, default=None
    )
    aliases_count = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "article",
            "name",
            "product_type",
            "product_type_display",
            "category",
            "uom",
            "uom_display",
            "weight_g",
            "min_stock",
            "is_active",
            "mo1", "mo2", "mo3", "mo4", "mo5", "mo6", "mo7", "mo8",
            "alias_of",
            "alias_of_article",
            "aliases_count",
        )

    def get_aliases_count(self, obj):
        """Сколько дублей привязано к этому продукту (если главный)."""
        if obj.alias_of_id:
            return 0  # сам является дублем — у него дублей быть не может
        return obj.aliases.count()


class ProductDetailSerializer(serializers.ModelSerializer):
    product_type_display = serializers.CharField(
        source="get_product_type_display", read_only=True
    )
    uom_display = serializers.CharField(source="get_uom_display", read_only=True)
    alias_of_article = serializers.CharField(
        source="alias_of.article", read_only=True, default=None
    )
    alias_of_name = serializers.CharField(
        source="alias_of.name", read_only=True, default=None
    )
    aliases = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = "__all__"

    def get_aliases(self, obj):
        """Список дублей этого продукта (если он главный)."""
        if obj.alias_of_id:
            return []  # сам является дублем — дублей у него быть не может
        return [
            {
                "id": a.id,
                "article": a.article,
                "name": a.name,
                "is_active": a.is_active,
            }
            for a in obj.aliases.all().order_by("article")
        ]


class BOMLineSerializer(serializers.ModelSerializer):
    component_article = serializers.CharField(source="component.article", read_only=True)
    component_name = serializers.CharField(source="component.name", read_only=True)

    class Meta:
        model = BOMLine
        fields = (
            "id",
            "bom",
            "component",
            "component_article",
            "component_name",
            "quantity",
            "note",
        )


class BOMSerializer(serializers.ModelSerializer):
    lines = BOMLineSerializer(many=True, read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = BOM
        fields = (
            "id",
            "product",
            "product_article",
            "product_name",
            "version",
            "is_active",
            "note",
            "created_at",
            "lines",
        )
