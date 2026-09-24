import io
import os
from decimal import Decimal

from django.utils import timezone
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from inventory.models import BOM, Product

FONT_REG = "DejaVu"
FONT_BOLD = "DejaVu-Bold"

_DEJAVU_REG_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_DEJAVU_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _register_fonts():
    global FONT_REG, FONT_BOLD
    if FONT_REG in pdfmetrics.getRegisteredFontNames():
        return
    try:
        pdfmetrics.registerFont(TTFont(FONT_REG, _DEJAVU_REG_PATH))
        if os.path.exists(_DEJAVU_BOLD_PATH):
            pdfmetrics.registerFont(TTFont(FONT_BOLD, _DEJAVU_BOLD_PATH))
        else:
            pdfmetrics.registerFont(TTFont(FONT_BOLD, _DEJAVU_REG_PATH))
    except Exception:
        FONT_REG = "Helvetica"
        FONT_BOLD = "Helvetica-Bold"


_register_fonts()


RUS_MONTHS = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def russian_date(d) -> str:
    return f"«{d.day:02d}» {RUS_MONTHS[d.month]} {d.year} г."


def _fmt_qty(value) -> str:
    """150.000 → 150,  10.500 → 10.5"""
    try:
        d = Decimal(str(value))
    except Exception:
        return str(value)
    if d == d.to_integral_value():
        return str(int(d))
    return str(d.normalize())


def _casting_children(product):
    """Собираем Mo1..Mo8 для отливки. Возвращает [Product, ...] без None."""
    if not product or getattr(product, "product_type", None) != "casting":
        return []
    ids = []
    for i in range(1, 9):
        v = getattr(product, f"mo{i}_id", None)
        if v:
            ids.append(v)
    if not ids:
        return []
    found = {p.id: p for p in Product.objects.filter(pk__in=ids)}
    return [found[i] for i in ids if i in found]


def _container_needs_big_label(container) -> bool:
    """Все этикетки одного размера — детали переехали на упаковочный лист."""
    return False


def _draw_container_label(c, container, x0, y_top, label_w, label_h):
    """Рисует одну этикетку в заданной рамке.
    Для отливки — плюс блок мини-ШК деталей (до 8), 4 в ряд, до 2 рядов.
    """
    product = container.product
    children = _casting_children(product)
    has_children = len(children) > 0

    # Внешняя рамка
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.8)
    c.rect(x0, y_top - label_h, label_w, label_h)

    # Код тары
    c.setFont(FONT_BOLD, 18)
    c.drawString(x0 + 5 * mm, y_top - 9 * mm, container.code)

    # Большой ШК тары
    big_bar_h = 14 * mm if not has_children else 16 * mm
    bc = code128.Code128(
        container.code,
        barWidth=0.35 * mm,
        barHeight=big_bar_h,
        humanReadable=False,
    )
    bc.drawOn(c, x0 + 5 * mm, y_top - 11 * mm - big_bar_h)

    y_cursor = y_top - 11 * mm - big_bar_h - 4 * mm

    art = product.article if product else "—"
    name = product.name if product else "—"
    uom = product.get_uom_display() if product else ""

    c.setFont(FONT_REG, 9)
    c.drawString(x0 + 5 * mm, y_cursor, f"Артикул: {art}")
    y_cursor -= 4.5 * mm
    c.drawString(x0 + 5 * mm, y_cursor, f"Наименование: {name[:70]}")
    y_cursor -= 6 * mm
    c.setFont(FONT_BOLD, 12)
    c.drawString(x0 + 5 * mm, y_cursor,
                 f"Кол-во: {_fmt_qty(container.quantity)} {uom}")
    c.setFont(FONT_REG, 7.5)
    y_cursor -= 4.5 * mm
    c.drawString(x0 + 5 * mm, y_cursor,
                 f"Создано: {timezone.localtime(container.created_at).strftime('%d.%m.%Y')}")

    # Мини-ШК деталей переехали на упаковочный лист (см. ниже).
    return


def generate_container_label_pdf(container) -> bytes:
    from reportlab.pdfgen import canvas as canvaslib

    buf = io.BytesIO()
    c = canvaslib.Canvas(buf, pagesize=A4)
    _, page_h = A4
    x0 = 10 * mm
    y_top = page_h - 10 * mm

    if _container_needs_big_label(container):
        label_w, label_h = 190 * mm, 110 * mm
    else:
        label_w, label_h = 110 * mm, 65 * mm

    _draw_container_label(c, container, x0, y_top, label_w, label_h)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


