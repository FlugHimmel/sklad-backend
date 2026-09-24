from django.db import migrations


def create_reserve_warehouse(apps, schema_editor):
    Warehouse = apps.get_model("warehouse", "Warehouse")
    Container = apps.get_model("warehouse", "Container")
    ContainerLine = apps.get_model("warehouse", "ContainerLine")

    # Создаём склад «Задел у станков»
    Warehouse.objects.get_or_create(
        code="RESERVE",
        defaults={"name": "Задел у станков", "is_active": True},
    )

    # Создаём ContainerLine для существующих тар, где её ещё нет
    for c in Container.objects.all():
        if not c.product_id or (c.quantity or 0) == 0:
            continue
        if ContainerLine.objects.filter(container=c).exists():
            continue
        ContainerLine.objects.create(
            container=c,
            product_id=c.product_id,
            quantity=c.quantity,
        )


def drop_reserve_warehouse(apps, schema_editor):
    Warehouse = apps.get_model("warehouse", "Warehouse")
    Warehouse.objects.filter(code="RESERVE").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("warehouse", "0007_alter_container_product_alter_container_quantity_and_more"),
    ]
    operations = [
        migrations.RunPython(create_reserve_warehouse, drop_reserve_warehouse),
    ]
