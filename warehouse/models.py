from decimal import Decimal
from django.conf import settings
from django.db import models, transaction
from django.db.models import Sum

from inventory.models import Product


class Warehouse(models.Model):
    name = models.CharField(max_length=128, unique=True, verbose_name="Название")
    code = models.CharField(max_length=32, unique=True, verbose_name="Код")
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        verbose_name = "Склад"
        verbose_name_plural = "Склады"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Stock(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE,
        related_name="stocks", verbose_name="Склад")
    product = models.ForeignKey(Product, on_delete=models.PROTECT,
        related_name="stocks", verbose_name="Артикул")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Остаток")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Остаток"
        verbose_name_plural = "Остатки"
        unique_together = [("warehouse", "product")]
        indexes = [models.Index(fields=["warehouse", "product"])]

    def __str__(self) -> str:
        return f"{self.warehouse.name} / {self.product.article}: {self.quantity}"


class ContainerStatus(models.TextChoices):
    WAREHOUSE       = "warehouse",       "На складке (заготовка)"
    IN_PRODUCTION   = "in_production",   "В работе (литАрт → мехАрт)"
    WAITING_MILLING = "waiting_milling", "Ждёт фрезеровки"
    WAITING_PACKING = "waiting_packing", "Ждёт упаковки"
    READY_TO_SHIP   = "ready_to_ship",   "Готов к отгрузке"
    SHIPPED         = "shipped",         "Отгружена"
    EMPTY           = "empty",           "Пустая (болванка)"
    WRITTEN_OFF     = "written_off",     "Списана"


