from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ExchangeRateViewSet

router = DefaultRouter()
router.register("rates", ExchangeRateViewSet, basename="exchange-rate")

urlpatterns = [path("", include(router.urls))]
