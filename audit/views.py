from django.db.models import Count, Q
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.views import IsAdminRole
from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditPagination(PageNumberPagination):
    page_size = 200
    page_size_query_param = "page_size"
    max_page_size = 2000


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.select_related("user").all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated, IsAdminRole]
    pagination_class = AuditPagination

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params

        model = p.get("model")
        if model:
            qs = qs.filter(model_name=model)
        action = p.get("action")
        if action:
            qs = qs.filter(action=action)
        uid = p.get("user")
        if uid:
            qs = qs.filter(user_id=uid)
        df = p.get("date_from")
        if df:
            qs = qs.filter(created_at__gte=df)
        dt = p.get("date_to")
        if dt:
            # FIX-DATE-TO: date_to включительно, до конца дня
            from datetime import datetime as _dt, timedelta as _td
            try:
                end = _dt.strptime(dt, "%Y-%m-%d") + _td(days=1)
                qs = qs.filter(created_at__lt=end)
            except (ValueError, TypeError):
                qs = qs.filter(created_at__lte=dt)
        search = p.get("search")
        if search:
            qs = qs.filter(
                Q(object_repr__icontains=search)
                | Q(user_repr__icontains=search)
                | Q(model_name__icontains=search)
            )
        return qs

    @action(detail=False, methods=["get"], url_path="models")
    def list_models(self, request):
        rows = (AuditLog.objects.values("model_name")
                .annotate(cnt=Count("id")).order_by("-cnt"))
        return Response(list(rows))
