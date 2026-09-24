from rest_framework import serializers

from .models import (
    Container, ContainerEvent, ContainerLine, Inventory, InventoryLine,
    Movement, ProductionRun, ProductionRunResult, ScrapEntry, Stock, Warehouse,
)


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = "__all__"


class StockSerializer(serializers.ModelSerializer):
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_type = serializers.CharField(source="product.product_type", read_only=True)
    uom = serializers.CharField(source="product.uom", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = Stock
        fields = "__all__"


class ContainerLineSerializer(serializers.ModelSerializer):
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_type = serializers.CharField(source="product.product_type", read_only=True)
    uom = serializers.CharField(source="product.uom", read_only=True)

    class Meta:
        model = ContainerLine
        fields = ("id", "product", "product_article", "product_name",
                  "product_type", "uom", "quantity")


class ContainerEventSerializer(serializers.ModelSerializer):
    event_type_display = serializers.CharField(source="get_event_type_display", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = ContainerEvent
        fields = "__all__"


class ContainerListSerializer(serializers.ModelSerializer):
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_type = serializers.CharField(source="product.product_type", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True)
    lines = ContainerLineSerializer(many=True, read_only=True)

    class Meta:
        model = Container
        fields = ("id", "code", "product", "product_article", "product_name", "product_type",
                  "quantity", "status", "status_display", "warehouse", "warehouse_name",
                  "order", "order_number", "note", "lines",
                  "created_at", "updated_at", "label_printed_at",
                  "packed_at", "shipped_at")


class ContainerDetailSerializer(serializers.ModelSerializer):
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_type = serializers.CharField(source="product.product_type", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True)
    lines = ContainerLineSerializer(many=True, read_only=True)
    events = ContainerEventSerializer(many=True, read_only=True)

    class Meta:
        model = Container
        fields = ("id", "code", "product", "product_article", "product_name", "product_type",
                  "quantity", "status", "status_display", "warehouse", "warehouse_name",
                  "order", "order_number", "note", "lines", "events",
                  "created_at", "updated_at", "label_printed_at",
                  "packed_at", "shipped_at")
        read_only_fields = ("created_at", "updated_at", "label_printed_at",
                  "packed_at", "shipped_at")


class MovementSerializer(serializers.ModelSerializer):
    movement_type_display = serializers.CharField(source="get_movement_type_display", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    container_code = serializers.CharField(source="container.code", read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True)
    production_run_id = serializers.IntegerField(source="production_run.id", read_only=True)
    warehouse_from_name = serializers.CharField(source="warehouse_from.name", read_only=True)
    warehouse_to_name = serializers.CharField(source="warehouse_to.name", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = Movement
        fields = "__all__"


class InventoryLineSerializer(serializers.ModelSerializer):
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    difference = serializers.SerializerMethodField()

    class Meta:
        model = InventoryLine
        fields = "__all__"

    def get_difference(self, obj):
        return obj.difference


class InventorySerializer(serializers.ModelSerializer):
    lines = InventoryLineSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Inventory
        fields = "__all__"
        read_only_fields = ("started_at", "completed_at", "created_by")


class ScrapEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapEntry
        fields = ("id", "reason", "quantity", "production_result")


class ProductionRunResultSerializer(serializers.ModelSerializer):
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    scrap_entries = ScrapEntrySerializer(many=True, read_only=True)

    class Meta:
        model = ProductionRunResult
        fields = ("id", "product", "product_article", "product_name",
                  "qty_good", "qty_scrap", "comment", "scrap_entries",
                  "created_at")


class ProductionRunListSerializer(serializers.ModelSerializer):
    source_product_article = serializers.CharField(source="source_product.article", read_only=True)
    source_product_name = serializers.CharField(source="source_product.name", read_only=True)
    product_article = serializers.CharField(source="product.article", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    order_number = serializers.CharField(source="order.number", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    finished_by_username = serializers.CharField(source="finished_by.username", read_only=True)
    operator_username = serializers.CharField(source="operator.username", read_only=True)
    operator_full_name = serializers.SerializerMethodField()
    scrap_entries = ScrapEntrySerializer(many=True, read_only=True)
    results = ProductionRunResultSerializer(many=True, read_only=True)
    qty_unallocated = serializers.DecimalField(
        max_digits=14, decimal_places=3, read_only=True)

    class Meta:
        model = ProductionRun
        fields = ("id",
                  "source_product", "source_product_article", "source_product_name",
                  "product", "product_article", "product_name",
                  "qty_source_used", "qty_good", "qty_scrap", "qty_returned",
                  "qty_unallocated",
                  "qty_from_reserve", "qty_reserve",
                  "status", "status_display",
                  "order", "order_number", "comment",
                  "scrap_written_off", "scrap_written_off_at",
                  "operator", "operator_username", "operator_full_name",
                  "scrap_entries", "results",
                  "created_at", "finished_at",
                  "created_by_username", "finished_by_username")

    def get_operator_full_name(self, obj):
        if not obj.operator:
            return None
        full = obj.operator.get_full_name().strip()
        return full or obj.operator.username


class ProductionRunDetailSerializer(ProductionRunListSerializer):
    movements = MovementSerializer(many=True, read_only=True)

    class Meta(ProductionRunListSerializer.Meta):
        fields = ProductionRunListSerializer.Meta.fields + ("movements",)


class ProductionRunCreateSerializer(serializers.Serializer):
    source_containers = serializers.ListField(child=serializers.DictField(), allow_empty=False)
    result_product_id = serializers.IntegerField()
    qty_good = serializers.DecimalField(max_digits=14, decimal_places=3)
    qty_scrap = serializers.DecimalField(max_digits=14, decimal_places=3, required=False, default=0)
    scrap_reason = serializers.CharField(required=False, allow_blank=True, default="")
    scrap_entries = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    reserve_picks = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    result_containers = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    warehouse_id = serializers.IntegerField()
    order_id = serializers.IntegerField(required=False, allow_null=True)
    operator_id = serializers.IntegerField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class ProductionIssueSerializer(serializers.Serializer):
    source_containers = serializers.ListField(
        child=serializers.DictField(), allow_empty=False)
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class ProductionFinishSerializer(serializers.Serializer):
    results = serializers.ListField(
        child=serializers.DictField(), allow_empty=False)
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class ProductionProcessSerializer(serializers.Serializer):
    """
    POST /api/production/process/
    Атомарная обработка одной тары: issue + finish.

    body: {
      "source_container_id": N,
      "results": [{"product_id": M, "qty_good": "X", "container_id": K|null,
                   "next_status": "waiting_milling", "scrap_entries": [...]}, ...],
      "comment": "..."
    }
    """
    source_container_id = serializers.IntegerField()
    results = serializers.ListField(
        child=serializers.DictField(), allow_empty=False)
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class ContainerFinishStageSerializer(serializers.Serializer):
    qty_total = serializers.DecimalField(max_digits=14, decimal_places=3)
    result_product_id = serializers.IntegerField(required=False, allow_null=True)
    good_entries = serializers.ListField(
        child=serializers.DictField(), required=False, default=list)
    reserve_entries = serializers.ListField(
        child=serializers.DictField(), required=False, default=list)
    next_status = serializers.CharField(required=False, allow_blank=True, default="")
    warehouse_id = serializers.IntegerField()
    reserve_warehouse_id = serializers.IntegerField(required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class ShipmentNoteLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = __import__("warehouse.models", fromlist=["ShipmentNoteLine"]).ShipmentNoteLine
        fields = (
            "id", "code", "product_article", "product_name", "uom",
            "quantity", "weight_kg", "sequence",
        )


class ShipmentNoteSerializer(serializers.ModelSerializer):
    lines = ShipmentNoteLineSerializer(many=True, read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True)

    class Meta:
        model = __import__("warehouse.models", fromlist=["ShipmentNote"]).ShipmentNote
        fields = (
            "id", "number", "year", "seq", "note_date",
            "from_name", "from_address", "to_name", "to_address",
            "total_qty", "total_weight_kg", "comment",
            "created_at", "created_by", "created_by_username",
            "lines",
        )
        read_only_fields = ("created_at", "created_by")

