"""Liste et fiche des entreprises (KOM-080)."""

from datetime import timedelta

from django.utils import timezone

from accounts.models import Membership, Tenant, User
from billing.models import Plan, Subscription
from core.permissions import CASHIER
from core.tenancy import tenant_context

from .helpers import DB, DAY, agent_client, make_sale

pytestmark = DB


def _set_sub(handle, **fields):
    with tenant_context(handle.tenant.id):
        sub = Subscription.objects.get()
        for key, value in fields.items():
            setattr(sub, key, value)
        sub.save()


def test_list_shows_columns(tenant_a):
    make_sale(tenant_a, "A-1", age=2 * DAY)
    plan = Plan.objects.get(code="pro")
    _set_sub(tenant_a, state="active", plan=plan, current_period_end=timezone.now() + 10 * DAY)
    html = agent_client().get("/ops/tenants/").content.decode()
    assert "Commerce A" in html and "+243990000001" in html
    assert "Actif" in html and "Pro" in html and "Active" in html
    assert "Commerce de détail" in html


def test_list_counts_stores_and_users(tenant_a):
    with tenant_context(tenant_a.tenant.id):
        from accounts.models import Store

        Store.objects.create(name="Deuxième")
    cashier = User.objects.create_user(phone="+243990000077", password="x-Secret-1234", full_name="Caissier")
    Membership.objects.create(user=cashier, tenant=tenant_a.tenant, role=CASHIER)
    from backoffice import queries

    row = queries.tenant_list_queryset().get(pk=tenant_a.tenant.id)
    assert row.store_count == 2 and row.user_count == 2
    assert row.owner_phone == "+243990000001"


def test_search_by_name_and_owner_phone(tenant_a, tenant_b):
    client = agent_client()
    by_name = client.get("/ops/tenants/", {"q": "commerce a"}).content.decode()
    assert "Commerce A" in by_name and "Commerce B" not in by_name
    by_phone = client.get("/ops/tenants/", {"q": "990000002"}).content.decode()
    assert "Commerce B" in by_phone and "Commerce A" not in by_phone


def test_filter_by_subscription_state_and_status(tenant_a, tenant_b):
    _set_sub(tenant_b, state="past_due")
    client = agent_client()
    past_due = client.get("/ops/tenants/", {"state": "past_due"}).content.decode()
    assert "Commerce B" in past_due and "Commerce A" not in past_due
    Tenant.objects.filter(pk=tenant_a.tenant.id).update(is_active=False)
    suspended = client.get("/ops/tenants/", {"status": "suspended"}).content.decode()
    assert "Commerce A" in suspended and "Commerce B" not in suspended
    active = client.get("/ops/tenants/", {"status": "active"}).content.decode()
    assert "Commerce B" in active and "Commerce A" not in active


def test_pagination():
    for i in range(30):
        Tenant.objects.create(name=f"Entreprise {i:02d}")
    client = agent_client()
    first = client.get("/ops/tenants/").content.decode()
    assert "Page 1 / 2" in first and first.count("/ops/tenants/") >= 25
    assert "Page 2 / 2" in client.get("/ops/tenants/", {"page": 2}).content.decode()


def test_detail_has_operating_info_only_without_consent(tenant_a):
    make_sale(tenant_a, "A-SECRET-1", total=123456)
    html = agent_client().get(f"/ops/tenants/{tenant_a.tenant.id}/").content.decode()
    assert "Commerce A" in html and "Patron Commerce A" in html and "+243990000001" in html
    assert "Aucun accès" in html
    assert "A-SECRET-1" not in html and "123 456" not in html  # aucune vente détaillée
    assert "Ventes ce mois (nombre)" in html


def test_detail_never_shows_password_hash_or_token(tenant_a):
    html = agent_client().get(f"/ops/tenants/{tenant_a.tenant.id}/").content.decode()
    user = User.objects.get(pk=tenant_a.user.pk)
    assert user.password not in html and "pbkdf2" not in html and "argon2" not in html
    assert tenant_a.token.key not in html
    assert "MotDePasse-123!" not in html


def test_detail_unknown_tenant_404():
    assert agent_client().get("/ops/tenants/00000000-0000-4000-8000-000000000000/").status_code == 404


def test_detail_lists_audit_and_payments(tenant_a):
    from audit import services as audit

    with tenant_context(tenant_a.tenant.id):
        audit.log("sale.cancel", "sale", "1", reason="Erreur de caisse", user=tenant_a.user)
    html = agent_client().get(f"/ops/tenants/{tenant_a.tenant.id}/").content.decode()
    assert "sale.cancel" in html and "Erreur de caisse" in html


def test_recent_activity_is_last_sale(tenant_a):
    from backoffice import queries

    make_sale(tenant_a, "A-1", age=timedelta(days=3))
    make_sale(tenant_a, "A-2", age=timedelta(days=1))
    row = queries.tenant_list_queryset().get(pk=tenant_a.tenant.id)
    assert timezone.now() - row.last_sale_at < timedelta(days=1, hours=1)
