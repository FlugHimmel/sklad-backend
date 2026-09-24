from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import (
    MyTokenObtainPairSerializer,
    UserSerializer,
    UserWriteSerializer,
)

User = get_user_model()


class IsAdminRole(BasePermission):
    """Разрешает только role=admin или superuser."""

    def has_permission(self, request, view):
        u = request.user
        return bool(
            u
            and u.is_authenticated
            and (u.is_superuser or getattr(u, "role", "") == User.Role.ADMIN)
        )


class LoginView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class UserViewSet(viewsets.ModelViewSet):
    """
    list/retrieve — всем авторизованным (нужно для выбора оператора).
    create/update/destroy — только админ.
    """

    queryset = User.objects.all().order_by("username")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return UserWriteSerializer
        return UserSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAuthenticated(), IsAdminRole()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = super().get_queryset()
        # По умолчанию возвращаем всех, включая неактивных —
        # админ должен их видеть в списке. Если нужно только активных:
        only_active = self.request.query_params.get("only_active")
        if only_active in ("1", "true"):
            qs = qs.filter(is_active=True)
        return qs

    def perform_destroy(self, instance):
        me = self.request.user
        if instance.pk == me.pk:
            raise ValidationError({"error": "Нельзя удалить собственную учётную запись"})
        # Защита последнего админа
        if instance.role == User.Role.ADMIN or instance.is_superuser:
            remaining = (
                User.objects.filter(role=User.Role.ADMIN, is_active=True)
                .exclude(pk=instance.pk)
                .count()
            )
            if remaining == 0 and not instance.is_superuser:
                raise ValidationError({"error": "Нельзя удалить последнего администратора"})
        instance.delete()

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        # Нельзя менять себе роль и is_active
        if instance.pk == request.user.pk:
            if "role" in request.data and request.data["role"] != instance.role:
                return Response(
                    {"error": "Нельзя менять собственную роль"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if "is_active" in request.data and not request.data["is_active"]:
                return Response(
                    {"error": "Нельзя деактивировать собственную учётную запись"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return super().update(request, *args, **kwargs)
