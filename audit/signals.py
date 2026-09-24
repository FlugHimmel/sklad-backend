from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .middleware import get_current_user

# (app_label, ModelName)
TRACKED = [
    # Номенклатура
    ("inventory", "Product"),
    ("inventory", "Category"),
    ("inventory", "BOM"),
    ("inventory", "BOMLine"),

    # Заказы
    ("orders", "Order"),
    ("orders", "OrderLine"),

    # Склад и тары
    ("warehouse", "Warehouse"),
    ("warehouse", "Stock"),
    ("warehouse", "Container"),
    ("warehouse", "ContainerLine"),
    ("warehouse", "Movement"),

    # Новая логика — операции
    ("warehouse", "Operation"),
    ("warehouse", "OperationLine"),

    # Накладные
    ("warehouse", "ShipmentNote"),
    ("warehouse", "ShipmentNoteLine"),

    # Инвентаризация
    ("warehouse", "Inventory"),
    ("warehouse", "InventoryLine"),

    # Пользователи и настройки
    ("accounts", "User"),
    ("company_settings", "CompanySettings"),
]

SKIP_FIELDS = {
    "created_at", "updated_at", "last_login", "password",
}

KEEP_FIELDS = set()


def _serialize_value(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "pk"):
        return v.pk
    return str(v)


def _snapshot(instance):
    out = {}
    for f in instance._meta.fields:
        name = f.name
        if name in SKIP_FIELDS and name not in KEEP_FIELDS:
            continue
        try:
            v = getattr(instance, name)
        except Exception:
            continue
        out[name] = _serialize_value(v)
    return out


def _user_repr(user):
    if not user:
        return ""
    try:
        full = user.get_full_name().strip()
    except Exception:
        full = ""
    return full or getattr(user, "username", "") or ""


_pre_state = {}


def _key(model, pk):
    return f"{model._meta.label}:{pk}"


def _register(model):
    @receiver(pre_save, sender=model, weak=False,
              dispatch_uid=f"audit_pre_{model._meta.label}")
    def _pre(sender, instance, **kwargs):
        if not instance.pk:
            return
        try:
            old = sender.objects.get(pk=instance.pk)
        except sender.DoesNotExist:
            return
        try:
            _pre_state[_key(sender, instance.pk)] = _snapshot(old)
        except Exception:
            pass

    @receiver(post_save, sender=model, weak=False,
              dispatch_uid=f"audit_post_{model._meta.label}")
    def _post(sender, instance, created, **kwargs):
        from .models import AuditAction, AuditLog
        user = get_current_user()
        try:
            new_snap = _snapshot(instance)
        except Exception:
            return
        try:
            object_repr = str(instance)[:255]
        except Exception:
            object_repr = ""
        if created:
            AuditLog.objects.create(
                user=user, user_repr=_user_repr(user),
                action=AuditAction.CREATE,
                model_name=sender.__name__,
                object_id=instance.pk or 0,
                object_repr=object_repr,
                changes={},
                snapshot=new_snap,
            )
            return

        old = _pre_state.pop(_key(sender, instance.pk), None)
        if old is None:
            return
        changes = {}
        for k, new_v in new_snap.items():
            old_v = old.get(k)
            if old_v != new_v:
                changes[k] = {"from": old_v, "to": new_v}
        if changes:
            AuditLog.objects.create(
                user=user, user_repr=_user_repr(user),
                action=AuditAction.UPDATE,
                model_name=sender.__name__,
                object_id=instance.pk or 0,
                object_repr=object_repr,
                changes=changes,
                snapshot={},
            )

    @receiver(post_delete, sender=model, weak=False,
              dispatch_uid=f"audit_del_{model._meta.label}")
    def _del(sender, instance, **kwargs):
        from .models import AuditAction, AuditLog
        user = get_current_user()
        try:
            snap = _snapshot(instance)
            object_repr = str(instance)[:255]
        except Exception:
            snap, object_repr = {}, ""
        AuditLog.objects.create(
            user=user, user_repr=_user_repr(user),
            action=AuditAction.DELETE,
            model_name=sender.__name__,
            object_id=instance.pk or 0,
            object_repr=object_repr,
            changes={},
            snapshot=snap,
        )


def register_all():
    from django.apps import apps as django_apps
    for app_label, model_name in TRACKED:
        try:
            model = django_apps.get_model(app_label, model_name)
        except LookupError:
            continue
        _register(model)
