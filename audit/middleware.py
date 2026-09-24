import threading

_thread_locals = threading.local()


def get_current_user():
    """Кто сейчас делает запрос. None — если вне HTTP-запроса."""
    return getattr(_thread_locals, "user", None)


def set_current_user(user):
    """Установить юзера в thread-local.
    Вызывается из AuditJWTAuthentication (DRF) или middleware (Django admin).
    """
    _thread_locals.user = user


class CurrentUserMiddleware:
    """Прокидывает юзера в thread-local для Django-views и admin.
    Для DRF см. accounts.authentication.AuditJWTAuthentication.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = None
        try:
            u = getattr(request, "user", None)
            if u is not None and getattr(u, "is_authenticated", False):
                user = u
        except Exception:
            user = None
        _thread_locals.user = user
        try:
            return self.get_response(request)
        finally:
            _thread_locals.user = None
