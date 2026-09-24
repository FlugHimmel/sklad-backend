from django.contrib import admin

from .models import CompanySettings


@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Документ", {"fields": ("ownership_note", "packing_list_title")}),
        ("Заказчик", {"fields": ("customer_name", "customer_address")}),
        ("Поставщик", {"fields": ("supplier_name", "supplier_address")}),
    )
    readonly_fields = ()

    def has_add_permission(self, request):
        # Разрешаем «Add» только если записи ещё нет
        return not CompanySettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
