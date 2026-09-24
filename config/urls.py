from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"status": "ok", "service": "sklad"})


from warehouse.views import (
    AdminResetDataView, ProductionSummaryView, VersionView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/auth/", include("accounts.urls")),
    path("api/audit/", include("audit.urls")),
    path("api/version/", VersionView.as_view(), name="version"),
    path("api/reports/production-summary/", ProductionSummaryView.as_view(), name="reports-production-summary"),
    path("api/admin/reset-data/", AdminResetDataView.as_view(), name="admin-reset-data"),
    path("api/", include("company_settings.urls")),
    path("api/", include("inventory.urls")),
    path("api/", include("orders.urls")),
    path("api/", include("warehouse.urls")),
]
