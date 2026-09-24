from rest_framework import serializers

from .models import Order, OrderLine, OrderStatusHistory


class OrderLineSerializer(serializers.ModelSerializer):
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    casting_article = serializers.CharField(source="casting.article", read_only=True)

    order_number = serializers.CharField(source="order.number", read_only=True)
    order_status = serializers.CharField(source="order.status", read_only=True)
    order_status_display = serializers.CharField(
        source="order.get_status_display", read_only=True)
    order_due_date = serializers.DateField(source="order.due_date", read_only=True)
    order_customer = serializers.CharField(source="order.customer", read_only=True)

    class Meta:
        model = OrderLine
        fields = (
            "id", "order",
            "order_number", "order_status", "order_status_display",
            "order_due_date", "order_customer",
            "product", "product_article", "product_name",
            "casting", "casting_article",
            "name", "quantity_planned", "quantity_done", "quantity_shipped",
            "sequence", "machine", "ready_date", "shipped_date",
            "reserve_qty", "places", "weight_g",
            "comment",
        )
        read_only_fields = ("quantity_done", "quantity_shipped")
        extra_kwargs = {"order": {"required": False}}


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    changed_by_username = serializers.CharField(source="changed_by.username", read_only=True)
    status_from_display = serializers.CharField(source="get_status_from_display", read_only=True)
    status_to_display = serializers.CharField(source="get_status_to_display", read_only=True)

    class Meta:
        model = OrderStatusHistory
        fields = "__all__"


class OrderListSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    lines_count = serializers.IntegerField(source="lines.count", read_only=True)

    class Meta:
        model = Order
        fields = ("id", "number", "kind", "kind_display", "customer", "status",
                  "status_display", "due_date", "created_at", "updated_at", "lines_count")


class OrderDetailSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    lines = OrderLineSerializer(many=True, read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = (
            "id", "number", "kind", "kind_display", "customer", "status",
            "status_display", "due_date", "comment", "created_at", "updated_at",
            "created_by", "lines", "status_history",
        )
        read_only_fields = ("created_at", "updated_at", "created_by")


class OrderCreateSerializer(serializers.Serializer):
    """POST /api/orders/ — создание заказа сразу со строками."""
    number = serializers.CharField(max_length=64)
    kind = serializers.CharField(required=False, default="production")
    customer = serializers.CharField(required=False, allow_blank=True, default="")
    status = serializers.CharField(required=False, default="new")
    due_date = serializers.DateField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True, default="")
    lines = OrderLineSerializer(many=True)
