"""URL routes for the questions app (mounted at /api/v1/questions/)."""

from rest_framework.routers import DefaultRouter

from questions.views import QuestionViewSet

router = DefaultRouter()
router.register(r"", QuestionViewSet, basename="question")

urlpatterns = router.urls
