"""
Сериализаторы для новых операций (Operation / OperationLine).
Не трогают существующие сериализаторы из serializers.py.
"""
from decimal import Decimal

from rest_framework import serializers

from warehouse.models import (
    Operation,
    OperationLine,
    OperationDirection,
)


class OperationLineSerializer(serializers.ModelSerializer):
    direction_display = serializers.CharField(
        source="get_direction_display", read_only=True)
    scrap_reason_display = serializers.CharField(
        source="get_scrap_reason_display", read_only=True)
    container_code = serializers.CharField(
        source="container.code", read_only=True, default=None)
    product_article = serializers.CharField(
        source="product.article", read_only=True)
    product_name = serializers.CharField(
        source="product.name", read_only=True)
    qty = serializers.DecimalField(
        max_digits=14, decimal_places=3, coerce_to_string=True, read_only=True)

    class Meta:
        model = OperationLine
        fields = (
            "id",
            "direction", "direction_display",
            "container_id", "container_code",
            "product_id", "product_article", "product_name",
            "qty",
            "scrap_reason", "scrap_reason_display",
        )


class OperationSerializer(serializers.ModelSerializer):
    operation_type_display = serializers.CharField(
        source="get_operation_type_display", read_only=True)
    created_by_username = serializers.CharField(
        source="created_by.username", read_only=True, default=None)
    lines = OperationLineSerializer(many=True, read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Operation
        fields = (
            "id",
            "operation_type", "operation_type_display",
            "created_at",
            "created_by_id", "created_by_username",
            "order_id",
            "comment",
            "lines",
            "summary",
        )

    def get_summary(self, obj):
        f = Decimal("0")
        t = Decimal("0")
        s = Decimal("0")
        for line in obj.lines.all():
            if line.direction == OperationDirection.FROM:
                f += line.qty
            elif line.direction == OperationDirection.TO:
                t += line.qty
            elif line.direction == OperationDirection.SCRAP:
                s += line.qty
        return {
            "from_total": str(f),
            "to_total": str(t),
            "scrap_total": str(s),
            "balanced": f == t + s,
        }
