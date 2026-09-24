"""
Кастомная JWT-аутентификация.

Задача: после успешной проверки токена записать юзера в thread-local,
чтобы аудит-signals могли записать «кто сделал».

Почему не Django middleware: DRF-аутентификация срабатывает позже
Django-middleware, поэтому в момент работы middleware request.user
ещё AnonymousUser.
"""
from rest_framework_simplejwt.authentication import JWTAuthentication

from audit.middleware import set_current_user


class AuditJWTAuthentication(JWTAuthentication):
    """JWT + запись юзера в thread-local для аудита."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            user, _token = result
            set_current_user(user)
        return result
