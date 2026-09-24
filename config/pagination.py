"""Кастомная пагинация DRF.

По умолчанию — 100 записей.
Фронт может запросить больше через `?page_size=500`.
Максимум — 5000 (защита от случайной выгрузки всей БД).
"""
from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 5000
