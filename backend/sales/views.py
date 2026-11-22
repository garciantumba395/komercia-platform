import json
import uuid

from django.utils.dateparse import parse_date
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response

from core.permissions import ACCOUNTANT, CASHIER, MANAGER, OWNER, HasRole

from . import receipt, services
from .models import CashSession, Sale
from .serializers import (
    CancelInputSerializer,
    CashSessionSerializer,
    CloseSessionInputSerializer,
    OpenSessionInputSerializer,
    SaleInputSerializer,
    SaleSerializer,
)

READ_ROLES = [OWNER, MANAGER, CASHIER, ACCOUNTANT]
WRITE_ROLES = [OWNER, MANAGER, CASHIER]
ADMIN_ROLES = [OWNER, MANAGER]


def _error(exc: services.SaleError) -> Response:
    return Response({"detail": exc.message}, status=exc.status)


def _uuid_param(request, name):
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValidationError({name: "Identifiant invalide."}) from None


class _PassthroughRenderer(BaseRenderer):
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if isinstance(data, str):
            return data.encode("utf-8")
        return json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")


class ReceiptHTMLRenderer(_PassthroughRenderer):
    media_type, format = "text/html", "html"


class ReceiptTextRenderer(_PassthroughRenderer):
    media_type, format = "text/plain", "text"


class SaleViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = SaleSerializer
    permission_classes = [HasRole]
    allowed_roles_by_action = {
        "list": READ_ROLES, "retrieve": READ_ROLES, "receipt": READ_ROLES,
        "create": WRITE_ROLES, "cancel": ADMIN_ROLES,
    }

    def get_queryset(self):  # isolation des entreprises assurée par la RLS
        qs = Sale.objects.select_related("store", "customer", "cashier").prefetch_related(
            "items", "payments")
        request = self.request
        if request.membership.role == CASHIER:  # un caissier ne voit que SES ventes
            qs = qs.filter(cashier=request.user)
        if self.action != "list":
            return qs
        params = request.query_params
        if (store := _uuid_param(request, "store")) is not None:
            qs = qs.filter(store_id=store)
        if (cashier := _uuid_param(request, "cashier")) is not None:
            qs = qs.filter(cashier_id=cashier)
        if (customer := _uuid_param(request, "customer")) is not None:
            qs = qs.filter(customer_id=customer)
        if params.get("status") in (Sale.COMPLETED, Sale.CANCELLED):
            qs = qs.filter(status=params["status"])
        elif params.get("status"):
            raise ValidationError({"status": "Statut inconnu (completed ou cancelled)."})
        for name, lookup in (("date_from", "created_at__date__gte"), ("date_to", "created_at__date__lte")):
            if params.get(name):
                value = parse_date(params[name])
                if value is None:
                    raise ValidationError({name: "Date invalide (AAAA-MM-JJ)."})
                qs = qs.filter(**{lookup: value})
        if params.get("q"):
            qs = qs.filter(number__icontains=params["q"].strip())
        return qs

    @extend_schema(request=SaleInputSerializer, responses={201: SaleSerializer, 200: SaleSerializer})
    def create(self, request, *args, **kwargs):
        serializer = SaleInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            sale, created = services.create_sale(
                dict(serializer.validated_data), user=request.user, role=request.membership.role)
        except services.SaleError as exc:
            return _error(exc)
        sale = self.get_queryset().get(pk=sale.pk)
        return Response(SaleSerializer(sale).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @extend_schema(request=CancelInputSerializer, responses=SaleSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        serializer = CancelInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = self.get_object()
        try:
            services.cancel_sale(sale.pk, user=request.user, reason=serializer.validated_data["reason"])
        except services.SaleError as exc:
            return _error(exc)
        return Response(SaleSerializer(self.get_queryset().get(pk=sale.pk)).data)

    @extend_schema(responses={200: str})
    @action(detail=True, methods=["get"])  # renderers fixés dans urls.py (as_view)
    def receipt(self, request, pk=None):
        sale = self.get_object()
        ctx = receipt.receipt_context(sale, request.tenant.name)
        if request.accepted_renderer.format == "text":
            return Response(receipt.render_text(ctx))
        return Response(receipt.render_html(ctx))


class CashSessionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = CashSessionSerializer
    permission_classes = [HasRole]
    allowed_roles_by_action = {
        "list": READ_ROLES[:3], "open": WRITE_ROLES, "current": WRITE_ROLES, "close": WRITE_ROLES,
    }

    def get_queryset(self):
        qs = CashSession.objects.select_related("store", "cashier")
        if self.request.membership.role == CASHIER:
            qs = qs.filter(cashier=self.request.user)
        if self.action == "list":
            if (store := _uuid_param(self.request, "store")) is not None:
                qs = qs.filter(store_id=store)
            if self.request.query_params.get("status") in (CashSession.OPEN, CashSession.CLOSED):
                qs = qs.filter(status=self.request.query_params["status"])
        return qs

    @extend_schema(request=OpenSessionInputSerializer, responses={201: CashSessionSerializer})
    @action(detail=False, methods=["post"])
    def open(self, request):
        serializer = OpenSessionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            session = services.open_session(
                store_id=serializer.validated_data["store"], user=request.user,
                opening_float=serializer.validated_data["opening_float"])
        except services.SaleError as exc:
            return _error(exc)
        return Response(self.get_serializer(session).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses=CashSessionSerializer)
    @action(detail=False, methods=["get"])
    def current(self, request):
        store = _uuid_param(request, "store")
        qs = CashSession.objects.filter(cashier=request.user, status=CashSession.OPEN)
        if store is not None:
            qs = qs.filter(store_id=store)
        session = qs.first()
        if session is None:
            return Response({"detail": "Aucune session de caisse ouverte."}, status=status.HTTP_404_NOT_FOUND)
        return Response(self.get_serializer(session).data)

    @extend_schema(request=CloseSessionInputSerializer, responses=CashSessionSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        serializer = CloseSessionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        only = request.user if request.membership.role == CASHIER else None
        try:
            session = services.close_session(
                pk, counted=serializer.validated_data["counted"], user=request.user, only_cashier=only)
        except services.SaleError as exc:
            return _error(exc)
        return Response(self.get_serializer(session).data)
