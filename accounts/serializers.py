import secrets

from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Только для чтения."""

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "phone",
            "is_superuser",
            "is_staff",
            "is_active",
        )
        read_only_fields = ("id", "is_superuser", "is_staff")


class UserWriteSerializer(serializers.ModelSerializer):
    """Для создания/редактирования через админский CRUD."""

    password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        min_length=4,
        label="Пароль",
        help_text="При создании обязателен. При редактировании — оставьте пустым, чтобы не менять.",
    )

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "first_name",
            "last_name",
            "email",
            "phone",
            "role",
            "is_active",
            "password",
        )
        read_only_fields = ("id",)

    def validate_username(self, value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Логин обязателен")
        qs = User.objects.filter(username__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Такой логин уже занят")
        return value

    def validate(self, attrs):
        creating = self.instance is None
        pwd = (attrs.get("password") or "").strip()
        if creating and not pwd:
            raise serializers.ValidationError(
                {"password": "Пароль обязателен при создании пользователя"}
            )
        # Защита: нельзя понизить роль последнего администратора
        if not creating and "role" in attrs:
            was_admin = (
                self.instance.role == User.Role.ADMIN or self.instance.is_superuser
            )
            now_admin = attrs.get("role") == User.Role.ADMIN
            if was_admin and not now_admin:
                remaining = User.objects.filter(
                    role=User.Role.ADMIN, is_active=True
                ).exclude(pk=self.instance.pk).count()
                if remaining == 0 and not self.instance.is_superuser:
                    raise serializers.ValidationError(
                        {"role": "Нельзя понизить последнего администратора"}
                    )
        return attrs

    def create(self, validated_data):
        pwd = (validated_data.pop("password", "") or "").strip()
        if not pwd:
            pwd = secrets.token_urlsafe(12)
        user = User(**validated_data)
        user.set_password(pwd)
        user.save()
        return user

    def update(self, instance, validated_data):
        pwd = (validated_data.pop("password", "") or "").strip()
        for k, v in validated_data.items():
            setattr(instance, k, v)
        if pwd:
            instance.set_password(pwd)
        instance.save()
        return instance


class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["username"] = user.username
        token["role"] = user.role
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data