class Container(models.Model):
    code = models.CharField(max_length=32, unique=True, blank=True,
        verbose_name="Код тары",
        help_text="Оставьте пустым — сгенерируется автоматически (TARA-XXXXXX)")
    product = models.ForeignKey(Product, null=True, blank=True,
        on_delete=models.PROTECT, related_name="containers",
        verbose_name="Главный артикул (кэш первой строки)")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Общее количество (сумма строк)")
    status = models.CharField(max_length=16, choices=ContainerStatus.choices,
        default=ContainerStatus.WAREHOUSE, verbose_name="Статус (legacy)")
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True,
        on_delete=models.PROTECT, related_name="containers",
        verbose_name="Текущий склад")
    order = models.ForeignKey("orders.Order", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="containers", verbose_name="Заказ")

    # НОВОЕ: свободная метка (то, что ты сам пишешь: «токарка», «на упаковку» и т.п.)
    label = models.CharField(max_length=64, blank=True, default="",
        verbose_name="Метка",
        help_text="Свободный текст: «токарка», «на упаковку», «задел» и т.п.")

    note = models.CharField(max_length=255, blank=True, default="",
        verbose_name="Примечание")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    label_printed_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Этикетка напечатана",
        help_text="Когда в последний раз печатали этикетку Code128")

    # НОВОЕ: упаковка. Если заполнено — тара готова к отгрузке.
    packed_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Упакована",
        help_text="Когда упаковали. Пусто — ещё не готова к отгрузке.")

    # НОВОЕ: отгрузка. Если заполнено — тара уехала, в списках не показываем.
    shipped_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Отгружена",
        help_text="Если заполнено — тара ушла со склада, в списках не показывается")

    class Meta:
        verbose_name = "Тара"
        verbose_name_plural = "Тара"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["shipped_at"]),
            models.Index(fields=["label"]),
            models.Index(fields=["warehouse", "shipped_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.code:
            with transaction.atomic():
                last = Container.objects.select_for_update().order_by("-id").first()
                next_id = (last.id + 1) if last else 1
                self.code = f"TARA-{next_id:06d}"
        super().save(*args, **kwargs)

    def recalculate(self):
        agg = self.lines.aggregate(s=Sum("quantity"))
        self.quantity = agg["s"] or 0
        first = self.lines.first()
        self.product = first.product if first else None
        self.save(update_fields=["product", "quantity", "updated_at"])

    @property
    def is_empty(self) -> bool:
        return not self.lines.exists()

    @property
    def is_shipped(self) -> bool:
        return self.shipped_at is not None

    def __str__(self) -> str:
        content = self.product.article if self.product_id else "—"
        return f"{self.code} [{self.get_status_display()}] {content} × {self.quantity}"


class ContainerLine(models.Model):
    container = models.ForeignKey(Container, on_delete=models.CASCADE,
        related_name="lines", verbose_name="Тара")
    product = models.ForeignKey(Product, on_delete=models.PROTECT,
        related_name="container_lines", verbose_name="Артикул")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Количество")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Содержимое тары"
        verbose_name_plural = "Содержимое тары"
        unique_together = [("container", "product")]
        ordering = ("-quantity",)

    def __str__(self) -> str:
        return f"{self.container.code}: {self.product.article} × {self.quantity}"


class MovementType(models.TextChoices):
    IN = "in", "Приход"
    OUT = "out", "Расход"
    TRANSFER = "transfer", "Перемещение"
    ADJUST = "adjust", "Корректировка"
    PRODUCE_IN = "produce_in", "Изготовление (приход)"
    PRODUCE_OUT = "produce_out", "Изготовление (расход)"
    SCRAP = "scrap", "Списание в брак"


class Movement(models.Model):
    movement_type = models.CharField(max_length=16, choices=MovementType.choices,
        verbose_name="Тип движения")
    product = models.ForeignKey(Product, on_delete=models.PROTECT,
        related_name="movements", verbose_name="Артикул")
    quantity = models.DecimalField(max_digits=14, decimal_places=3,
        verbose_name="Количество")
    warehouse_from = models.ForeignKey(Warehouse, null=True, blank=True,
        on_delete=models.PROTECT, related_name="movements_from",
        verbose_name="Склад-источник")
    warehouse_to = models.ForeignKey(Warehouse, null=True, blank=True,
        on_delete=models.PROTECT, related_name="movements_to",
        verbose_name="Склад-приёмник")
    container = models.ForeignKey(Container, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="movements", verbose_name="Тара")
    order = models.ForeignKey("orders.Order", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="movements", verbose_name="Заказ")
    production_run = models.ForeignKey("ProductionRun", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="movements",
        verbose_name="Производственная операция (legacy)")
    comment = models.CharField(max_length=255, blank=True, default="",
        verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="movements_created",
        verbose_name="Создал")

    class Meta:
        verbose_name = "Движение"
        verbose_name_plural = "Движения"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["movement_type"]),
                   models.Index(fields=["created_at"])]

    def __str__(self) -> str:
        return f"{self.get_movement_type_display()} {self.product.article} × {self.quantity}"


class ContainerEventType(models.TextChoices):
    CREATED = "created", "Создана"
    ISSUED = "issued", "Выдана в производство"
    RETURNED = "returned", "Возвращена из производства"
    MOVED = "moved", "Перемещена"
    SHIPPED = "shipped", "Отгружена"
    ADJUSTED = "adjusted", "Скорректирована"
    STATUS_CHANGED = "status_changed", "Смена статуса"


class ContainerEvent(models.Model):
    container = models.ForeignKey(Container, on_delete=models.CASCADE,
        related_name="events", verbose_name="Тара")
    event_type = models.CharField(max_length=16, choices=ContainerEventType.choices,
        verbose_name="Событие")
    product = models.ForeignKey(Product, null=True, blank=True,
        on_delete=models.SET_NULL, verbose_name="Артикул на момент события")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Количество")
    order = models.ForeignKey("orders.Order", null=True, blank=True,
        on_delete=models.SET_NULL, verbose_name="Заказ")
    movement = models.ForeignKey(Movement, null=True, blank=True,
        on_delete=models.SET_NULL, verbose_name="Связанное движение")
    comment = models.CharField(max_length=255, blank=True, default="",
        verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, verbose_name="Создал")

    class Meta:
        verbose_name = "Событие тары"
        verbose_name_plural = "События тары"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.container.code}: {self.get_event_type_display()}"


