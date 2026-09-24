from django.contrib import admin

from .models import (
    Container,
    ContainerEvent,
    Inventory,
    InventoryLine,
    Movement,
    Operation,
    OperationLine,
    Stock,
    Warehouse,
)


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ("warehouse", "product", "quantity", "updated_at")
    list_filter = ("warehouse", "product__product_type")
    search_fields = ("product__article", "product__name")
    autocomplete_fields = ("warehouse", "product")


class ContainerEventInline(admin.TabularInline):
    model = ContainerEvent
    extra = 0
    can_delete = False
    readonly_fields = (
        "event_type",
        "product",
        "quantity",
        "order",
        "movement",
        "comment",
        "created_at",
        "created_by",
    )

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Container)
class ContainerAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "product",
        "quantity",
        "status",
        "warehouse",
        "order",
        "updated_at",
    )
    list_filter = ("status", "warehouse")
    search_fields = ("code", "product__article", "product__name")
    autocomplete_fields = ("product", "warehouse", "order")
    readonly_fields = ("code", "created_at", "updated_at")
    inlines = [ContainerEventInline]


@admin.register(Movement)
class MovementAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "movement_type",
        "product",
        "quantity",
        "warehouse_from",
        "warehouse_to",
        "container",
        "order",
    )
    list_filter = ("movement_type", "warehouse_from", "warehouse_to")
    search_fields = ("product__article", "product__name", "container__code", "comment")
    autocomplete_fields = ("product", "warehouse_from", "warehouse_to", "container", "order")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"


class InventoryLineInline(admin.TabularInline):
    model = InventoryLine
    extra = 1
    autocomplete_fields = ("product",)
    readonly_fields = ("difference",)

    def difference(self, obj):
        return obj.difference
    difference.short_description = "Расхождение"


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ("id", "warehouse", "status", "started_at", "completed_at")
    list_filter = ("status", "warehouse")
    inlines = [InventoryLineInline]
    readonly_fields = ("started_at",)


@admin.register(ContainerEvent)
class ContainerEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "container", "event_type", "product", "quantity", "order")
    list_filter = ("event_type",)
    search_fields = ("container__code", "product__article", "comment")
    autocomplete_fields = ("container", "product", "order", "movement")
    readonly_fields = ("created_at",)


class OperationLineInline(admin.TabularInline):
    model = OperationLine
    extra = 0
    readonly_fields = ("direction", "container", "product", "qty", "scrap_reason")


@admin.register(Operation)
class OperationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "created_at",
        "operation_type",
        "created_by",
        "comment",
    )
    list_filter = ("operation_type", "created_at")
    search_fields = ("comment", "lines__product__article", "lines__container__code")
    readonly_fields = ("created_at",)
    inlines = [OperationLineInline]
    date_hierarchy = "created_at"
