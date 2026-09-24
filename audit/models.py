from django.conf import settings
from django.db import models


class AuditAction(models.TextChoices):
    CREATE = "create", "Создано"
    UPDATE = "update", "Изменено"
    DELETE = "delete", "Удалено"


class AuditLog(models.Model):
    created_at = models.DateTimeField(
        auto_now_add=True, db_index=True, verbose_name="Когда")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="audit_logs",
        verbose_name="Кто",
    )
    user_repr = models.CharField(
        max_length=150, blank=True, default="",
        verbose_name="Кто (имя)",
        help_text="Снимок, чтобы не терять имя при удалении пользователя")

    action = models.CharField(
        max_length=16, choices=AuditAction.choices,
        db_index=True, verbose_name="Действие")
    model_name = models.CharField(
        max_length=64, db_index=True, verbose_name="Модель")
    object_id = models.BigIntegerField(db_index=True, verbose_name="ID объекта")
    object_repr = models.CharField(
        max_length=255, blank=True, default="", verbose_name="Объект")

    changes = models.JSONField(
        blank=True, default=dict, verbose_name="Что изменилось")
    snapshot = models.JSONField(
        blank=True, default=dict, verbose_name="Снимок полей")

    class Meta:
        verbose_name = "Запись аудита"
        verbose_name_plural = "Журнал изменений"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["model_name", "object_id"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        ts = self.created_at.strftime("%d.%m.%Y %H:%M") if self.created_at else "—"
        return f"{ts} · {self.user_repr or '—'} · {self.get_action_display()} {self.model_name}"
