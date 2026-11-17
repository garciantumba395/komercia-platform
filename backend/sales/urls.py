from django.urls import path

from rest_framework.renderers import JSONRenderer

from .views import CashSessionViewSet, ReceiptHTMLRenderer, ReceiptTextRenderer, SaleViewSet

sales = SaleViewSet.as_view({"get": "list", "post": "create"})
sale_detail = SaleViewSet.as_view({"get": "retrieve"})
sale_cancel = SaleViewSet.as_view({"post": "cancel"})
# `?format=html|text` : rendu direct (pas de négociation JSON) ; HTML par défaut.
sale_receipt = SaleViewSet.as_view(
    {"get": "receipt"}, renderer_classes=[ReceiptHTMLRenderer, ReceiptTextRenderer, JSONRenderer]
)

sessions = CashSessionViewSet.as_view({"get": "list"})
session_open = CashSessionViewSet.as_view({"post": "open"})
session_current = CashSessionViewSet.as_view({"get": "current"})
session_close = CashSessionViewSet.as_view({"post": "close"})

urlpatterns = [
    path("cash-sessions/", sessions, name="cash-session-list"),
    path("cash-sessions/open/", session_open, name="cash-session-open"),
    path("cash-sessions/current/", session_current, name="cash-session-current"),
    path("cash-sessions/<uuid:pk>/close/", session_close, name="cash-session-close"),
    path("", sales, name="sale-list"),
    path("<uuid:pk>/", sale_detail, name="sale-detail"),
    path("<uuid:pk>/cancel/", sale_cancel, name="sale-cancel"),
    path("<uuid:pk>/receipt/", sale_receipt, name="sale-receipt"),
]
