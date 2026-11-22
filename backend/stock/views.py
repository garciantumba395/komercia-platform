import datetime

from django.db.models import F
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from audit import services as audit
from catalog.permissions import ALL_ROLES
from core.permissions import MANAGER, OWNER, STOREKEEPER, HasRole

from .models import StockLevel, StockMovement
from .serializers import MovementCreateSerializer, StockLevelSerializer, StockMovementSerializer
from .services import InsufficientStock, apply_movement, get_quantity

ADJUST_ROLES = [OWNER, MANAGER, STOREKEEPER]


def _filter_by_ids(qs, params, names):
    for name in names:
        if params.get(name):
            try:
                qs = qs.filter(**{f"{name}_id": params[name]})
            except (ValueError, TypeError):
                raise ValidationError({name: "Identifiant invalide."}) from None
    return qs


class StockLevelViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StockLevelSerializer
    permission_classes = [HasRole]
    allowed_roles = ALL_ROLES

    def get_queryset(self):
        qs = StockLevel.objects.select_related("product", "store").order_by("product__name", "id")
        params = self.request.query_params
        qs = _filter_by_ids(qs, params, ["store", "product"])
        if params.get("low", "").lower() in ("1", "true", "yes", "oui"):
            qs = qs.filter(product__track_stock=True, quantity__lte=F("product__min_stock"))
        return qs


class LowStockAlertViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Produits au seuil minimum ou sous le seuil (alerte KOM-043)."""

    serializer_class = StockLevelSerializer
    permission_classes = [HasRole]
    allowed_roles = ALL_ROLES

    def get_queryset(self):
        qs = StockLevel.objects.select_related("product", "store").filter(
            product__track_stock=True, quantity__lte=F("product__min_stock")
        )
        return _filter_by_ids(qs, self.request.query_params, ["store"]).order_by("quantity", "id")


class StockMovementViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    """Append-only : ni modification ni suppression."""

    permission_classes = [HasRole]
    allowed_roles_by_action = {
        "list": ALL_ROLES,
        "retrieve": ALL_ROLES,
        "create": ADJUST_ROLES,
    }

    def get_serializer_class(self):
        return MovementCreateSerializer if self.action == "create" else StockMovementSerializer

    def get_queryset(self):
        qs = StockMovement.objects.select_related("product").order_by("-created_at", "-id")
        params = self.request.query_params
        qs = _filter_by_ids(qs, params, ["store", "product"])
        if params.get("kind"):
            qs = qs.filter(kind=params["kind"])
        for key, lookup in (("date_from", "created_at__date__gte"), ("date_to", "created_at__date__lte")):
            if params.get(key):
                try:
                    qs = qs.filter(**{lookup: datetime.date.fromisoformat(params[key])})
                except ValueError:
                    raise ValidationError({key: "Date invalide (AAAA-MM-JJ)."}) from None
        return qs

    def create(self, request, *args, **kwargs):
        serializer = MovementCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        quantity_before = get_quantity(d["product"], d["store"])
        try:
            movement = apply_movement(
                d["product"], d["store"], d["delta"], d["kind"],
                reason=d["reason"].strip(), reference=d["reference"],
                unit_cost_minor=d["unit_cost_minor"], user=request.user,
                allow_negative=(d["kind"] == "in"),
            )
        except InsufficientStock as exc:
            raise ValidationError({"quantity": str(exc)}) from None
        if movement is not None:
            codes = ["stock.movement"] + (["stock.adjust"] if movement.kind == "adjustment" else [])
            for code in codes:
                audit.log(code, "stockmovement", movement.id, request=request,
                          before={"quantity": quantity_before},
                          after={"kind": movement.kind, "product_id": movement.product_id,
                                 "store_id": movement.store_id, "delta": movement.quantity,
                                 "quantity": quantity_before + movement.quantity,
                                 "reference": movement.reference},
                          reason=movement.reason)
        return Response(StockMovementSerializer(movement, context={"request": request}).data, status=201)
