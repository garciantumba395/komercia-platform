"""URLs de test : une vue protégée par SubscriptionWriteAllowed, montée sur des chemins variés."""

from django.urls import path
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.permissions import SubscriptionWriteAllowed


class Probe(APIView):
    permission_classes = [IsAuthenticated, SubscriptionWriteAllowed]

    def get(self, request):
        return Response({"ok": True})

    def post(self, request):
        return Response({"ok": True})


urlpatterns = [
    path("api/v1/probe/", Probe.as_view()),
    path("api/v1/auth/probe/", Probe.as_view()),
    path("api/v1/billing/probe/", Probe.as_view()),
    path("api/v1/sync/probe/", Probe.as_view()),
    path("api/v1/reports/export/", Probe.as_view()),
]
