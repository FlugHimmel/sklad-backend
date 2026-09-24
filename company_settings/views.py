from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated

from .models import CompanySettings
from .serializers import CompanySettingsSerializer


class CompanySettingsViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = CompanySettings.objects.all()
    serializer_class = CompanySettingsSerializer
    permission_classes = [IsAuthenticated]
