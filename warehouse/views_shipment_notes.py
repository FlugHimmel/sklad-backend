from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from company_settings.models import CompanySettings

from .models import Container, ShipmentNote, ShipmentNoteLine
from .pdf_from_template import (
    generate_from_docx_template,
    generate_bulk_from_docx_template,
    build_data_for_container,
)
from .pdf import (
    generate_packing_list_pdf,
    generate_packing_bulk_pdf,
    generate_shipment_note_pdf,
    generate_simple_packing_list_pdf,
    generate_simple_packing_bulk_pdf,
)
from .serializers import ShipmentNoteSerializer


def _is_foundry(user) -> bool:
    return bool(user and getattr(user, "role", None) == "foundry")


def _company_dict(user):
    cs = CompanySettings.objects.first()
    if not cs:
        return {}
    return cs.as_dict_for(user)


class ContainerPackingListPdfView(APIView):
    """GET /api/containers/<int:pk>/simple-packing-pdf/

    Формат PDF зависит от роли:
      * foundry → простой лист (крупный код + ШК, для литейки)
      * остальные → полный лист по форме Ромашка
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            c = Container.objects.select_related("product", "warehouse").get(pk=pk)
        except Container.DoesNotExist:
            return Response({"error": "Тара не найдена"},
                            status=status.HTTP_404_NOT_FOUND)

        if _is_foundry(request.user):
            company = _company_dict(request.user)
            pdf_bytes = generate_simple_packing_list_pdf(c, company)
            filename = f"packing-simple-{c.code}.pdf"
        else:
            # Админ/менеджер — рендерим через docx-шаблон Ромашка
            import os
            template_path = "/opt/sklad/templates/packing_vilo.docx"
            if os.path.exists(template_path):
                data = build_data_for_container(c)
                pdf_bytes = generate_from_docx_template(
                    template_path, data["main"], rows_data=data["rows"],
                )
            else:
                # Fallback: старый код если шаблона нет
                company = _company_dict(request.user)
                art_before = request.query_params.get("art_before")
                pdf_bytes = generate_packing_list_pdf(
                    c, company, art_before_override=art_before,
                )
            filename = f"packing-{c.code}.pdf"

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


class BulkSimplePackingPdfView(APIView):
    """POST /api/containers/bulk-packing-pdf/
    Body: {"ids": [1, 2, 3]}

    Формат зависит от роли:
      * foundry → простые листы (bulk)
      * остальные → полные листы по форме Ромашка (склеены через pypdf)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        ids = request.data.get("ids") or []
        if not ids or not isinstance(ids, list):
            return Response({"error": "ids обязателен (список ID)"},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(ids) > 200:
            return Response({"error": "Максимум 200 тар за раз"},
                            status=status.HTTP_400_BAD_REQUEST)

        containers = list(
            Container.objects.filter(pk__in=ids)
            .select_related("product", "warehouse")
            .prefetch_related("lines__product")
        )
        if not containers:
            return Response({"error": "Ни одной тары не найдено"},
                            status=status.HTTP_404_NOT_FOUND)

        by_id = {c.id: c for c in containers}
        ordered = [by_id[i] for i in ids if i in by_id]

        if _is_foundry(request.user):
            company = _company_dict(request.user)
            pdf_bytes = generate_simple_packing_bulk_pdf(ordered, company)
            filename = f"packing-bulk-simple-{len(ordered)}.pdf"
        else:
            # Админ/менеджер — docx-шаблон Ромашка
            import os
            template_path = "/opt/sklad/templates/packing_vilo.docx"
            if os.path.exists(template_path):
                data = [build_data_for_container(c) for c in ordered]
                pdf_bytes = generate_bulk_from_docx_template(
                    template_path, data,
                )
            else:
                company = _company_dict(request.user)
                pdf_bytes = generate_packing_bulk_pdf(ordered, company)
            filename = f"packing-bulk-{len(ordered)}.pdf"

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response


# ───────────────────────────────────────────────────────────────────────
# Накладные (не трогаем)
# ───────────────────────────────────────────────────────────────────────

def _next_note_number(year: int) -> tuple:
    """Возвращает (номер_строкой, seq). Формат: ЗАВ-{year}-{seq:04d}."""
    from warehouse.models import ShipmentNote
    max_seq = (
        ShipmentNote.objects.filter(year=year)
        .order_by("-seq").values_list("seq", flat=True).first()
    ) or 0
    seq = max_seq + 1
    return f"ЗАВ-{year}-{seq:04d}", seq


class ShipmentNoteCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        codes = request.data.get("codes") or []
        comment = request.data.get("comment") or ""
        if not codes:
            return Response({"error": "codes пуст"},
                            status=status.HTTP_400_BAD_REQUEST)

        containers = list(
            Container.objects.filter(code__in=codes)
            .select_related("product", "warehouse")
            .prefetch_related("lines__product")
        )
        if not containers:
            return Response({"error": "Ни одной тары не найдено"},
                            status=status.HTTP_404_NOT_FOUND)

        from decimal import Decimal as _D
        from django.utils import timezone as _tz

        today = _tz.localdate()
        year = today.year
        number, seq = _next_note_number(year)

        company = _company_dict(request.user)

        # Считаем итоги по тарам
        total_qty = _D("0")
        total_kg = _D("0")
        for c in containers:
            for line in c.lines.all():
                total_qty += line.quantity
                wg = line.product.weight_g if line.product and line.product.weight_g else _D("0")
                total_kg += (_D(str(wg)) * line.quantity) / _D("1000")

        note = ShipmentNote.objects.create(
            number=number,
            year=year,
            seq=seq,
            note_date=today,
            from_name=company.get("supplier_name", "") or "",
            from_address=company.get("supplier_address", "") or "",
            to_name=company.get("customer_name", "") or "",
            to_address=company.get("customer_address", "") or "",
            total_qty=total_qty,
            total_weight_kg=total_kg.quantize(_D("0.001")),
            comment=comment,
            created_by=request.user,
        )

        line_seq = 1
        for c in containers:
            for line in c.lines.all():
                wg = line.product.weight_g if line.product and line.product.weight_g else _D("0")
                line_kg = (_D(str(wg)) * line.quantity) / _D("1000")
                ShipmentNoteLine.objects.create(
                    note=note,
                    container=c,
                    code=c.code,
                    product_article=line.product.article if line.product else "",
                    product_name=line.product.name if line.product else "",
                    uom="шт",
                    quantity=line.quantity,
                    weight_kg=line_kg.quantize(_D("0.001")),
                    sequence=line_seq,
                )
                line_seq += 1

        note = ShipmentNote.objects.prefetch_related("lines").get(pk=note.pk)
        return Response(ShipmentNoteSerializer(note).data,
                        status=status.HTTP_201_CREATED)


class ShipmentNoteListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # NEW-PAGINATED
        from django.db.models import Q as _Q

        qs = ShipmentNote.objects.all().order_by("-created_at")

        # Поиск по номеру
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                _Q(number__icontains=search)
                | _Q(from_name__icontains=search)
                | _Q(to_name__icontains=search)
            )

        # Фильтр по датам
        df = request.query_params.get("date_from")
        dt = request.query_params.get("date_to")
        if df:
            qs = qs.filter(note_date__gte=df)
        if dt:
            qs = qs.filter(note_date__lte=dt)

        # Пагинация
        try:
            page = int(request.query_params.get("page", "1"))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.query_params.get("page_size", "50"))
        except (TypeError, ValueError):
            page_size = 50
        page = max(1, page)
        page_size = max(1, min(200, page_size))

        count = qs.count()
        start = (page - 1) * page_size
        end = start + page_size
        items = list(qs[start:end])

        return Response({
            "count": count,
            "page": page,
            "page_size": page_size,
            "items": ShipmentNoteSerializer(items, many=True).data,
        })


class ShipmentNoteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            note = ShipmentNote.objects.get(pk=pk)
        except ShipmentNote.DoesNotExist:
            return Response({"error": "Накладная не найдена"},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(ShipmentNoteSerializer(note).data)


class ShipmentNotePdfView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            note = ShipmentNote.objects.get(pk=pk)
        except ShipmentNote.DoesNotExist:
            return Response({"error": "Накладная не найдена"},
                            status=status.HTTP_404_NOT_FOUND)
        pdf_bytes = generate_shipment_note_pdf(note)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="note-{note.number}.pdf"'
        )
        return response





class CreateNoteForTodayView(APIView):
    """POST /api/shipment-notes/create-for-today/

    Формирует накладную на все тары, которые СЕЙЧАС на Завод,
    не отгружены и ещё не попали ни в одну накладную.
    Тары после создания помечаются как «в накладной» (через ShipmentNoteLine).

    Body (опционально): {"comment": "..."}
    Response: сериализованная накладная + список кодов тар.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from decimal import Decimal as _D
        from django.utils import timezone as _tz

        comment = (request.data.get("comment") or "").strip()

        # TODAY-CHECK: не даём создать вторую накладную за сегодня
        today = _tz.localdate()
        existing = ShipmentNote.objects.filter(note_date=today).first()
        if existing:
            return Response(
                {"error": f"Накладная за сегодня уже создана: "
                          f"{existing.number}. Завтра можно создать новую."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 1. Ищем подходящие тары ──────────────────────────────
        qs = (
            Container.objects
            .filter(
                warehouse__code="ZLK",
                shipped_at__isnull=True,
            )
            .exclude(shipment_lines__isnull=False)  # не в других накладных
            .select_related("product", "warehouse")
            .prefetch_related("lines__product")
            .order_by("code")
        )

        containers = list(qs)
        if not containers:
            return Response(
                {"error": "Нет тар для отгрузки за сегодня. "
                          "Создай тары на Завод, или они уже в накладной."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 2. Считаем итоги ────────────────────────────────────
        total_qty = _D("0")
        total_kg = _D("0")
        for c in containers:
            for line in c.lines.all():
                total_qty += line.quantity
                wg = line.product.weight_g if line.product and line.product.weight_g else _D("0")
                total_kg += (_D(str(wg)) * line.quantity) / _D("1000")

        # ── 3. Создаём накладную ────────────────────────────────
        today = _tz.localdate()
        year = today.year
        number, seq = _next_note_number(year)
        company = _company_dict(request.user)

        from django.db import transaction
        with transaction.atomic():
            note = ShipmentNote.objects.create(
                number=number,
                year=year,
                seq=seq,
                note_date=today,
                from_name=company.get("supplier_name", "") or "",
                from_address=company.get("supplier_address", "") or "",
                to_name=company.get("customer_name", "") or "",
                to_address=company.get("customer_address", "") or "",
                total_qty=total_qty,
                total_weight_kg=total_kg.quantize(_D("0.001")),
                comment=comment,
                created_by=request.user,
            )

            line_seq = 1
            for c in containers:
                for line in c.lines.all():
                    wg = line.product.weight_g if line.product and line.product.weight_g else _D("0")
                    line_kg = (_D(str(wg)) * line.quantity) / _D("1000")
                    ShipmentNoteLine.objects.create(
                        note=note,
                        container=c,
                        code=c.code,
                        product_article=line.product.article if line.product else "",
                        product_name=line.product.name if line.product else "",
                        uom="шт",
                        quantity=line.quantity,
                        weight_kg=line_kg.quantize(_D("0.001")),
                        sequence=line_seq,
                    )
                    line_seq += 1

        note = ShipmentNote.objects.prefetch_related("lines").get(pk=note.pk)
        data = ShipmentNoteSerializer(note).data
        data["container_codes"] = [c.code for c in containers]
        return Response(data, status=status.HTTP_201_CREATED)



# ───────────────────────────────────────────────────────────────────────
# Утилиты для даты
# ───────────────────────────────────────────────────────────────────────
def timezone_now_year():
    from django.utils import timezone
    return timezone.localdate().year


def timezone_now_date():
    from django.utils import timezone
    return timezone.localdate()