def generate_container_labels_pdf(containers) -> bytes:
    """Печать пачкой на A4: большие (с Mo) и маленькие этикетки
    укладываются на листы максимально плотно, вперемешку.

    Правила укладки:
      большая 190×110 мм — 1 в строке, по центру;
      маленькая 98×62 мм — 2 в строке.

    Порядок этикеток сохраняется — рисуем сверху вниз в том
    порядке, в котором контейнеры пришли в списке.
    """
    from reportlab.pdfgen import canvas as canvaslib

    containers = list(containers)
    buf = io.BytesIO()
    c = canvaslib.Canvas(buf, pagesize=A4)

    if not containers:
        c.showPage()
        c.save()
        buf.seek(0)
        return buf.read()

    page_w, page_h = A4
    margin = 5 * mm
    gap = 4 * mm

    small_w, small_h = 98 * mm, 62 * mm
    big_w, big_h = 190 * mm, 110 * mm

    # used_y — на сколько мм от верха страницы уже занято.
    used_y = margin
    # Если открыт ряд маленьких (нарисована только левая) — здесь лежит
    # used_y для этого ряда. None — ряда нет.
    small_row_start_y = None

    def _new_page():
        nonlocal used_y, small_row_start_y
        c.showPage()
        used_y = margin
        small_row_start_y = None

    for container in containers:
        is_big = _container_needs_big_label(container)

        if is_big:
            # Закрываем открытый ряд мелких, если он есть
            if small_row_start_y is not None:
                used_y = small_row_start_y + small_h + gap
                small_row_start_y = None

            if used_y + big_h > page_h - margin:
                _new_page()

            y_top = page_h - used_y
            x0 = (page_w - big_w) / 2
            _draw_container_label(c, container, x0, y_top, big_w, big_h)
            used_y += big_h + gap

        else:
            if small_row_start_y is None:
                # Начинаем новый ряд мелких
                if used_y + small_h > page_h - margin:
                    _new_page()
                small_row_start_y = used_y
                y_top = page_h - used_y
                _draw_container_label(c, container, margin, y_top,
                                      small_w, small_h)
                # used_y не двигаем — ждём вторую в ряду
            else:
                # Вторая в ряду — рисуем справа, на той же высоте
                y_top = page_h - small_row_start_y
                x0 = margin + small_w + gap
                _draw_container_label(c, container, x0, y_top,
                                      small_w, small_h)
                used_y = small_row_start_y + small_h + gap
                small_row_start_y = None

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


def _resolve_art_before(product):
    """Ищет артикул отливки (артикул до МО) для детали.
    1. Через BOM (component).
    2. Через Mo-слоты обратно: ищем Product(product_type='casting') где
       деталь стоит в mo1..mo8.
    Возвращает строку или None.
    """
    if product is None:
        return None
    # Через BOM
    try:
        from inventory.models import BOM
        bom = (
            BOM.objects.filter(product=product, is_active=True)
            .prefetch_related("lines__component")
            .first()
        )
        if bom:
            first_line = bom.lines.first()
            if first_line and first_line.component:
                return first_line.component.article
    except Exception:
        pass

    # Через Mo-слоты
    try:
        from inventory.models import Product
        qs = Product.objects.filter(product_type="casting")
        for i in range(1, 9):
            field = f"mo{i}"
            found = qs.filter(**{field: product}).first()
            if found:
                return found.article
    except Exception:
        pass

    return None


