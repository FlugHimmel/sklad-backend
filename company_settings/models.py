from django.db import models


class CompanySettings(models.Model):
    """Синглтон: реквизиты для шапки упаковочного листа.

    Есть два блока:
      * Обычный (для администратора / менеджера) — печатает Модель → Ромашка.
      * Foundry (для литейки) — печатает Завод → Модель.
    При генерации PDF смотрим роль пользователя и берём нужный блок.
    """

    # ─── ОБЫЧНЫЙ БЛОК (админ/менеджер) ───────────────────────────────
    ownership_note = models.CharField(
        max_length=255,
        default="СОБСТВЕННОСТЬ ООО «Ромашка»",
        verbose_name="Строка собственности",
    )
    packing_list_title = models.CharField(
        max_length=128,
        default="УПАКОВОЧНЫЙ ЛИСТ",
        verbose_name="Заголовок документа",
    )

    customer_name = models.CharField(
        max_length=255,
        default="ООО «Ромашка»",
        verbose_name="Наименование заказчика",
    )
    customer_address = models.TextField(
        default=(
            "000000, г. Город, ул. Примерная, "
            "д. 1, офис 1.\n"
            "Склад: 000000, РФ, обл. Примерная, р-н Примерный, "
            "пос. Примерный, промплощадка №1, д. 1"
        ),
        verbose_name="Адрес заказчика",
    )

    supplier_name = models.CharField(
        max_length=255,
        default="ООО «Пример»",
        verbose_name="Наименование поставщика",
    )
    supplier_address = models.TextField(
        default="г. Город, Производственный проезд, 1.",
        verbose_name="Адрес поставщика",
    )

    # ─── БЛОК ДЛЯ ЛИТЕЙКИ ────────────────────────────────────────────
    foundry_ownership_note = models.CharField(
        max_length=255,
        default="СОБСТВЕННОСТЬ ООО «ПРИМЕР»",
        verbose_name="Строка собственности (литейка)",
    )
    foundry_customer_name = models.CharField(
        max_length=255,
        default="ООО «Пример»",
        verbose_name="Заказчик (литейка)",
    )
    foundry_customer_address = models.TextField(
        default="Республика Примерная, город Примерный, "
                "Производственный проезд, 1",
        verbose_name="Адрес заказчика (литейка)",
    )
    foundry_supplier_name = models.CharField(
        max_length=255,
        default="ООО «Завод»",
        verbose_name="Поставщик (литейка)",
    )
    foundry_supplier_address = models.TextField(
        default="Республика Примерная, Примерный район, "
                "поселок Примерный, ул. Полевая 1",
        verbose_name="Адрес поставщика (литейка)",
    )

    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Реквизиты компании"
        verbose_name_plural = "Реквизиты компании"

    def __str__(self) -> str:
        return "Реквизиты компании"

    def save(self, *args, **kwargs):
        # Гарантируем синглтон (pk=1)
        self.pk = 1
        super().save(*args, **kwargs)

    # ─── Хелпер: собрать dict для PDF с учётом роли ───────────────────
    def as_dict_for(self, user=None) -> dict:
        """Возвращает реквизиты в виде dict, подставляя нужный блок.
        Литейка → блок foundry_*. Все остальные → обычный блок.
        """
        is_foundry = bool(user and getattr(user, "role", None) == "foundry")
        if is_foundry:
            return {
                "ownership_note": self.foundry_ownership_note,
                "packing_list_title": self.packing_list_title,
                "customer_name": self.foundry_customer_name,
                "customer_address": self.foundry_customer_address,
                "supplier_name": self.foundry_supplier_name,
                "supplier_address": self.foundry_supplier_address,
            }
        return {
            "ownership_note": self.ownership_note,
            "packing_list_title": self.packing_list_title,
            "customer_name": self.customer_name,
            "customer_address": self.customer_address,
            "supplier_name": self.supplier_name,
            "supplier_address": self.supplier_address,
        }
