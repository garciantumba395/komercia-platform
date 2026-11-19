from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import LoginView, MemberViewSet, MeView, RegisterView, StoreViewSet

router = DefaultRouter()
router.register("stores", StoreViewSet, basename="store")
router.register("users", MemberViewSet, basename="member")

urlpatterns = [
    path("auth/register/", RegisterView.as_view()),
    path("auth/login/", LoginView.as_view()),
    path("auth/me/", MeView.as_view()),
    path("", include(router.urls)),
]
