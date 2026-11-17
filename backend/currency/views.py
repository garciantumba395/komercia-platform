import uuid

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from core.permissions import ACCOUNTANT, CASHIER, MANAGER, OWNER, STOREKEEPER, HasRole

from . import services
from .models import ExchangeRate
from .serializers import ExchangeRateSerializer

ALL_ROLES = [OWNER, MANAGER, CASHIER, STOREKEEPER, ACCOUNTANT]
STORE_PARAM = OpenApiParameter(
    "store", str, description="UUID de la boutique, ou « default » pour le taux par défaut de l'entreprise."
)


def _parse_store(value):
    """None si absent ; "default" -> "default" ; sinon UUID validé."""
    if value in (None, ""):
        return None
    if value == "default":
        return "default"
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ValidationError({"store": "Identifiant de boutique invalide."}) from None


class ExchangeRateViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    """Taux de change CDF/USD : historique en ajout seul (pas de modification ni suppression)."""

    serializer_class = ExchangeRateSerializer
    permission_classes = [HasRole]
    allowed_roles_by_action = {
        "list": ALL_ROLES,
        "retrieve": ALL_ROLES,
        "current": ALL_ROLES,
        "create": [OWNER, MANAGER],
    }

    def get_queryset(self):
        return ExchangeRate.objects.order_by("-effective_at", "-created_at")  # filtré par la RLS

    @extend_schema(parameters=[STORE_PARAM])
    def list(self, request, *args, **kwargs):
        store = _parse_store(request.query_params.get("store"))
        if store == "default":
            self.queryset_filter = {"store__isnull": True}
        elif store is not None:
            self.queryset_filter = {"store_id": store}
        else:
            self.queryset_filter = {}
        return super().list(request, *args, **kwargs)

    def filter_queryset(self, queryset):
        return queryset.filter(**getattr(self, "queryset_filter", {}))

    def perform_create(self, serializer):
        serializer.save(set_by=self.request.user)

    @extend_schema(parameters=[STORE_PARAM], responses=ExchangeRateSerializer)
    @action(detail=False, methods=["get"], url_path="current")
    def current(self, request):
        store = _parse_store(request.query_params.get("store"))
        store_id = None if store == "default" else store
        rate = services.current_rate(store_id)
        if rate is None:
            raise NotFound("Aucun taux de change défini.")
        data = ExchangeRateSerializer(rate).data
        data["is_store_rate"] = rate.store_id is not None
        data["as_of"] = timezone.now()
        return Response(data)
