import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from inventory.models import BOM, BOMLine, Product


class Command(BaseCommand):
    help = (
        "Полная установка номенклатуры из JSON: сначала удаляет ВСЕ "
        "Product/BOM/BOMLine, потом создаёт заново из файла. "
        "Идемпотентно — можно запускать много раз."
    )

    def add_arguments(self, parser):
        parser.add_argument("json_path", type=str, help="Путь к JSON-файлу")
        parser.add_argument("--dry-run", action="store_true",
                            help="Только показать, что будет сделано")

    def handle(self, *args, **options):
        path = Path(options["json_path"])
        if not path.exists():
            self.stdout.write(self.style.ERROR(f"Файл не найден: {path}"))
            return

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        dry = options["dry_run"]
        if dry:
            self.stdout.write(self.style.WARNING("=== DRY-RUN (без записи) ==="))

        # Проверим, что на Product нет защищённых ссылок от контейнеров/движений.
        # Если есть — сначала попросим сделать reset_data.
        from warehouse.models import Container, ContainerLine, Movement, Stock
        from orders.models import OrderLine

        blockers = []
        for model, label in [
            (ContainerLine, "содержимое тары"),
            (Container, "тары"),
            (Movement, "движения"),
            (Stock, "остатки"),
            (OrderLine, "строки заказов"),
        ]:
            cnt = model.objects.count()
            if cnt:
                blockers.append(f"{label}: {cnt}")

        if blockers:
            self.stdout.write(self.style.ERROR(
                "Нельзя очистить номенклатуру — есть зависимые данные:\n  "
                + "\n  ".join(blockers)
                + "\n\nСначала запусти: python manage.py reset_data --yes"
            ))
            return

        if dry:
            self.stdout.write(f"JSON содержит {len(data)} отливок")
            total_parts = sum(len(x.get("parts", [])) for x in data)
            self.stdout.write(f"Всего деталей: {total_parts}")
            self.stdout.write("")
            for x in data:
                self.stdout.write(
                    f"  {x['casting']} — {x['type']} — "
                    f"{x['weight_kg']} кг — детали: {', '.join(x.get('parts', []))}"
                )
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("DRY-RUN завершён, ничего не записано."))
            return

        created_castings = 0
        created_parts = 0
        boms_created = 0

        with transaction.atomic():
            # 1. Чистим всё
            BOM.objects.all().delete()          # каскадом удалит BOMLine
            Product.objects.all().delete()

            # 2. Создаём заново
            for item in data:
                cast_art = str(item["casting"]).strip()
                cast_type = str(item.get("type") or "").strip() or cast_art
                weight_kg = Decimal(str(item.get("weight_kg") or "0"))
                weight_g = weight_kg * Decimal("1000")

                casting = Product.objects.create(
                    article=cast_art,
                    name=f"{cast_type} {cast_art} (литьё)",
                    product_type="casting",
                    uom="pcs",
                    weight_g=weight_g,
                    is_active=True,
                )
                created_castings += 1

                parts_objs = []
                for part_art in item.get("parts", []):
                    part_art = str(part_art).strip()
                    if not part_art:
                        continue
                    part = Product.objects.create(
                        article=part_art,
                        name=f"{cast_type} {part_art}",
                        product_type="part",
                        uom="pcs",
                        weight_g=weight_g,
                        is_active=True,
                    )
                    parts_objs.append(part)
                    created_parts += 1

                    # BOM: деталь ← отливка × 1
                    bom = BOM.objects.create(
                        product=part, version="1.0", is_active=True)
                    BOMLine.objects.create(
                        bom=bom,
                        component=casting,
                        quantity=Decimal("1"),
                        note=f"Отливка для {part.article}",
                    )
                    boms_created += 1

                # Mo1..Mo4
                mo_fields = ["mo1", "mo2", "mo3", "mo4"]
                for idx, field in enumerate(mo_fields):
                    val = parts_objs[idx] if idx < len(parts_objs) else None
                    setattr(casting, field, val)
                casting.save(update_fields=mo_fields)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Установка номенклатуры завершена ==="))
        self.stdout.write(f"  Создано отливок: {created_castings}")
        self.stdout.write(f"  Создано деталей: {created_parts}")
        self.stdout.write(f"  Создано BOM: {boms_created}")
