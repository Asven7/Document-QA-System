"""Root URL configuration for the Document QA System.

URL layout
----------
* ``/admin/``                 - Django Admin (management interface)
* ``/api/health/``            - public health check
* ``/api/auth/token/``        - obtain a DRF API token
* ``/api/schema/``            - OpenAPI 3 schema (public)
* ``/api/docs/``              - Swagger UI (public)
* ``/api/v1/documents/``      - document management endpoints
* ``/api/v1/questions/``      - question and answer history endpoints
"""

from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny

from core.views import HealthView, TokenObtainView

admin.site.site_header = "Document QA System Administration"
admin.site.site_title = "Document QA System"
admin.site.index_title = "Management dashboard"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", HealthView.as_view(), name="health"),
    path("api/auth/token/", TokenObtainView.as_view(), name="obtain-token"),
    path(
        "api/schema/",
        SpectacularAPIView.as_view(permission_classes=[AllowAny]),
        name="schema",
    ),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/v1/documents/", include("documents.urls")),
    path("api/v1/questions/", include("questions.urls")),
]
