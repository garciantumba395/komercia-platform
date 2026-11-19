import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Membership, Store, User
from billing import services
from billing.models import Subscription
from core.tenancy import tenant_context
from currency.models import ExchangeRate

NOW = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
SECRET = "test-webhook-secret"


def get_sub(handle) -> Subscription:
    with tenant_context(handle.tenant.id):
        return Subscription.objects.get()


def update_sub(handle, **fields) -> Subscription:
    with tenant_context(handle.tenant.id):
        sub = Subscription.objects.get()
        for key, value in fields.items():
            setattr(sub, key, value)
        sub.save()
        return sub


def set_rate(handle, rate="2800"):
    from django.utils import timezone

    with tenant_context(handle.tenant.id):
        return ExchangeRate.objects.create(
            rate_cdf_per_usd=Decimal(rate), effective_at=timezone.now() - timedelta(minutes=1),
            set_by=handle.user)


def role_client(handle, role, phone) -> APIClient:
    user = User.objects.create_user(phone=phone, password="MotDePasse-123!", full_name=role)
    Membership.objects.create(user=user, tenant=handle.tenant, role=role)
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


def make_payment(handle, plan="pro", currency="USD", now=NOW, phone="+243990000001"):
    """Crée un paiement pending (service) et retourne le paiement."""
    with tenant_context(handle.tenant.id):
        sub = Subscription.objects.get()
        payment, _ = services.subscribe(sub, plan_code=plan, currency=currency, phone=phone, now=now)
        return payment


def confirm(handle, payment, now=NOW):
    with tenant_context(handle.tenant.id):
        return services.confirm_payment(payment.reference, now)


def activate(handle, plan="pro", now=NOW):
    """Souscription payée et confirmée à `now`."""
    payment = make_payment(handle, plan, now=now)
    confirm(handle, payment, now)
    return get_sub(handle)


def sign(body: bytes, secret=SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_webhook(reference, status, signature="auto", provider="sandbox"):
    body = json.dumps({"reference": reference, "status": status}).encode()
    extra = {}
    if signature == "auto":
        extra["HTTP_X_KOMERSIA_SIGNATURE"] = sign(body)
    elif signature is not None:
        extra["HTTP_X_KOMERSIA_SIGNATURE"] = signature
    return APIClient().post(f"/api/v1/billing/webhooks/{provider}/", data=body,
                            content_type="application/json", **extra)


def add_stores(handle, n):
    with tenant_context(handle.tenant.id):
        for i in range(n):
            Store.objects.create(name=f"Extra {i}")
