from django.conf import settings
from django.db import models

from inventory.models import Product


class OrderKind(models.TextChoices):
    PRODUCTION = "production", "Производственный"
    SHIPMENT = "shipment", "На отгрузку"


class OrderStatus(models.TextChoices):
    NEW = "new", "Новый"
    IN_WORK = "in_work", "В работе"
    READY = "ready", "Готов"
    SHIPPED = "shipped", "Отгружен"
    CLOSED = "closed", "Закрыт"


class Order(models.Model):
    number = models.CharField(max_length=64, unique=True, verbose_name="Номер заказа")
    kind = models.CharField(
        max_length=16,
        choices=OrderKind.choices,
        default=OrderKind.PRODUCTION,
        verbose_name="Тип заказа",
    )
    customer = models.CharField(
        max_length=255, blank=True, default="", verbose_name="Заказчик"
    )
    status = models.CharField(
        max_length=16,
        choices=OrderStatus.choices,
        default=OrderStatus.NEW,
        verbose_name="Статус",
    )
    due_date = models.DateField(null=True, blank=True, verbose_name="Срок")
    comment = models.TextField(blank=True, default="", verbose_name="Комментарий")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders_created",
        verbose_name="Создал",
    )

    class Meta:
        verbose_name = "Заказ"
        verbose_name_plural = "Заказы"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Заказ {self.number}"


class OrderLine(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="lines",
        verbose_name="Заказ",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="order_lines",
        verbose_name="Артикул (деталь после МО)",
    )
    casting = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="order_lines_casting",
        verbose_name="Артикул литья",
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Наименование",
        help_text="Подтягивается из карточки артикула, можно переопределить",
    )
    quantity_planned = models.DecimalField(
        max_digits=14, decimal_places=3, verbose_name="План, шт"
    )
    quantity_done = models.DecimalField(
        max_digits=14, decimal_places=3, default=0, verbose_name="Изготовлено, шт"
    )
    quantity_shipped = models.DecimalField(
        max_digits=14, decimal_places=3, default=0, verbose_name="Отгружено, шт"
    )

    # Поля из «производственного» Excel-журнала
    sequence = models.IntegerField(
        default=0, verbose_name="№ п/п в заказе",
        help_text="Порядковый номер строки внутри заказа")
    machine = models.CharField(
        max_length=32, blank=True, default="",
        verbose_name="№ станка",
        help_text="Например «6--5», «3--4». Свободный текст.")
    ready_date = models.DateField(
        null=True, blank=True,
        verbose_name="Дата готовности",
        help_text="Когда планируется/состоялась готовность")
    shipped_date = models.DateField(
        null=True, blank=True,
        verbose_name="Дата отгрузки",
        help_text="Когда планируется/состоялась отгрузка")
    reserve_qty = models.DecimalField(
        max_digits=14, decimal_places=3, default=0,
        verbose_name="Задел (дельта)",
        help_text="Отклонение от плана: +6 → готовых 106; −1 → готовых 99")
    places = models.IntegerField(
        null=True, blank=True,
        verbose_name="Мест",
        help_text="Количество грузовых мест")
    weight_g = models.DecimalField(
        max_digits=14, decimal_places=3, null=True, blank=True,
        verbose_name="Вес партии, г",
        help_text="Если пусто — считается из веса единицы × количество")

    comment = models.CharField(max_length=255, blank=True, default="", verbose_name="Комментарий")

    class Meta:
        verbose_name = "Строка заказа"
        verbose_name_plural = "Строки заказа"

    def save(self, *args, **kwargs):
        if not self.name and self.product_id:
            self.name = self.product.name
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.product.article} × {self.quantity_planned}"


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="status_history",
        verbose_name="Заказ",
    )
    status_from = models.CharField(max_length=16, choices=OrderStatus.choices, verbose_name="Из")
    status_to = models.CharField(max_length=16, choices=OrderStatus.choices, verbose_name="В")
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name="Когда")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="Кто",
    )
    comment = models.CharField(max_length=255, blank=True, default="", verbose_name="Комментарий")

    class Meta:
        verbose_name = "История статуса"
        verbose_name_plural = "История статусов"
        ordering = ("-changed_at",)

    def __str__(self) -> str:
        return f"{self.order.number}: {self.status_from} → {self.status_to}"