def generate_packing_list_pdf(container, company, art_before_override=None) -> bytes:
    """Красивый упаковочный лист."""
    buf = io.BytesIO()
    styles = getSampleStyleSheet()

    st_ownership = ParagraphStyle(
        "own", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=9,
        textColor=colors.HexColor("#666666"), alignment=TA_LEFT, leading=12,
    )
    st_small = ParagraphStyle(
        "sm", parent=styles["Normal"], fontName=FONT_REG, fontSize=8.5,
        leading=11, alignment=TA_LEFT, textColor=colors.HexColor("#222222"),
    )
    st_title = ParagraphStyle(
        "tt", parent=styles["Heading1"], fontName=FONT_BOLD, fontSize=20,
        alignment=TA_CENTER, textColor=colors.black, spaceAfter=4,
    )
    st_date = ParagraphStyle(
        "dt", parent=styles["Normal"], fontName=FONT_REG, fontSize=11,
        alignment=TA_LEFT, textColor=colors.black,
    )
    st_cell = ParagraphStyle(
        "cl", parent=styles["Normal"], fontName=FONT_REG, fontSize=9,
        leading=11, alignment=TA_LEFT, textColor=colors.black,
    )
    st_cell_c = ParagraphStyle("clc", parent=st_cell, alignment=TA_CENTER)
    st_cell_b = ParagraphStyle("clb", parent=st_cell, fontName=FONT_BOLD)
    st_head = ParagraphStyle(
        "hd", parent=st_cell, fontName=FONT_BOLD, alignment=TA_CENTER,
        textColor=colors.black, fontSize=9.5, leading=11,
    )
    st_total = ParagraphStyle(
        "tl", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=13,
        alignment=TA_LEFT, textColor=colors.black,
    )

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title="Упаковочный лист",
        author=company.get("supplier_name", ""),
    )

    story = []
    story.append(Paragraph(company["ownership_note"], st_ownership))
    story.append(Spacer(1, 4 * mm))

    customer_block = (
        f"<b>Заказчик:</b> {company['customer_name']}<br/>"
        f"{company['customer_address'].replace(chr(10), '<br/>')}"
    )
    supplier_block = (
        f"<b>Поставщик:</b> {company['supplier_name']}<br/>"
        f"{company['supplier_address'].replace(chr(10), '<br/>')}"
    )
    header_tbl = Table(
        [[Paragraph(customer_block, st_small), Paragraph(supplier_block, st_small)]],
        colWidths=[doc.width * 0.62, doc.width * 0.38],
    )
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph(company["packing_list_title"], st_title))
    story.append(Spacer(1, 3 * mm))

    today = timezone.localdate()
    story.append(Paragraph(russian_date(today), st_date))
    story.append(Spacer(1, 6 * mm))

    product = container.product
    art_after = product.article if product else "—"
    name = product.name if product else "—"

    art_before = "—"
    if art_before_override:
        art_before = str(art_before_override)
    elif product:
        resolved = _resolve_art_before(product)
        if resolved:
            art_before = resolved

    weight_g = Decimal(str(product.weight_g)) if product else Decimal("0")
    total_kg = (weight_g * Decimal(str(container.quantity))) / Decimal("1000")

    head_row = [
        Paragraph("№", st_head),
        Paragraph("Наименование<br/>детали, материал", st_head),
        Paragraph("Артикул<br/>до МО", st_head),
        Paragraph("Артикул<br/>после МО", st_head),
        Paragraph("Кол-во,<br/>шт", st_head),
    ]
    body_row = [
        Paragraph("1", st_cell_c),
        Paragraph(name, st_cell),
        Paragraph(art_before, st_cell_c),
        Paragraph(art_after, st_cell_b),
        Paragraph(_fmt_qty(container.quantity), st_cell_b),
    ]

    col_widths = [
        10 * mm,
        doc.width - 10 * mm - 26 * mm - 26 * mm - 20 * mm,
        26 * mm,
        26 * mm,
        20 * mm,
    ]
    table = Table([head_row, body_row], colWidths=col_widths,
                  rowHeights=[14 * mm, 12 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f3f6")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
        ("BOX", (0, 0), (-1, -1), 0.9, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(table)

    story.append(Spacer(1, 12 * mm))
    weight_row = Table(
        [[Paragraph("ВЕС:", st_total),
          Paragraph(f"{total_kg.quantize(Decimal('0.001'))} кг", st_total)]],
        colWidths=[20 * mm, doc.width - 20 * mm],
    )
    weight_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (1, 0), (1, 0), 0.7, colors.black),
    ]))
    story.append(weight_row)

    story.append(Spacer(1, 18 * mm))
    story.append(Paragraph(
        "Отгрузил: _______________________ / _________________",
        st_small,
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()


def generate_transfer_note_pdf(containers, from_warehouse, to_warehouse,
                                company, user=None) -> bytes:
    """Накладная на перемещение тары между складами.
    Список: № / Код / Артикул / Наименование / Кол-во / Ед.
    Итог по количеству. Две подписи: Отгрузил / Принял.
    """
    buf = io.BytesIO()
    styles = getSampleStyleSheet()

    st_own = ParagraphStyle(
        "own", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=9,
        textColor=colors.HexColor("#666666"), alignment=TA_LEFT, leading=12,
    )
    st_small = ParagraphStyle(
        "sm", parent=styles["Normal"], fontName=FONT_REG, fontSize=9,
        leading=11, alignment=TA_LEFT, textColor=colors.HexColor("#222222"),
    )
    st_title = ParagraphStyle(
        "tt", parent=styles["Heading1"], fontName=FONT_BOLD, fontSize=18,
        alignment=TA_CENTER, textColor=colors.black, spaceAfter=4,
    )
    st_date = ParagraphStyle(
        "dt", parent=styles["Normal"], fontName=FONT_REG, fontSize=10,
        alignment=TA_LEFT, textColor=colors.black,
    )
    st_head = ParagraphStyle(
        "hd", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=9.5,
        leading=11, alignment=TA_CENTER, textColor=colors.black,
    )
    st_cell = ParagraphStyle(
        "cl", parent=styles["Normal"], fontName=FONT_REG, fontSize=9,
        leading=11, alignment=TA_LEFT, textColor=colors.black,
    )
    st_cell_c = ParagraphStyle("clc", parent=st_cell, alignment=TA_CENTER)
    st_cell_b = ParagraphStyle("clb", parent=st_cell, fontName=FONT_BOLD)
    st_total = ParagraphStyle(
        "tl", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=12,
        alignment=TA_LEFT, textColor=colors.black,
    )

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title="Накладная на перемещение",
        author=company.get("supplier_name", "") or "",
    )

    story = []
    story.append(Paragraph(company.get("ownership_note", ""), st_own))
    story.append(Spacer(1, 5 * mm))

    today = timezone.localdate()
    when_str = f"Дата: {russian_date(today)}"
    if user is not None:
        try:
            full = user.get_full_name().strip() or user.username
        except Exception:
            full = ""
        if full:
            when_str += f"   ·   Составил: {full}"
    story.append(Paragraph(when_str, st_date))
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("НАКЛАДНАЯ НА ПЕРЕМЕЩЕНИЕ", st_title))
    story.append(Spacer(1, 6 * mm))

    supplier_block = (
        f"<b>Отгрузил (склад):</b> {from_warehouse.name}"
    )
    customer_block = (
        f"<b>Принял (склад):</b> {to_warehouse.name}"
    )
    header_tbl = Table(
        [[Paragraph(supplier_block, st_small),
          Paragraph(customer_block, st_small)]],
        colWidths=[doc.width * 0.5, doc.width * 0.5],
    )
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 8 * mm))

    head_row = [
        Paragraph("№", st_head),
        Paragraph("Код тары", st_head),
        Paragraph("Артикул", st_head),
        Paragraph("Наименование", st_head),
        Paragraph("Кол-во", st_head),
        Paragraph("Ед.", st_head),
    ]

    body_rows = []
    total_qty = Decimal("0")
    for i, c in enumerate(containers, 1):
        product = c.product
        art = product.article if product else "—"
        name = product.name if product else "—"
        uom = product.get_uom_display() if product else ""
        qty = Decimal(str(c.quantity or 0))
        total_qty += qty
        body_rows.append([
            Paragraph(str(i), st_cell_c),
            Paragraph(c.code, st_cell_c),
            Paragraph(art, st_cell_c),
            Paragraph(name, st_cell),
            Paragraph(_fmt_qty(qty), st_cell_b),
            Paragraph(uom, st_cell_c),
        ])

    col_widths = [
        10 * mm,
        32 * mm,
        30 * mm,
        doc.width - 10 * mm - 32 * mm - 30 * mm - 22 * mm - 12 * mm,
        22 * mm,
        12 * mm,
    ]

    table_data = [head_row] + body_rows
    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f3f6")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (4, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
        ("BOX", (0, 0), (-1, -1), 0.9, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    tbl.setStyle(TableStyle(style_cmds))
    story.append(tbl)
    story.append(Spacer(1, 6 * mm))

    total_row = Table(
        [[Paragraph("ИТОГО тар:", st_total),
          Paragraph(str(len(containers)), st_total),
          Paragraph("ИТОГО количество:", st_total),
          Paragraph(_fmt_qty(total_qty), st_total)]],
        colWidths=[35 * mm, 25 * mm, 45 * mm,
                   doc.width - 35 * mm - 25 * mm - 45 * mm],
    )
    total_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEABOVE", (0, 0), (-1, 0), 0.7, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(total_row)

    story.append(Spacer(1, 20 * mm))

    sign_tbl = Table(
        [[Paragraph("Отгрузил: _______________________ / _________________", st_small),
          Paragraph("Принял: _______________________ / _________________", st_small)]],
        colWidths=[doc.width * 0.5, doc.width * 0.5],
    )
    sign_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(sign_tbl)

    doc.build(story)
    buf.seek(0)
    return buf.read()


# ============================================================================
# Накладная на отгрузку Завод → Модель (автономер, история)
# ============================================================================

SHIPMENT_FROM_NAME = 'ООО "Завод" (Пример литейная компания)'
SHIPMENT_TO_NAME = 'ООО "Модель"'


def generate_shipment_note_pdf(note) -> bytes:
    """PDF накладной на отгрузку. note — ShipmentNote с lines."""
    buf = io.BytesIO()
    styles = getSampleStyleSheet()

    st_small = ParagraphStyle("sm", parent=styles["Normal"], fontName=FONT_REG,
        fontSize=9, leading=11, alignment=TA_LEFT,
        textColor=colors.HexColor("#222222"))
    st_title = ParagraphStyle("tt", parent=styles["Heading1"], fontName=FONT_BOLD,
        fontSize=18, alignment=TA_CENTER, textColor=colors.black, spaceAfter=4)
    st_num = ParagraphStyle("nm", parent=styles["Normal"], fontName=FONT_BOLD,
        fontSize=13, alignment=TA_CENTER, textColor=colors.black)
    st_date = ParagraphStyle("dt", parent=styles["Normal"], fontName=FONT_REG,
        fontSize=10, alignment=TA_LEFT, textColor=colors.black)
    st_head = ParagraphStyle("hd", parent=styles["Normal"], fontName=FONT_BOLD,
        fontSize=9.5, leading=11, alignment=TA_CENTER, textColor=colors.black)
    st_cell = ParagraphStyle("cl", parent=styles["Normal"], fontName=FONT_REG,
        fontSize=9, leading=11, alignment=TA_LEFT, textColor=colors.black)
    st_cell_c = ParagraphStyle("clc", parent=st_cell, alignment=TA_CENTER)
    st_cell_b = ParagraphStyle("clb", parent=st_cell, fontName=FONT_BOLD)
    st_total = ParagraphStyle("tl", parent=styles["Normal"], fontName=FONT_BOLD,
        fontSize=12, alignment=TA_LEFT, textColor=colors.black)

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"Накладная {note.number}",
        author=note.from_name or "",
    )

    story = []

    from_block = (
        f"<b>Отправитель:</b><br/>{note.from_name}"
        + (f"<br/>{note.from_address.replace(chr(10), '<br/>')}"
           if note.from_address else "")
    )
    to_block = (
        f"<b>Получатель:</b><br/>{note.to_name}"
        + (f"<br/>{note.to_address.replace(chr(10), '<br/>')}"
           if note.to_address else "")
    )
    header_tbl = Table(
        [[Paragraph(from_block, st_small), Paragraph(to_block, st_small)]],
        colWidths=[doc.width * 0.5, doc.width * 0.5],
    )
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph("НАКЛАДНАЯ", st_title))
    story.append(Paragraph(f"№ {note.number}", st_num))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f"Дата: {russian_date(note.note_date)}", st_date))
    story.append(Spacer(1, 6 * mm))

    head_row = [
        Paragraph("№", st_head),
        Paragraph("Код тары", st_head),
        Paragraph("Артикул", st_head),
        Paragraph("Наименование", st_head),
        Paragraph("Кол-во", st_head),
        Paragraph("Ед.", st_head),
        Paragraph("Вес, кг", st_head),
    ]

    body_rows = []
    for i, ln in enumerate(note.lines.all(), 1):
        body_rows.append([
            Paragraph(str(i), st_cell_c),
            Paragraph(ln.code, st_cell_c),
            Paragraph(ln.product_article, st_cell_c),
            Paragraph(ln.product_name, st_cell),
            Paragraph(_fmt_qty(ln.quantity), st_cell_b),
            Paragraph(ln.uom or "шт", st_cell_c),
            Paragraph(_fmt_qty(ln.weight_kg), st_cell_c),
        ])

    col_widths = [
        10 * mm,
        30 * mm,
        26 * mm,
        doc.width - 10 * mm - 30 * mm - 26 * mm - 20 * mm - 12 * mm - 22 * mm,
        20 * mm,
        12 * mm,
        22 * mm,
    ]

    tbl = Table([head_row] + body_rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f3f6")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (4, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
        ("BOX", (0, 0), (-1, -1), 0.9, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 6 * mm))

    total_row = Table(
        [[Paragraph("ИТОГО тар:", st_total),
          Paragraph(str(note.lines.count()), st_total),
          Paragraph("Количество:", st_total),
          Paragraph(_fmt_qty(note.total_qty), st_total),
          Paragraph("Общий вес:", st_total),
          Paragraph(f"{_fmt_qty(note.total_weight_kg)} кг", st_total)]],
        colWidths=[35 * mm, 18 * mm, 30 * mm, 25 * mm, 32 * mm,
                   doc.width - 35 * mm - 18 * mm - 30 * mm - 25 * mm - 32 * mm],
    )
    total_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEABOVE", (0, 0), (-1, 0), 0.7, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(total_row)

    if note.comment:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(f"Примечание: {note.comment}", st_small))

    story.append(Spacer(1, 20 * mm))

    sign_tbl = Table(
        [[Paragraph("Отгрузил: _______________________ / _________________", st_small),
          Paragraph("Принял: _______________________ / _________________", st_small)]],
        colWidths=[doc.width * 0.5, doc.width * 0.5],
    )
    sign_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(sign_tbl)

    doc.build(story)
    buf.seek(0)
    return buf.read()