# =========================================================================
# НОВАЯ ЛОГИКА: ОПЕРАЦИИ (вместо ProductionRun)
# =========================================================================

class OperationType(models.TextChoices):
    TURNING  = "turning",  "Токарка"
    MILLING  = "milling",  "Фрезеровка"
    PACKING  = "packing",  "Упаковка"
    MOVE     = "move",     "Перемещение"
    SHIP     = "ship",     "Отгрузка"
    OTHER    = "other",    "Прочее"


class OperationDirection(models.TextChoices):
    FROM  = "from",  "Взято"
    TO    = "to",    "Положено"
    SCRAP = "scrap", "Брак"


class ScrapReason(models.TextChoices):
    SETUP        = "setup",        "Наладка"
    FOUNDRY      = "foundry",      "Литейный брак"
    PRODUCTION   = "production",   "Производственный брак"
    NEGLIGENCE   = "negligence",   "Халатность"


class Operation(models.Model):
    """
    Одна производственная операция (постфактум).
    Внутри — строки OperationLine: взял/положил/брак.
    Баланс: SUM(from.qty) == SUM(to.qty) + SUM(scrap.qty).
    """
    operation_type = models.CharField(
        max_length=16, choices=OperationType.choices,
        default=OperationType.OTHER, verbose_name="Тип операции",
        db_index=True)
    order = models.ForeignKey("orders.Order", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="operations",
        verbose_name="Заказ")
    comment = models.TextField(blank=True, default="", verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="operations_created",
        verbose_name="Создал")

    class Meta:
        verbose_name = "Операция"
        verbose_name_plural = "Операции"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return (f"#{self.pk} {self.get_operation_type_display()} "
                f"({self.created_at:%d.%m.%Y %H:%M})")


class OperationLine(models.Model):
    """
    Строка операции.
      direction='from'  — взято из тары (container обязателен)
      direction='to'    — положено в тару (container обязателен)
      direction='scrap' — брак (container = NULL, scrap_reason обязателен)
    """
    operation = models.ForeignKey(Operation, on_delete=models.CASCADE,
        related_name="lines", verbose_name="Операция")
    direction = models.CharField(max_length=8, choices=OperationDirection.choices,
        verbose_name="Направление", db_index=True)
    container = models.ForeignKey(Container, null=True, blank=True,
        on_delete=models.PROTECT, related_name="operation_lines",
        verbose_name="Тара")
    product = models.ForeignKey(Product, on_delete=models.PROTECT,
        related_name="operation_lines", verbose_name="Артикул")
    qty = models.DecimalField(max_digits=14, decimal_places=3,
        verbose_name="Количество")
    scrap_reason = models.CharField(
        max_length=16, choices=ScrapReason.choices,
        blank=True, default="", verbose_name="Причина брака")

    class Meta:
        verbose_name = "Строка операции"
        verbose_name_plural = "Строки операции"
        ordering = ("id",)
        indexes = [
            models.Index(fields=["direction"]),
            models.Index(fields=["container", "direction"]),
        ]

    def __str__(self) -> str:
        tail = f" [{self.get_scrap_reason_display()}]" if self.scrap_reason else ""
        return f"{self.get_direction_display()}: {self.product.article} × {self.qty}{tail}"


# =========================================================================
# LEGACY: старые модели. Будут удалены на Этапе 5.
# Пока оставлены, чтобы не сломать views/serializers/admin/фронт.
# =========================================================================

class ProductionRunStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "В работе"
    COMPLETED   = "completed",   "Завершено"
    CANCELLED   = "cancelled",   "Отменено"


