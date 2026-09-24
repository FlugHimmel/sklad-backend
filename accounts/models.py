from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Кастомный пользователь. Одна базовая роль + суперюзер админ."""

    class Role(models.TextChoices):
        USER = "user", "Пользователь"
        FOUNDRY = "foundry", "Литейка"
        ADMIN = "admin", "Администратор"

    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.USER,
        verbose_name="Роль",
    )
    phone = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name="Телефон",
    )

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self) -> str:
        full = self.get_full_name().strip()
        return full or self.username
