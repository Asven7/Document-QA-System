"""URL routes for the documents app (mounted at /api/v1/documents/)."""

from rest_framework.routers import DefaultRouter

from documents.views import DocumentViewSet

router = DefaultRouter()
router.register(r"", DocumentViewSet, basename="document")

urlpatterns = router.urls
