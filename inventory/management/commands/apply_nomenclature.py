"""Применяет файл-номенклатуру к БД.

Формат файла тот же, что у check_nomenclature:
    Колесо рабочее 2028376<TAB>2474215
    <TAB>2474394
    Колесо рабочее 2028374<TAB>2474229
    ...

Что делает:
  1. Создаёт отсутствующие детали (product_type='part', is_active=True).
  2. Создаёт отсутствующие отливки (product_type='casting', is_active=True).
  3. Перезаписывает mo1..mo4 у всех отливок по файлу.
     Старые неверные привязки отвязываются (дети остаются в базе).

По умолчанию — dry-run. С флагом --apply — применяет.

Запуск:
    python manage.py apply_nomenclature /opt/sklad/nomenclature_check.txt
    python manage.py apply_nomenclature /opt/sklad/nomenclature_check.txt --apply
"""
import re
from collections import OrderedDict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventory.models import Product


NUM_RE = re.compile(r"\d{5,}")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    current = None
    parsed = OrderedDict()
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        parts = re.split(r"\t+", ln, maxsplit=1)
        left = parts[0].strip()
        right = parts[1].strip() if len(parts) > 1 else ""
        if left:
            m = NUM_RE.search(left)
            if not m:
                continue
            current = m.group(0)
            parsed.setdefault(current, [])
            if right:
                parsed[current].append(right)
        else:
            if current is None:
                continue
            if right:
                parsed[current].append(right)
    return parsed


class Command(BaseCommand):
    help = "Создаёт/обновляет номенклатуру по файлу (отливка → Mo)"

    def add_arguments(self, parser):
        parser.add_argument("path", type=str)
        parser.add_argument("--apply", action="store_true",
                            help="Реально применить изменения")

    def handle(self, *args, **opts):
        path = opts["path"]
        apply = opts["apply"]

        try:
            parsed = _parse(path)
        except OSError as e:
            raise CommandError(f"Не читается файл {path}: {e}")

        if not parsed:
            raise CommandError("В файле не распознано ни одной отливки")

        # Все дети из файла — уникальные артикулы
        all_children = set()
        for kids in parsed.values():
            all_children.update(kids)

        # Проверка «>4 детей» — предупреждение
        over_limit = {c: kids for c, kids in parsed.items() if len(kids) > 8}

        # ── Планируем ──────────────────────────────────────
        existing_products = {
            p.article: p
            for p in Product.objects.filter(
                article__in=list(all_children) + list(parsed.keys())
            )
        }

        to_create_children = [
            a for a in sorted(all_children) if a not in existing_products
        ]
        to_create_castings = [
            a for a in parsed.keys() if a not in existing_products
        ]

        # Обновление Mo: сравним с текущим состоянием
        will_update_mo = []
        for c_art, kids in parsed.items():
            c = existing_products.get(c_art)
            if not c:
                continue
            db_children = []
            for i in (1, 2, 3, 4, 5, 6, 7, 8):
                cid = getattr(c, f"mo{i}_id", None)
                if cid:
                    db_children.append(Product.objects.get(pk=cid).article)
            if db_children != kids[:8]:
                will_update_mo.append(c_art)

        # ── Отчёт ────────────────────────────────────────
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nФайл: {path}  (--apply = {apply})"))
        self.stdout.write(f"Отливок в файле:           {len(parsed)}")
        self.stdout.write(f"Уникальных детей в файле:  {len(all_children)}")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Будут созданы детали:      {len(to_create_children)}"))
        if to_create_children:
            self.stdout.write("  " + ", ".join(to_create_children))
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Будут созданы отливки:     {len(to_create_castings)}"))
        if to_create_castings:
            self.stdout.write("  " + ", ".join(to_create_castings))
        self.stdout.write("")
        self.stdout.write(self.style.WARNING(
            f"Отливки, где Mo перезапишутся: {len(will_update_mo)}"))
        if will_update_mo:
            self.stdout.write("  " + ", ".join(will_update_mo))
        if over_limit:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(
                f"ОТЛИВОК С > 4 ДЕТЕЙ: {len(over_limit)} "
                f"(берём первые 4)"))
            for c, kids in over_limit.items():
                self.stdout.write(f"  {c}: {kids}")

        if not apply:
            self.stdout.write("")
            self.stdout.write(self.style.NOTICE(
                "DRY-RUN. Ничего не изменено. "
                "Повтори с флагом --apply, чтобы применить."))
            return

        # ── Применяем ────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Применяю..."))

        with transaction.atomic():
            created_children = []
            for a in to_create_children:
                p = Product.objects.create(
                    article=a,
                    name=f"Деталь {a}",
                    product_type="part",
                    is_active=True,
                )
                existing_products[a] = p
                created_children.append(a)

            created_castings = []
            for a in to_create_castings:
                p = Product.objects.create(
                    article=a,
                    name=f"Отливка {a}",
                    product_type="casting",
                    is_active=True,
                )
                existing_products[a] = p
                created_castings.append(a)

            updated = []
            for c_art, kids in parsed.items():
                c = existing_products.get(c_art)
                if not c:
                    continue
                mo = kids[:8]
                for i in range(1, 9):
                    field = f"mo{i}"
                    if i <= len(mo):
                        setattr(c, field, existing_products[mo[i - 1]])
                    else:
                        setattr(c, field, None)
                c.save(update_fields=[
                    "mo1", "mo2", "mo3", "mo4",
                    "mo5", "mo6", "mo7", "mo8", "updated_at"])
                updated.append(c_art)

        self.stdout.write(self.style.SUCCESS(
            f"\nСоздано деталей:  {len(created_children)}"))
        self.stdout.write(self.style.SUCCESS(
            f"Создано отливок:  {len(created_castings)}"))
        self.stdout.write(self.style.SUCCESS(
            f"Обновлено Mo у:   {len(updated)}"))
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("ГОТОВО."))