class ProductionRun(models.Model):
    """
    LEGACY. Заменено на Operation + OperationLine.
    Оставлено для совместимости, будет удалено.
    """

    source_product = models.ForeignKey(Product, on_delete=models.PROTECT,
        related_name="runs_source", verbose_name="Литьё (источник)")
    product = models.ForeignKey(Product, null=True, blank=True,
        on_delete=models.PROTECT, related_name="runs_result",
        verbose_name="Главный результат (кэш первого)")

    qty_source_used = models.DecimalField(max_digits=14, decimal_places=3,
        default=0, verbose_name="Взято заготовок")
    qty_good = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Годных (кэш суммы результатов)")
    qty_scrap = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Брак (кэш суммы результатов)")
    qty_returned = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Возвращено в исходные тары")
    scrap_reason = models.CharField(max_length=255, blank=True, default="",
        verbose_name="Причина брака (legacy)")
    qty_from_reserve = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Взято из задела")
    qty_reserve = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Оставлено в задел")

    status = models.CharField(max_length=16, choices=ProductionRunStatus.choices,
        default=ProductionRunStatus.COMPLETED,
        verbose_name="Статус партии")

    order = models.ForeignKey("orders.Order", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="production_runs",
        verbose_name="Заказ")
    comment = models.TextField(blank=True, default="", verbose_name="Комментарий")
    scrap_written_off = models.BooleanField(default=False,
        verbose_name="Брак списан актом")
    scrap_written_off_at = models.DateTimeField(null=True, blank=True,
        verbose_name="Дата списания брака")

    operator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="production_runs_as_operator",
        verbose_name="Оператор")

    created_at = models.DateTimeField(auto_now_add=True,
        verbose_name="Старт (выдача в работу)")
    finished_at = models.DateTimeField(null=True, blank=True,
        verbose_name="Завершено")

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="production_runs_created",
        verbose_name="Создал")
    finished_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="production_runs_finished",
        verbose_name="Завершил")

    class Meta:
        verbose_name = "Производственная операция (legacy)"
        verbose_name_plural = "Производственные операции (legacy)"
        ordering = ("-created_at",)

    @property
    def qty_unallocated(self):
        return (self.qty_source_used
                - self.qty_good
                - self.qty_scrap
                - self.qty_returned)

    def __str__(self) -> str:
        result = self.product.article if self.product_id else "?"
        return (f"#{self.pk} {self.source_product.article} → "
                f"{result} ({self.get_status_display()}, {self.qty_good} годных)")


class ProductionRunResult(models.Model):
    """LEGACY. Будет удалено."""
    run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE,
        related_name="results", verbose_name="Партия")
    product = models.ForeignKey(Product, on_delete=models.PROTECT,
        related_name="production_results", verbose_name="Деталь")
    qty_good = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Годных")
    qty_scrap = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Брак (кэш суммы причин)")
    comment = models.TextField(blank=True, default="", verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="production_results_created",
        verbose_name="Создал")

    class Meta:
        verbose_name = "Результат партии (legacy)"
        verbose_name_plural = "Результаты партии (legacy)"
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"#{self.run_id} → {self.product.article} × {self.qty_good}"


class ScrapEntry(models.Model):
    """LEGACY. Будет удалено."""
    production_run = models.ForeignKey(ProductionRun, on_delete=models.CASCADE,
        related_name="scrap_entries", verbose_name="Операция")
    production_result = models.ForeignKey(ProductionRunResult, null=True, blank=True,
        on_delete=models.CASCADE, related_name="scrap_entries",
        verbose_name="Результат партии (для новых)")
    reason = models.CharField(max_length=255, verbose_name="Причина брака")
    quantity = models.DecimalField(max_digits=14, decimal_places=3,
        verbose_name="Количество, шт")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Причина брака (legacy)"
        verbose_name_plural = "Причины брака (legacy)"
        ordering = ("-quantity",)

    def __str__(self) -> str:
        return f"{self.reason}: {self.quantity}"


# =========================================================================
# ИНВЕНТАРИЗАЦИЯ / НАКЛАДНЫЕ — без изменений
# =========================================================================

class InventoryStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"
    IN_PROGRESS = "in_progress", "В процессе"
    COMPLETED = "completed", "Завершена"
    CANCELLED = "cancelled", "Отменена"


