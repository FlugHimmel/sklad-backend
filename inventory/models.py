from django.db import models


class UnitOfMeasure(models.TextChoices):
    PCS = "pcs", "шт"
    KG = "kg", "кг"


class ProductType(models.TextChoices):
    RAW = "raw", "Сырьё"
    CASTING = "casting", "Заготовка / литьё"
    PART = "part", "Деталь"
    FINISHED = "finished", "Готовая продукция"


class Category(models.Model):
    """Категории, дерево."""

    name = models.CharField(max_length=200, verbose_name="Название")
    code = models.CharField(max_length=32, blank=True, default="", verbose_name="Код")
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        verbose_name="Родитель",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активна")

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    """Артикул — отдельная запись. 4 типа: сырьё, заготовка, деталь, готовая."""

    article = models.CharField(
        max_length=64, unique=True, verbose_name="Артикул"
    )
    name = models.CharField(max_length=255, verbose_name="Наименование")
    product_type = models.CharField(
        max_length=16, choices=ProductType.choices, verbose_name="Тип"
    )
    category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products",
        verbose_name="Категория",
    )
    uom = models.CharField(
        max_length=8,
        choices=UnitOfMeasure.choices,
        default=UnitOfMeasure.PCS,
        verbose_name="Ед. изм.",
    )
    weight_g = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        verbose_name="Вес единицы, г",
        help_text="Вес одной штуки в граммах (например, 2186 для 2.186 кг)",
    )
    min_stock = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=0,
        verbose_name="Мин. остаток",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    # ДУБЛИ АРТИКУЛОВ: если этот артикул — старый/альтернативный,
    # ссылается на главный. Reports и агрегация идут по главному.
    alias_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="aliases",
        verbose_name="Главный артикул",
        help_text="Если этот артикул — старый/дублирующий, укажите главный. "
                  "Иначе пусто.",
    )

    # Слоты МО1..МО8 — только для типа «Заготовка / литьё».
    # Хранят ссылки на детали-варианты, которые можно сделать из этой отливки.
    mo1 = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="casting_mo1",
        limit_choices_to={"product_type": ProductType.PART},
        verbose_name="МО слот 1",
    )
    mo2 = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="casting_mo2",
        limit_choices_to={"product_type": ProductType.PART},
        verbose_name="МО слот 2",
    )
    mo3 = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="casting_mo3",
        limit_choices_to={"product_type": ProductType.PART},
        verbose_name="МО слот 3",
    )
    mo4 = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="casting_mo4",
        limit_choices_to={"product_type": ProductType.PART},
        verbose_name="МО слот 4",
    )
    mo5 = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="casting_mo5",
        limit_choices_to={"product_type": ProductType.PART},
        verbose_name="МО слот 5",
    )
    mo6 = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="casting_mo6",
        limit_choices_to={"product_type": ProductType.PART},
        verbose_name="МО слот 6",
    )
    mo7 = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="casting_mo7",
        limit_choices_to={"product_type": ProductType.PART},
        verbose_name="МО слот 7",
    )
    mo8 = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="casting_mo8",
        limit_choices_to={"product_type": ProductType.PART},
        verbose_name="МО слот 8",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Артикул"
        verbose_name_plural = "Номенклатура"
        ordering = ("article",)
        indexes = [
            models.Index(fields=["product_type"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["alias_of"]),
        ]

    def __str__(self) -> str:
        if self.alias_of_id:
            return f"{self.article} (дубль {self.alias_of.article})"
        return f"{self.article} — {self.name}"

    # ─── ХЕЛПЕРЫ ДЛЯ РАБОТЫ С ДУБЛЯМИ ─────────────────────────────
    @property
    def canonical_id(self) -> int:
        """ID главного продукта (себя, если сам главный)."""
        return self.alias_of_id or self.id

    def canonical(self):
        """Возвращает главный продукт (себя, если сам главный)."""
        return self.alias_of if self.alias_of_id else self


class BOM(models.Model):
    """Спецификация (Bill of Materials). Одна активная на продукт."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE,
        related_name="boms", verbose_name="Продукт",
    )
    version = models.CharField(max_length=16, default="1.0", verbose_name="Версия")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    note = models.CharField(max_length=255, blank=True, default="", verbose_name="Примечание")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Спецификация"
        verbose_name_plural = "Спецификации"
        unique_together = [("product", "version")]
        ordering = ("product", "-is_active", "-created_at")

    def __str__(self) -> str:
        return f"BOM {self.product.article} v{self.version}"


class BOMLine(models.Model):
    """Строка спецификации: компонент + количество."""

    bom = models.ForeignKey(
        BOM, on_delete=models.CASCADE,
        related_name="lines", verbose_name="Спецификация",
    )
    component = models.ForeignKey(
        Product, on_delete=models.PROTECT,
        related_name="used_in_boms", verbose_name="Компонент",
    )
    quantity = models.DecimalField(
        max_digits=14, decimal_places=3, verbose_name="Количество"
    )
    note = models.CharField(max_length=255, blank=True, default="", verbose_name="Примечание")

    class Meta:
        verbose_name = "Строка спецификации"
        verbose_name_plural = "Строки спецификации"

    def __str__(self) -> str:
        return f"{self.component.article} × {self.quantity}"
