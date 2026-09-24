"""Парсер вставленного текста из Excel/CSV.

Формат: TSV (табы). Первая строка — шапка.
Колонки распознаются по подстрокам в заголовке — гибко:

    №стнк / станок         → machine
    п/п / пп / №           → sequence
    заказ / номер заказа   → number (пустой = продолжение предыдущего)
    лить / отлив           → casting_article
    mo / мо / мех / м/о /
    деталь / артикул детали → product_article
    наименование / название → name
    к-во / кол / количество → quantity_planned
    срок                    → due_date
    готовность / готов      → ready_date
    задел                   → reserve_qty (дельта: +сверх плана / −недодел)
    отгружено               → shipped_date
    мест                    → places
    вес                     → weight_g (кг → граммы)
    статус                  → status
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


STATUS_MAP = {
    "новый": "new",
    "новая": "new",
    "в работе": "in_work",
    "готов": "ready",
    "готово": "ready",
    "готова": "ready",
    "отгружен": "shipped",
    "отгружено": "shipped",
    "отгружена": "shipped",
    "закрыт": "closed",
    "закрыто": "closed",
    "закрыта": "closed",
}


def _parse_date(s):
    if s is None:
        return None
    s = str(s).strip()
    if not s or s in ("—", "-", " ", "0"):
        return None
    # Отрезаем время, если есть
    s = s.split(" ")[0]
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    # dd.MM без года — текущий год
    m = re.match(r"^(\d{1,2})[.\-/](\d{1,2})$", s)
    if m:
        try:
            return date(date.today().year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


def _parse_decimal(s):
    if s is None:
        return None
    s = str(s).strip()
    if not s or s in ("—", "-"):
        return None
    s = s.replace(",", ".").replace(" ", "").replace("\u00a0", "")
    # Вытащить число в начале (иногда «100(150)», «+6», «−1»)
    m = re.match(r"^([+\-]?\d+(?:\.\d+)?)", s)
    if not m:
        return None
    try:
        return Decimal(m.group(1))
    except (InvalidOperation, ValueError):
        return None


def _parse_int(s):
    d = _parse_decimal(s)
    if d is None:
        return None
    try:
        return int(d)
    except (ValueError, TypeError):
        return None


def _parse_weight_g(s):
    """Пользователь вводит килограммы (896) — сохраняем в граммах (896000)."""
    d = _parse_decimal(s)
    if d is None or d == 0:
        return None
    return d * Decimal("1000")


def _detect_columns(header_cells):
    """Определяет индексы колонок по подстрокам в шапке.
    Порядок проверок важен — специфичные варианты раньше общих.
    """
    idx = {}
    for i, raw in enumerate(header_cells):
        h = (raw or "").strip().lower().replace("\u00a0", " ")
        if not h:
            continue

        # Порядок: сначала узкие, потом широкие
        if "станк" in h or h.startswith("№ст") or "№ ст" in h:
            idx.setdefault("machine", i)
        elif h in ("п/п", "пп", "п.п", "п.п.", "№", "№п/п", "№ п/п"):
            idx.setdefault("sequence", i)
        elif "номер заказ" in h or h == "заказ" or h == "зак" or h == "№ заказа":
            idx.setdefault("number", i)
        elif "отлив" in h or "лить" in h:
            idx.setdefault("casting_article", i)
        elif ("артикул детал" in h or "детал" in h
              or h in ("mo", "мо", "мех", "м/о", "м.о.", "м.о")):
            idx.setdefault("product_article", i)
        elif "наимен" in h or "назван" in h:
            idx.setdefault("name", i)
        elif "к-во" in h or "кол" in h or "колич" in h:
            idx.setdefault("quantity_planned", i)
        elif "срок" in h:
            idx.setdefault("due_date", i)
        elif "готов" in h:
            idx.setdefault("ready_date", i)
        elif "задел" in h:
            idx.setdefault("reserve_qty", i)
        elif "отгруж" in h:
            idx.setdefault("shipped_date", i)
        elif "мест" in h:
            idx.setdefault("places", i)
        elif "вес" in h or "масс" in h:
            idx.setdefault("weight_g", i)
        elif "статус" in h or "состоян" in h:
            idx.setdefault("status", i)
    return idx


def parse_paste(text):
    """Парсит вставленный текст и возвращает словарь:
    {
      "header": [...],
      "columns": {...},
      "orders": [
        {"number": "84М", "due_date": ..., "status": ..., "lines": [{...}]},
        ...
      ],
      "skipped_lines": [...],
    }
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if len(lines) < 2:
        raise ValueError("Нужно минимум две строки: шапка + данные")

    # Найдём строку-шапку: ищем строку, содержащую «заказ» + один из
    # маркеров артикула детали/отливки/количества. Ищем в первых 15 строках.
    header_line_idx = None
    for i, ln in enumerate(lines[:15]):
        low = ln.lower()
        has_order = "заказ" in low
        has_art = ("детал" in low or "отлив" in low
                   or "mo" in low or "мех" in low or "лить" in low
                   or "артикул" in low)
        has_qty = ("к-во" in low or "кол" in low or "колич" in low)
        if has_order and (has_art or has_qty):
            header_line_idx = i
            break

    if header_line_idx is None:
        header_line_idx = 0

    header_line = lines[header_line_idx]
    if "\t" in header_line:
        split_re = re.compile(r"\t")
    elif ";" in header_line:
        split_re = re.compile(r";")
    else:
        split_re = re.compile(r"\s{3,}")

    header_cells = [c.strip() for c in split_re.split(header_line)]
    idx = _detect_columns(header_cells)

    required = {"number", "product_article", "quantity_planned"}
    missing = required - set(idx.keys())
    if missing:
        raise ValueError(
            f"В шапке не найдены обязательные столбцы: {sorted(missing)}. "
            f"Найдены колонки: {header_cells}"
        )

    orders = []
    current = None
    skipped = []

    for raw in lines[header_line_idx + 1:]:
        if not raw.strip():
            continue

        cells = [c.strip() for c in split_re.split(raw)]
        while len(cells) < len(header_cells):
            cells.append("")
        if len(cells) > len(header_cells):
            cells = cells[:len(header_cells)]

        def get(key):
            i = idx.get(key)
            if i is None or i >= len(cells):
                return ""
            return cells[i]

        number = get("number").strip()
        product_article = get("product_article").strip()

        if not number and not product_article:
            skipped.append(raw)
            continue

        # Номер заказа непустой → начинаем новый заказ
        if number:
            status_raw = get("status").strip().lower()
            status = STATUS_MAP.get(status_raw, "new")
            due = _parse_date(get("due_date"))
            current = {
                "number": number,
                "kind": "production",
                "customer": "",
                "status": status,
                "due_date": due,
                "comment": "",
                "lines": [],
            }
            orders.append(current)

        if current is None:
            skipped.append(raw)
            continue

        if not product_article:
            skipped.append(raw)
            continue

        line = {
            "sequence": _parse_int(get("sequence")) or 0,
            "machine": get("machine").strip(),
            "casting_article": get("casting_article").strip(),
            "product_article": product_article,
            "name": get("name").strip(),
            "quantity_planned": _parse_decimal(get("quantity_planned")) or Decimal("0"),
            "ready_date": _parse_date(get("ready_date")),
            "shipped_date": _parse_date(get("shipped_date")),
            "reserve_qty": _parse_decimal(get("reserve_qty")) or Decimal("0"),
            "places": _parse_int(get("places")),
            "weight_g": _parse_weight_g(get("weight_g")),
        }

        current["lines"].append(line)

    return {
        "header": header_cells,
        "columns": idx,
        "orders": orders,
        "skipped_lines": skipped,
    }