class Inventory(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT,
        related_name="inventories", verbose_name="Склад")
    status = models.CharField(max_length=16, choices=InventoryStatus.choices,
        default=InventoryStatus.DRAFT, verbose_name="Статус")
    started_at = models.DateTimeField(auto_now_add=True, verbose_name="Начата")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершена")
    comment = models.CharField(max_length=255, blank=True, default="",
        verbose_name="Комментарий")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, verbose_name="Создал")

    class Meta:
        verbose_name = "Инвентаризация"
        verbose_name_plural = "Инвентаризации"
        ordering = ("-started_at",)

    def __str__(self) -> str:
        return f"Инвентаризация #{self.pk} ({self.warehouse.name})"


class InventoryLine(models.Model):
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE,
        related_name="lines", verbose_name="Инвентаризация")
    product = models.ForeignKey(Product, on_delete=models.PROTECT,
        related_name="inventory_lines", verbose_name="Артикул")
    quantity_theory = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="По учёту")
    quantity_fact = models.DecimalField(max_digits=14, decimal_places=3, default=0,
        verbose_name="Факт")
    comment = models.CharField(max_length=255, blank=True, default="",
        verbose_name="Комментарий")

    class Meta:
        verbose_name = "Строка инвентаризации"
        verbose_name_plural = "Строки инвентаризации"

    @property
    def difference(self):
        return (self.quantity_fact or 0) - (self.quantity_theory or 0)

    def __str__(self) -> str:
        return f"{self.product.article}: учёт {self.quantity_theory}, факт {self.quantity_fact}"


class ShipmentNote(models.Model):
    number = models.CharField(max_length=32, unique=True, verbose_name="Номер")
    year = models.PositiveIntegerField(verbose_name="Год")
    seq = models.PositiveIntegerField(verbose_name="Порядковый номер за год")
    note_date = models.DateField(verbose_name="Дата накладной")

    from_name = models.CharField(max_length=255, default="", verbose_name="Отправитель")
    from_address = models.CharField(max_length=500, blank=True, default="",
                                     verbose_name="Адрес отправителя")
    to_name = models.CharField(max_length=255, default="", verbose_name="Получатель")
    to_address = models.CharField(max_length=500, blank=True, default="",
                                   verbose_name="Адрес получателя")

    total_qty = models.DecimalField(max_digits=14, decimal_places=3, default=0,
                                     verbose_name="Всего шт")
    total_weight_kg = models.DecimalField(max_digits=14, decimal_places=3, default=0,
                                           verbose_name="Всего вес, кг")
    comment = models.CharField(max_length=255, blank=True, default="",
                                verbose_name="Комментарий")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="shipment_notes_created",
        verbose_name="Создал")

    class Meta:
        verbose_name = "Накладная на отгрузку"
        verbose_name_plural = "Накладные на отгрузку"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.number} · {self.note_date}"


class ShipmentNoteLine(models.Model):
    note = models.ForeignKey(ShipmentNote, on_delete=models.CASCADE,
        related_name="lines", verbose_name="Накладная")
    container = models.ForeignKey("Container", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="shipment_lines",
        verbose_name="Тара")
    code = models.CharField(max_length=32, verbose_name="Код тары")
    product_article = models.CharField(max_length=64, verbose_name="Артикул")
    product_name = models.CharField(max_length=255, verbose_name="Наименование")
    uom = models.CharField(max_length=16, default="шт", verbose_name="Ед.")
    quantity = models.DecimalField(max_digits=14, decimal_places=3,
                                    verbose_name="Кол-во")
    weight_kg = models.DecimalField(max_digits=14, decimal_places=3, default=0,
                                     verbose_name="Вес, кг")
    sequence = models.PositiveIntegerField(default=0, verbose_name="№")

    class Meta:
        verbose_name = "Строка накладной"
        verbose_name_plural = "Строки накладных"
        ordering = ("sequence", "id")

    def __str__(self) -> str:
        return f"{self.note.number} #{self.sequence}: {self.code} × {self.quantity}"
