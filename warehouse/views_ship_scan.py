"""
API-эндпоинт сканирующей отгрузки:
принимает список кодов тар и полностью списывает+удаляет их.
"""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .ship_scan_service import ship_and_delete_by_codes


class BulkShipDeleteView(APIView):
    """
    POST /api/containers/bulk-ship-delete/

    Body:
      {
        "codes": ["TARA-000001", "TARA-000002", ...],
        "comment": "Отгрузка клиенту от 2026-01-01"
      }

    Ответ:
      {
        "shipped":  [{"container_id": N, "code": "...", "quantity": "...", "warehouse_name": "..."}],
        "not_found": ["..."],
        "errors":   [{"code": "...", "error": "..."}],
        "skipped":  [{"code": "...", "reason": "..."}],
        "total_shipped": N
      }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        codes = request.data.get("codes") or []
        comment = request.data.get("comment", "") or ""

        if not codes:
            return Response(
                {"error": "codes пуст"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(codes) > 500:
            return Response(
                {"error": "Максимум 500 тар за раз"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        shipped, not_found, errors, skipped = ship_and_delete_by_codes(
            codes=codes, user=request.user, comment=comment,
        )

        return Response({
            "shipped": shipped,
            "not_found": not_found,
            "errors": errors,
            "skipped": skipped,
            "total_shipped": len(shipped),
        })