# ============================================================================
# Простой упаковочный лист на A4 (одна тара, крупно)
# ============================================================================

def generate_simple_packing_list_pdf(container, company=None) -> bytes:
    """A4-лист на одну тару: крупный код, ШК, артикул, кол-во, вес."""
    from reportlab.pdfgen import canvas as canvaslib

    company = company or {}
    buf = io.BytesIO()
    c = canvaslib.Canvas(buf, pagesize=A4)
    page_w, page_h = A4

    margin = 20 * mm

    c.setFont(FONT_BOLD, 14)
    c.drawString(margin, page_h - 20 * mm, "УПАКОВОЧНЫЙ ЛИСТ")

    c.setFont(FONT_REG, 9)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawRightString(page_w - margin, page_h - 16 * mm,
                      company.get("supplier_name", "") or "")
    c.drawRightString(page_w - margin, page_h - 20 * mm,
                      timezone.localdate().strftime("%d.%m.%Y"))
    c.setFillColor(colors.black)

    c.setLineWidth(1.5)
    c.rect(margin, page_h / 2 - 20 * mm, page_w - 2 * margin, page_h / 2 - 10 * mm)

    c.setFont(FONT_BOLD, 42)
    c.drawCentredString(page_w / 2, page_h - 60 * mm, container.code)

    big_bar_h = 30 * mm
    bc = code128.Code128(container.code, barWidth=0.5 * mm,
                          barHeight=big_bar_h, humanReadable=False)
    bc_w = bc.width
    bc.drawOn(c, (page_w - bc_w) / 2, page_h - 62 * mm - big_bar_h)

    product = container.product
    art = product.article if product else "—"
    name = product.name if product else "—"
    uom = product.get_uom_display() if product else "шт"
    qty = container.quantity or 0

    y = page_h - 62 * mm - big_bar_h - 20 * mm

    c.setFont(FONT_REG, 14)
    c.drawCentredString(page_w / 2, y, f"Артикул: {art}")
    y -= 12 * mm

    # BIG-NAME: крупно, жирно, с переносом на 2 строки при необходимости
    from reportlab.pdfbase.pdfmetrics import stringWidth as _sw

    name_font_size = 30
    max_w = page_w - 2 * margin - 10 * mm

    def _wrap_name(text, font, size, max_width):
        """Разбивает название на 1-2 строки по словам."""
        words = text.split()
        if not words:
            return [text]
        lines = []
        cur = ""
        for w in words:
            cand = (cur + " " + w).strip() if cur else w
            if _sw(cand, font, size) <= max_width:
                cur = cand
            else:
                if cur:
                    lines.append(cur)
                cur = w
                if len(lines) >= 2:
                    break
        if cur and len(lines) < 2:
            lines.append(cur)
        return lines[:2]

    c.setFont(FONT_BOLD, name_font_size)
    name_lines = _wrap_name(name, FONT_BOLD, name_font_size, max_w)
    for i, line in enumerate(name_lines):
        c.drawCentredString(page_w / 2, y - i * 13 * mm, line)
    y -= (13 * mm * len(name_lines)) + 10 * mm

    c.setFont(FONT_BOLD, 40)
    c.drawCentredString(page_w / 2, y, f"{_fmt_qty(qty)} {uom}")
    y -= 20 * mm

    weight_g = (Decimal(str(product.weight_g))
                if product and product.weight_g else Decimal("0"))
    total_kg = (weight_g * Decimal(str(qty))) / Decimal("1000")
    c.setFont(FONT_BOLD, 18)
    c.drawCentredString(page_w / 2, y,
                        f"ВЕС: {total_kg.quantize(Decimal('0.001'))} кг")

    # ── Блок деталей из этой отливки (перенос с этикетки) ─────────
    children = _casting_children(product)
    if children:
        y -= 22 * mm
        c.setStrokeColor(colors.grey)
        c.setLineWidth(0.5)
        c.line(margin, y + 6 * mm, page_w - margin, y + 6 * mm)
        c.setFillColor(colors.HexColor("#444444"))
        c.setFont(FONT_BOLD, 12)
        c.drawCentredString(page_w / 2, y,
                            "ДЕТАЛИ, КОТОРЫЕ ДЕЛАЮТСЯ ИЗ ЭТОЙ ОТЛИВКИ")
        c.setFillColor(colors.black)
        y -= 8 * mm

        # 2 в ряд (компактно и читаемо), максимум 4 ряда = 8 деталей.
        # barWidth считается адаптивно от длины самого длинного кода,
        # чтобы ШК гарантированно влезал в колонку.
        to_draw = children[:8]
        n = len(to_draw)
        cols = 1 if n == 1 else 2
        gap = 10 * mm
        avail_w = page_w - 2 * margin
        col_w = (avail_w - gap * (cols - 1)) / cols

        # Оценка ширины Code128: (L * 11 + 35) модулей.
        max_len = max(len(f"PART:{c.article}") for c in to_draw)
        est_modules = max_len * 11 + 35
        max_bar_w = (col_w - 8 * mm) / est_modules
        bar_w = min(0.4 * mm, max_bar_w)
        if bar_w < 0.2 * mm:
            bar_w = 0.2 * mm

        mini_bar_h = 16 * mm
        row_pitch = mini_bar_h + 14 * mm

        for i, child in enumerate(to_draw):
            col = i % cols
            row = i // cols
            cx = margin + col * (col_w + gap)
            cy_base = y - mini_bar_h - row * row_pitch

            mini = code128.Code128(
                f"PART:{child.article}",
                barWidth=bar_w,
                barHeight=mini_bar_h,
                humanReadable=False,
            )
            offset = (col_w - mini.width) / 2
            if offset < 0:
                offset = 0
            mini.drawOn(c, cx + offset, cy_base)

            c.setFont(FONT_BOLD, 13)
            c.drawCentredString(cx + col_w / 2, cy_base - 6 * mm,
                                child.article)
            c.setFont(FONT_REG, 9)
            c.setFillColor(colors.HexColor("#555555"))
            c.drawCentredString(cx + col_w / 2, cy_base - 10 * mm,
                                child.name[:42])
            c.setFillColor(colors.black)

    c.setFont(FONT_REG, 10)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawCentredString(page_w / 2, 20 * mm,
                        f"Создано: {timezone.localtime(container.created_at).strftime('%d.%m.%Y %H:%M')}")
    c.setFillColor(colors.black)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


