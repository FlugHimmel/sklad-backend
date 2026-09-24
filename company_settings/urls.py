from rest_framework.routers import DefaultRouter

from .views import CompanySettingsViewSet

router = DefaultRouter()
router.register("company-settings", CompanySettingsViewSet, basename="company-settings")
urlpatterns = router.urls
