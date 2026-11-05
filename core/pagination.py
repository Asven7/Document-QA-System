"""Shared pagination class for all list endpoints."""

from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    """Page-number pagination with a configurable, capped page size."""

    page_size_query_param = "page_size"
    max_page_size = 100