def generate_simple_packing_bulk_pdf(containers, company=None) -> bytes:
    """Один PDF с упаковочными листами для нескольких тар.
    Каждая тара — своя страница A4.
    """
    from reportlab.pdfgen import canvas as canvaslib
    from reportlab.lib.pagesizes import A4 as _A4
    from reportlab.lib.units import mm as _mm
    from reportlab.lib import colors as _colors
    from decimal import Decimal as _Decimal

    company = company or {}
    buf = io.BytesIO()
    c = canvaslib.Canvas(buf, pagesize=_A4)
    page_w, page_h = _A4
    margin = 20 * _mm

    for container in containers:
        # ── Заголовок ──
        c.setFont(FONT_BOLD, 14)
        c.drawString(margin, page_h - 20 * _mm, "УПАКОВОЧНЫЙ ЛИСТ")

        c.setFont(FONT_REG, 9)
        c.setFillColor(_colors.HexColor("#666666"))
        c.drawRightString(page_w - margin, page_h - 16 * _mm,
                          company.get("supplier_name", "") or "")
        c.drawRightString(page_w - margin, page_h - 20 * _mm,
                          timezone.localdate().strftime("%d.%m.%Y"))
        c.setFillColor(_colors.black)

        # ── Рамка + код + ШК ──
        c.setLineWidth(1.5)
        c.rect(margin, page_h / 2 - 20 * _mm,
               page_w - 2 * margin, page_h / 2 - 10 * _mm)

        c.setFont(FONT_BOLD, 42)
        c.drawCentredString(page_w / 2, page_h - 60 * _mm, container.code)

        big_bar_h = 30 * _mm
        bc = code128.Code128(container.code, barWidth=0.5 * _mm,
                             barHeight=big_bar_h, humanReadable=False)
        bc.drawOn(c, (page_w - bc.width) / 2, page_h - 62 * _mm - big_bar_h)

        product = container.product
        art = product.article if product else "—"
        name = product.name if product else "—"
        uom = product.get_uom_display() if product else "шт"
        qty = container.quantity or 0

        y = page_h - 62 * _mm - big_bar_h - 20 * _mm

        c.setFont(FONT_REG, 14)
        c.drawCentredString(page_w / 2, y, f"Артикул: {art}")
        y -= 12 * _mm

        c.setFont(FONT_REG, 16)
        c.drawCentredString(page_w / 2, y, name[:60])
        y -= 20 * _mm

        c.setFont(FONT_BOLD, 40)
        c.drawCentredString(page_w / 2, y, f"{_fmt_qty(qty)} {uom}")
        y -= 20 * _mm

        weight_g = (_Decimal(str(product.weight_g))
                    if product and product.weight_g else _Decimal("0"))
        total_kg = (weight_g * _Decimal(str(qty))) / _Decimal("1000")
        c.setFont(FONT_BOLD, 18)
        c.drawCentredString(page_w / 2, y,
                            f"ВЕС: {total_kg.quantize(_Decimal('0.001'))} кг")

        # ── Детали из отливки ──
        children = _casting_children(product)
        if children:
            y -= 22 * _mm
            c.setStrokeColor(_colors.grey)
            c.setLineWidth(0.5)
            c.line(margin, y + 6 * _mm, page_w - margin, y + 6 * _mm)
            c.setFillColor(_colors.HexColor("#444444"))
            c.setFont(FONT_BOLD, 12)
            c.drawCentredString(
                page_w / 2, y, "ДЕТАЛИ, КОТОРЫЕ ДЕЛАЮТСЯ ИЗ ЭТОЙ ОТЛИВКИ")
            c.setFillColor(_colors.black)
            y -= 8 * _mm

            to_draw = children[:8]
            n = len(to_draw)
            cols = 1 if n == 1 else 2
            gap = 10 * _mm
            avail_w = page_w - 2 * margin
            col_w = (avail_w - gap * (cols - 1)) / cols

            max_len = max(len(f"PART:{c.article}") for c in to_draw)
            est_modules = max_len * 11 + 35
            max_bar_w = (col_w - 8 * _mm) / est_modules
            bar_w = min(0.4 * _mm, max_bar_w)
            if bar_w < 0.2 * _mm:
                bar_w = 0.2 * _mm

            mini_bar_h = 16 * _mm
            row_pitch = mini_bar_h + 14 * _mm

            for i, child in enumerate(to_draw):
                col = i % cols
                row = i // cols
                cx = margin + col * (col_w + gap)
                cy_base = y - mini_bar_h - row * row_pitch

                mini = code128.Code128(
                    f"PART:{child.article}",
                    barWidth=bar_w, barHeight=mini_bar_h,
                    humanReadable=False,
                )
                offset = (col_w - mini.width) / 2
                if offset < 0:
                    offset = 0
                mini.drawOn(c, cx + offset, cy_base)

                c.setFont(FONT_BOLD, 13)
                c.drawCentredString(cx + col_w / 2, cy_base - 6 * _mm,
                                    child.article)
                c.setFont(FONT_REG, 9)
                c.setFillColor(_colors.HexColor("#555555"))
                c.drawCentredString(cx + col_w / 2, cy_base - 10 * _mm,
                                    child.name[:42])
                c.setFillColor(_colors.black)

        # ── Низ страницы ──
        c.setFont(FONT_REG, 10)
        c.setFillColor(_colors.HexColor("#666666"))
        c.drawCentredString(
            page_w / 2, 20 * _mm,
            f"Создано: {timezone.localtime(container.created_at).strftime('%d.%m.%Y %H:%M')}")
        c.setFillColor(_colors.black)

        c.showPage()

    c.save()
    buf.seek(0)
    return buf.read()


def generate_packing_bulk_pdf(containers, company) -> bytes:
    """Один PDF со страницами полных упаковочных листов (форма Ромашка).
    Склеивает одиночные generate_packing_list_pdf через pypdf.
    """
    from pypdf import PdfWriter, PdfReader
    import io as _io

    writer = PdfWriter()
    for c in containers:
        art_before = _resolve_art_before(c.product)
        pdf_bytes = generate_packing_list_pdf(
            c, company, art_before_override=art_before,
        )
        reader = PdfReader(_io.BytesIO(pdf_bytes))
        for page in reader.pages:
            writer.add_page(page)

    out = _io.BytesIO()
    writer.write(out)
    return out.getvalue()
