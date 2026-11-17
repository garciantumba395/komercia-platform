import uuid
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Membership, Store, User
from catalog.tests.helpers import make_product  # noqa: F401
from core.tenancy import tenant_context
from currency.models import ExchangeRate
from customers.models import Customer

BASE = "/api/v1/sales/"
SESSIONS = BASE + "cash-sessions/"


def make_rate(handle, rate="2800"):
    with tenant_context(handle.tenant.id):
        return ExchangeRate.objects.create(
            rate_cdf_per_usd=Decimal(rate), effective_at=timezone.now() - timedelta(minutes=1),
            set_by=handle.user)


def make_customer(handle, name="Client", limit=1_000_000, ccy="CDF"):
    with tenant_context(handle.tenant.id):
        return Customer.objects.create(name=name, credit_limit_minor=limit, credit_limit_currency=ccy)


def make_store(handle, name="Boutique 2"):
    with tenant_context(handle.tenant.id):
        return Store.objects.create(name=name)


def sale_body(handle, items, payments=None, **extra):
    """items : liste de (produit, quantité[, dict d'options de ligne])."""
    lines = []
    for entry in items:
        product, qty, *opts = entry
        line = {"product": str(product.id), "quantity": str(qty)}
        if opts:
            line.update(opts[0])
        lines.append(line)
    body = {"client_id": str(uuid.uuid4()), "store": str(handle.store.id), "items": lines,
            "payments": payments if payments is not None else []}
    body.update(extra)
    return body


def cash(amount, currency="CDF"):
    return {"method": "cash", "amount_minor": amount, "currency": currency}


def client_for_role(handle, role, tag=""):
    """Client authentifié d'un utilisateur de ce rôle ; le même (rôle, tag) renvoie le même utilisateur."""
    if role == "owner":
        return handle.client()
    cache = handle.__dict__.setdefault("_role_clients", {})
    key = (role, tag)
    if key not in cache:
        phone = f"+2439{uuid.uuid4().int % 10**9:09d}"
        user = User.objects.create_user(phone=phone, password="MotDePasse-123!", full_name=role.title())
        Membership.objects.create(user=user, tenant=handle.tenant, role=role)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        client.user = user
        cache[key] = client
    return cache[key]
