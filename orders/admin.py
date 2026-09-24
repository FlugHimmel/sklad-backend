from django.contrib import admin

from .models import Order, OrderLine, OrderStatusHistory


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 1
    autocomplete_fields = ("product", "casting")
    fields = (
        "product",
        "casting",
        "name",
        "quantity_planned",
        "quantity_done",
        "quantity_shipped",
        "comment",
    )


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("status_from", "status_to", "changed_at", "changed_by", "comment")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "kind",
        "customer",
        "status",
        "due_date",
        "created_at",
    )
    list_filter = ("kind", "status")
    search_fields = ("number", "customer", "lines__product__article")
    date_hierarchy = "created_at"
    inlines = [OrderLineInline, OrderStatusHistoryInline]
    readonly_fields = ("created_at", "updated_at")
