"""Сверяет файл-список (отливка → Mo-детали) с текущей БД.

Формат файла: TSV.
- Строка с текстом в первой колонке → новая отливка.
  Артикул отливки берём как число из названия (последнее число до таба).
- Пустая первая колонка + артикул во второй → следующая Mo-деталь.

Запуск:
    python manage.py check_nomenclature /opt/sklad/nomenclature_check.txt
"""
import re
from collections import OrderedDict

from django.core.management.base import BaseCommand, CommandError

from inventory.models import Product


NUM_RE = re.compile(r"\d{5,}")


class Command(BaseCommand):
    help = "Сверка номенклатуры с файлом-списком (отливка → Mo)"

    def add_arguments(self, parser):
        parser.add_argument("path", type=str)

    def handle(self, *args, **opts):
        path = opts["path"]
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            raise CommandError(f"Не читается файл {path}: {e}")

        # парсим
        current = None
        parsed = OrderedDict()  # casting_article -> [child_articles]
        for ln in raw.splitlines():
            if not ln.strip():
                continue
            parts = re.split(r"\t+", ln, maxsplit=1)
            left = parts[0].strip()
            right = parts[1].strip() if len(parts) > 1 else ""

            if left:
                # новая отливка — выдираем первое 5+-значное число
                m = NUM_RE.search(left)
                if not m:
                    self.stdout.write(self.style.WARNING(
                        f"  пропуск (нет артикула отливки): {ln!r}"))
                    current = None
                    continue
                casting_art = m.group(0)
                current = casting_art
                parsed.setdefault(current, [])
                if right:
                    parsed[current].append(right)
            else:
                # продолжение: Mo-деталь для текущей отливки
                if current is None:
                    continue
                if right:
                    parsed[current].append(right)

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n=== Из файла прочитано отливок: {len(parsed)}"))

        missing_castings = []
        missing_children = {}       # casting_art → [child_arts]
        mo_mismatch = {}            # casting_art → {"in_db": [...], "in_file": [...]}
        ok_count = 0

        for casting_art, child_arts in parsed.items():
            casting = Product.objects.filter(
                article=casting_art, product_type="casting"
            ).first()
            if not casting:
                missing_castings.append(casting_art)
                # детей тоже считаем недостающими
                missing_children[casting_art] = child_arts
                continue

            db_children = []
            for i in (1, 2, 3, 4, 5, 6, 7, 8):
                cid = getattr(casting, f"mo{i}_id", None)
                if cid:
                    db_children.append(Product.objects.get(pk=cid).article)

            file_set = set(child_arts)
            db_set = set(db_children)

            # каких детей нет в БД
            miss = [a for a in child_arts if not Product.objects.filter(article=a).exists()]
            if miss:
                missing_children[casting_art] = miss

            if file_set != db_set:
                mo_mismatch[casting_art] = {
                    "in_db": db_children,
                    "in_file": child_arts,
                }
            else:
                ok_count += 1

        # ── Отчёт ────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Совпадает (отливка + все Mo): {ok_count}"))

        if missing_castings:
            self.stdout.write(self.style.ERROR(
                f"\n=== Отливок НЕТ в БД ({len(missing_castings)}):"))
            for a in missing_castings:
                self.stdout.write(f"  {a}  → надо {parsed[a]}")

        if mo_mismatch:
            self.stdout.write(self.style.WARNING(
                f"\n=== Отливки с расхождением Mo ({len(mo_mismatch)}):"))
            for a, d in mo_mismatch.items():
                self.stdout.write(f"  {a}:")
                self.stdout.write(f"     в БД:    {d['in_db']}")
                self.stdout.write(f"     в файле: {d['in_file']}")

        # Детали, которых нет в БД (среди тех, что должны быть)
        all_missing_children = {}
        for c_art, kids in missing_children.items():
            for k in kids:
                all_missing_children[k] = c_art
        if all_missing_children:
            self.stdout.write(self.style.ERROR(
                f"\n=== Деталей-детей НЕТ в БД ({len(all_missing_children)}):"))
            for k, c_art in all_missing_children.items():
                self.stdout.write(f"  {k}   (для отливки {c_art})")

        self.stdout.write("")
