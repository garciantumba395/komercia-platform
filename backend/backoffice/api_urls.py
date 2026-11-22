"""Routes de l'API de consentement de support. À monter sous `api/v1/support/` (voir `api.py`)."""

from django.urls import path

from .api import SupportGrantView, SupportRevokeView, SupportStateView

urlpatterns = [
    path("", SupportStateView.as_view()),
    path("grant/", SupportGrantView.as_view()),
    path("revoke/", SupportRevokeView.as_view()),
]
