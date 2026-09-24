from django.contrib import admin

from .models import BOM, BOMLine, Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "parent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    autocomplete_fields = ("parent",)


class BOMLineInline(admin.TabularInline):
    model = BOMLine
    extra = 1
    autocomplete_fields = ("component",)


@admin.register(BOM)
class BOMAdmin(admin.ModelAdmin):
    list_display = ("product", "version", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("product__article", "product__name")
    autocomplete_fields = ("product",)
    inlines = [BOMLineInline]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "article",
        "name",
        "product_type",
        "uom",
        "weight_g",
        "min_stock",
        "is_active",
    )
    list_filter = ("product_type", "uom", "is_active")
    search_fields = ("article", "name")
    autocomplete_fields = ("category", "mo1", "mo2", "mo3", "mo4")
    fieldsets = (
        (None, {"fields": ("article", "name", "product_type", "category", "is_active")}),
        ("Единицы и вес", {"fields": ("uom", "weight_g", "min_stock")}),
        (
            "Слоты МО (только для типа «Заготовка / литьё»)",
            {
                "fields": ("mo1", "mo2", "mo3", "mo4"),
                "description": (
                    "Заполняйте только для артикулов типа «Заготовка / литьё». "
                    "Каждый слот — деталь-вариант, которую можно изготовить из этой отливки."
                ),
            },
        ),
    )
