from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import LowStockAlertViewSet, StockLevelViewSet, StockMovementViewSet

router = DefaultRouter()
router.register("levels", StockLevelViewSet, basename="stocklevel")
router.register("movements", StockMovementViewSet, basename="stockmovement")
router.register("alerts/low-stock", LowStockAlertViewSet, basename="lowstock")

urlpatterns = [path("", include(router.urls))]
