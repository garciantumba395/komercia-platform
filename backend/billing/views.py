import hashlib
import hmac

from django.conf import settings
from django.http import Http404, HttpResponse
from django.utils import timezone
from rest_framework.generics import ListAPIView
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import ACCOUNTANT, MANAGER, OWNER, HasRole
from currency.services import current_rate

from . import receipts, services
from .models import BillingNotice, Plan, SubscriptionPayment
from .providers import get_provider, provider_code
from .serializers import (
    ChangePlanSerializer,
    NoticeSerializer,
    PaymentBriefSerializer,
    PaymentSerializer,
    PlanSerializer,
    ProviderResultSerializer,
    SubscribeSerializer,
)

READ_ROLES = [OWNER, MANAGER, ACCOUNTANT]
WRITE_ROLES = [OWNER]
SIGNATURE_HEADER = "HTTP_X_KOMERSIA_SIGNATURE"


def _subscription(request):
    sub, _ = services.ensure_subscription(request.tenant.id)
    return sub


class PlanListView(APIView):
    permission_classes = [HasRole]
    allowed_roles = READ_ROLES

    def get(self, request):
        plans = Plan.objects.filter(is_active=True)
        return Response(PlanSerializer(plans, many=True, context={"rate": current_rate()}).data)


class SubscriptionView(APIView):
    permission_classes = [HasRole]
    allowed_roles = READ_ROLES

    def get(self, request):
        return Response(services.summary(_subscription(request)))


class SubscribeView(APIView):
    permission_classes = [HasRole]
    allowed_roles = WRITE_ROLES

    def post(self, request):
        data = SubscribeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        sub = _subscription(request)
        payment, instructions = services.subscribe(
            sub, plan_code=data.validated_data["plan"], currency=data.validated_data["currency"],
            phone=data.validated_data["phone"])
        return Response({"payment": PaymentBriefSerializer(payment).data,
                         "instructions": instructions}, status=201)


class PaymentListView(ListAPIView):
    permission_classes = [HasRole]
    allowed_roles = READ_ROLES
    serializer_class = PaymentSerializer

    def get_queryset(self):
        return SubscriptionPayment.objects.select_related("plan").order_by("-created_at", "-id")


class _AnyFormat(DefaultContentNegotiation):
    """`?format=` sert ici au reçu (html|text) : ne pas le laisser piloter le rendu DRF."""

    def select_renderer(self, request, renderers, format_suffix=None):
        return renderers[0], renderers[0].media_type


class PaymentReceiptView(APIView):
    permission_classes = [HasRole]
    allowed_roles = READ_ROLES
    content_negotiation_class = _AnyFormat
    renderer_classes = [JSONRenderer]

    def get(self, request, pk):
        payment = SubscriptionPayment.objects.select_related("plan").filter(pk=pk).first()
        if payment is None:
            raise Http404
        if payment.status != SubscriptionPayment.CONFIRMED:
            raise services.BillingError("Aucun reçu : ce paiement n'est pas confirmé.", "not_confirmed")
        fmt = request.query_params.get("format", "html")
        if fmt not in ("html", "text"):
            raise services.BillingError("Format invalide (html ou text).", "invalid_format")
        name = request.tenant.name
        if fmt == "text":
            return HttpResponse(receipts.render_text(payment, name),
                                content_type="text/plain; charset=utf-8")
        return HttpResponse(receipts.render_html(payment, name), content_type="text/html; charset=utf-8")


class ChangePlanView(APIView):
    permission_classes = [HasRole]
    allowed_roles = WRITE_ROLES

    def post(self, request):
        data = ChangePlanSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        sub = _subscription(request)
        kind, payment, instructions = services.change_plan(
            sub, plan_code=data.validated_data["plan"], currency=data.validated_data["currency"],
            phone=data.validated_data["phone"], user=request.user)
        sub.refresh_from_db()
        if kind == "upgrade":
            return Response({"kind": "upgrade", "payment": PaymentBriefSerializer(payment).data,
                             "instructions": instructions}, status=201)
        pending = sub.pending_plan.code if sub.pending_plan_id else None
        return Response({"kind": kind, "pending_plan": pending,
                         "effective_at": sub.current_period_end if pending else None})


class CancelView(APIView):
    permission_classes = [HasRole]
    allowed_roles = WRITE_ROLES

    def post(self, request):
        sub = services.cancel(_subscription(request), user=request.user)
        return Response(services.summary(sub))


class ResumeView(APIView):
    permission_classes = [HasRole]
    allowed_roles = WRITE_ROLES

    def post(self, request):
        sub = services.resume(_subscription(request), user=request.user)
        return Response(services.summary(sub))


class NoticeListView(ListAPIView):
    permission_classes = [HasRole]
    allowed_roles = READ_ROLES
    serializer_class = NoticeSerializer

    def get_queryset(self):
        qs = BillingNotice.objects.all()
        if self.request.query_params.get("unread") in ("1", "true"):
            qs = qs.filter(read_at__isnull=True)
        return qs.order_by("-created_at", "-id")


class NoticeReadView(APIView):
    permission_classes = [HasRole]
    allowed_roles = WRITE_ROLES

    def post(self, request, pk):
        notice = BillingNotice.objects.filter(pk=pk).first()
        if notice is None:
            raise Http404
        if notice.read_at is None:
            notice.read_at = timezone.now()
            notice.save(update_fields=["read_at", "updated_at"])
        return Response(NoticeSerializer(notice).data)


# --- Fournisseur : appels sans contexte de tenant (le tenant est retrouvé par la référence) ---


class _ProviderView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes: list = []

    @staticmethod
    def _result(payment):
        return Response({"reference": payment.reference, "status": payment.status})


class SandboxConfirmView(_ProviderView):
    """Simule la réponse de l'opérateur. Indisponible (404) hors BILLING_PROVIDER == "sandbox"."""

    def post(self, request):
        if provider_code() != "sandbox":
            raise Http404
        data = ProviderResultSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        payment = services.apply_provider_result(**data.validated_data)
        if payment is None:
            raise Http404
        return self._result(payment)


def signature_is_valid(request) -> bool:
    secret = getattr(settings, "BILLING_WEBHOOK_SECRET", None)
    given = request.META.get(SIGNATURE_HEADER, "")
    if not secret or not given:
        return False
    given = given.removeprefix("sha256=")
    expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


class WebhookView(_ProviderView):
    """Webhook générique : signature HMAC-SHA256 du corps brut (en-tête X-Komersia-Signature)."""

    def post(self, request, provider):
        if provider != provider_code():
            raise Http404
        if not signature_is_valid(request):
            return Response({"detail": "Signature invalide.", "code": "invalid_signature"}, status=403)
        try:
            reference, status = get_provider(provider).parse_webhook(request)
        except ValueError as exc:
            return Response({"detail": str(exc), "code": "invalid_payload"}, status=400)
        payment = services.apply_provider_result(reference, status)
        if payment is None:
            raise Http404
        return self._result(payment)
