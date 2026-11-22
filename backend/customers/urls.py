from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CustomerViewSet, DebtListView, OverdueDebtListView

router = DefaultRouter()
router.register("customers", CustomerViewSet, basename="customer")

urlpatterns = [
    path("debts/overdue/", OverdueDebtListView.as_view()),
    path("debts/", DebtListView.as_view()),
    path("", include(router.urls)),
]
