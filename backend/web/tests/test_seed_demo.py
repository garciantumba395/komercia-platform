import pytest
from django.core.management import call_command
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Membership, User
from accounts.services import issue_token
from catalog.models import Product
from core.tenancy import tenant_context
from currency.models import ExchangeRate
from customers.models import Customer
from stock.models import StockLevel

pytestmark = pytest.mark.django_db


def seed(capsys=None):
    call_command("seed_demo", verbosity=0)


def test_seed_creates_demo_data(db):
    seed()
    owner = User.objects.get(phone="+243990000000")
    assert owner.check_password("Demo-1234!")
    tenant = owner.memberships.get().tenant
    assert tenant.name == "Boutique Démo" and tenant.sector == "retail"
    cashier = User.objects.get(phone="+243990000001")
    assert cashier.check_password("Caissier-1234!")
    assert Membership.objects.get(user=cashier).role == "cashier"
    with tenant_context(tenant.id):
        assert 38 <= Product.objects.count() <= 50
        assert Product.objects.filter(currency="USD").exists()
        assert Product.objects.filter(currency="CDF").exists()
        assert not Product.objects.filter(barcode="").exists()
        assert not Product.objects.filter(cost_minor=0).exists()
        rate = ExchangeRate.objects.get(store__isnull=True)
        assert str(rate.rate_cdf_per_usd).startswith("2800")
        assert Customer.objects.count() == 6
        assert Customer.objects.filter(credit_limit_minor__gt=0).count() >= 3
        assert StockLevel.objects.count() == Product.objects.count()
        low = [lvl for lvl in StockLevel.objects.select_related("product")
               if lvl.quantity <= lvl.product.min_stock]
        assert len(low) >= 4


def test_seed_is_idempotent(db):
    seed()
    seed()
    owner = User.objects.get(phone="+243990000000")
    tenant = owner.memberships.get().tenant
    assert User.objects.filter(phone__in=["+243990000000", "+243990000001"]).count() == 2
    with tenant_context(tenant.id):
        assert ExchangeRate.objects.count() == 1
        count = Product.objects.count()
        assert Customer.objects.count() == 6
    seed()
    with tenant_context(tenant.id):
        assert Product.objects.count() == count


def test_demo_accounts_can_log_in_through_the_api(db):
    seed()
    client = APIClient()
    accounts = [("+243990000000", "Demo-1234!", "owner"), ("+243990000001", "Caissier-1234!", "cashier")]
    for phone, password, role in accounts:
        response = client.post("/api/v1/auth/login/", {"phone": phone, "password": password}, format="json")
        assert response.status_code == 200 and response.json()["role"] == role
    token = Token.objects.get(user__phone="+243990000000")
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    assert client.get("/api/v1/currency/rates/current/").json()["rate_cdf_per_usd"].startswith("2800")


def test_seed_creates_sample_sales_when_sales_module_exists(db):
    pytest.importorskip("sales.services")
    seed()
    seed()
    owner = User.objects.get(phone="+243990000000")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {issue_token(owner)}")
    data = client.get("/api/v1/sales/").json()
    assert data["count"] == 15
