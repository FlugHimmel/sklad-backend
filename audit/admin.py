from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user_repr", "action", "model_name",
                    "object_repr")
    list_filter = ("action", "model_name")
    search_fields = ("object_repr", "user_repr")
    readonly_fields = tuple(f.name for f in AuditLog._meta.fields)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
