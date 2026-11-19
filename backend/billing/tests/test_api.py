from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from audit.models import AuditEntry
from billing import services
from billing.models import PaymentReference, Subscription, SubscriptionPayment
from core.permissions import ACCOUNTANT, CASHIER, MANAGER, STOREKEEPER
from core.tenancy import activate_tenant, tenant_context

from .helpers import (
    NOW,
    SECRET,
    activate,
    add_stores,
    get_sub,
    post_webhook,
    role_client,
    set_rate,
    update_sub,
)

pytestmark = pytest.mark.django_db
B = "/api/v1/billing"
SUBSCRIBE = {"plan": "pro", "currency": "USD", "phone": "+243990000001"}


def sandbox_confirm(reference, status="confirmed", client=None):
    from rest_framework.test import APIClient

    return (client or APIClient()).post(f"{B}/sandbox/confirm/", {"reference": reference, "status": status},
                                        format="json")


def test_plans_list_with_cdf_estimate(tenant_a):
    r = tenant_a.client().get(f"{B}/plans/")
    assert r.status_code == 200
    plans = {p["code"]: p for p in r.json()}
    assert list(plans) == ["essentiel", "pro", "business"]
    assert plans["pro"]["price_usd_minor"] == 1500 and plans["pro"]["price_cdf_estimate"] is None
    set_rate(tenant_a, "2800")
    plans = {p["code"]: p for p in tenant_a.client().get(f"{B}/plans/").json()}
    assert plans["pro"]["price_cdf_estimate"] == 42000
    assert plans["business"]["extra_store_price_usd_minor"] == 1000


def test_subscription_summary_in_trial(tenant_a):
    r = tenant_a.client().get(f"{B}/subscription/")
    data = r.json()
    assert r.status_code == 200
    assert data["state"] == "trial" and data["plan"] is None and data["is_write_allowed"] is True
    assert data["next_action"] == "subscribe" and data["days_left"] == 14
    assert data["limits"] == {"max_stores": 3, "max_users": 10, "stores_used": 1, "users_used": 1}


def test_full_flow_subscribe_confirm_receipt(tenant_a):
    client = tenant_a.client()
    r = client.post(f"{B}/subscribe/", SUBSCRIBE, format="json")
    assert r.status_code == 201
    body = r.json()
    assert body["payment"]["status"] == "pending" and body["payment"]["amount_minor"] == 1500
    assert body["payment"]["currency"] == "USD" and body["payment"]["reference"]
    assert body["instructions"]
    assert client.get(f"{B}/subscription/").json()["state"] == "trial"

    assert sandbox_confirm(body["payment"]["reference"]).status_code == 200
    data = client.get(f"{B}/subscription/").json()
    assert data["state"] == "active" and data["plan"] == "pro" and data["next_action"] is None
    assert 28 <= data["days_left"] <= 31

    payments = client.get(f"{B}/payments/").json()["results"]
    assert len(payments) == 1 and payments[0]["status"] == "confirmed"
    pid = payments[0]["id"]
    html = client.get(f"{B}/payments/{pid}/receipt/?format=html")
    assert html.status_code == 200 and html["Content-Type"].startswith("text/html")
    assert "Reçu d'abonnement" in html.content.decode() and "Commerce A" in html.content.decode()
    text = client.get(f"{B}/payments/{pid}/receipt/?format=text")
    assert text["Content-Type"].startswith("text/plain")
    assert body["payment"]["reference"] in text.content.decode() and "$15.00" in text.content.decode()
    assert client.get(f"{B}/payments/{pid}/receipt/").status_code == 200  # html par défaut
    assert client.get(f"{B}/payments/{pid}/receipt/?format=pdf").status_code == 400


def test_receipt_cdf_shows_rate_and_pending_has_none(tenant_a):
    set_rate(tenant_a, "2800")
    client = tenant_a.client()
    ref = client.post(f"{B}/subscribe/", {**SUBSCRIBE, "currency": "CDF"}, format="json").json()
    pid = client.get(f"{B}/payments/").json()["results"][0]["id"]
    assert client.get(f"{B}/payments/{pid}/receipt/").status_code == 400  # non confirmé
    sandbox_confirm(ref["payment"]["reference"])
    text = client.get(f"{B}/payments/{pid}/receipt/?format=text").content.decode()
    assert "42 000 FC" in text and "1 USD = 2800" in text and "$15.00" in text


def test_subscribe_validation(tenant_a):
    c = tenant_a.client()
    assert c.post(f"{B}/subscribe/", {**SUBSCRIBE, "plan": "inconnu"}, format="json").status_code == 400
    assert c.post(f"{B}/subscribe/", {**SUBSCRIBE, "currency": "EUR"}, format="json").status_code == 400
    assert c.post(f"{B}/subscribe/", {**SUBSCRIBE, "phone": "abc"}, format="json").status_code == 400
    assert c.post(f"{B}/subscribe/", {"plan": "pro", "currency": "USD"}, format="json").status_code == 400
    with tenant_context(tenant_a.tenant.id):
        assert SubscriptionPayment.objects.count() == 0


def test_change_plan_api_upgrade_and_downgrade(tenant_a):
    client = tenant_a.client()
    activate(tenant_a, now=timezone.now(), plan="essentiel")
    r = client.post(f"{B}/change-plan/", {"plan": "pro"}, format="json")
    assert r.status_code == 201 and r.json()["kind"] == "upgrade"
    assert 0 < r.json()["payment"]["amount_minor"] <= 1000
    sandbox_confirm(r.json()["payment"]["reference"])
    assert client.get(f"{B}/subscription/").json()["plan"] == "pro"
    r = client.post(f"{B}/change-plan/", {"plan": "essentiel"}, format="json")
    assert r.status_code == 200 and r.json()["kind"] == "downgrade"
    assert r.json()["pending_plan"] == "essentiel"
    assert client.get(f"{B}/subscription/").json()["pending_plan"] == "essentiel"
    add_stores(tenant_a, 1)
    r = client.post(f"{B}/change-plan/", {"plan": "essentiel"}, format="json")
    assert r.status_code == 400 and r.json()["code"] == "limits_exceeded"
    r = client.post(f"{B}/change-plan/", {"plan": "pro"}, format="json")  # rester sur le plan actuel
    assert r.status_code == 200 and r.json() == {"kind": "unchanged", "pending_plan": None,
                                                 "effective_at": None}
    r = client.post(f"{B}/change-plan/", {"plan": "pro"}, format="json")
    assert r.status_code == 400 and r.json()["code"] == "same_plan"


def test_cancel_and_resume_api(tenant_a):
    client = tenant_a.client()
    assert client.post(f"{B}/cancel/").status_code == 400  # essai : rien à résilier
    activate(tenant_a, now=timezone.now())
    r = client.post(f"{B}/cancel/")
    assert r.status_code == 200 and r.json()["cancel_at_period_end"] is True
    assert r.json()["state"] == "active" and r.json()["next_action"] is None
    r = client.post(f"{B}/resume/")
    assert r.status_code == 200 and r.json()["cancel_at_period_end"] is False
    assert client.post(f"{B}/resume/").status_code == 400


def test_next_action_pay_when_due_soon_or_late(tenant_a):
    sub = activate(tenant_a, now=NOW)
    assert services.summary(sub, NOW + timedelta(days=10))["next_action"] is None
    assert services.summary(sub, sub.current_period_end - timedelta(days=5))["next_action"] == "pay"
    late = services.summary(sub, sub.current_period_end + timedelta(days=2))
    assert late["state"] == "past_due" and late["next_action"] == "pay" and late["days_left"] == 5
    ro = services.summary(sub, sub.current_period_end + timedelta(days=9))
    assert ro["state"] == "read_only" and ro["is_write_allowed"] is False and ro["next_action"] == "pay"


def test_read_only_subscription_can_still_pay_via_api(tenant_a):
    update_sub(tenant_a, trial_ends_at=timezone.now() - timedelta(days=1))
    client = tenant_a.client()
    assert client.get(f"{B}/subscription/").json()["state"] == "read_only"
    r = client.post(f"{B}/subscribe/", SUBSCRIBE, format="json")
    sandbox_confirm(r.json()["payment"]["reference"])
    assert client.get(f"{B}/subscription/").json()["state"] == "active"


# --- rôles ---------------------------------------------------------------------------------


@pytest.mark.parametrize("role,read_ok", [(MANAGER, True), (ACCOUNTANT, True), (CASHIER, False),
                                          (STOREKEEPER, False)])
def test_read_permissions_by_role(tenant_a, role, read_ok):
    client = role_client(tenant_a, role, "+243990009999")
    for url in ("plans/", "subscription/", "payments/", "notices/"):
        assert (client.get(f"{B}/{url}").status_code == 200) is read_ok, url


@pytest.mark.parametrize("role", [MANAGER, ACCOUNTANT, CASHIER, STOREKEEPER])
def test_write_is_owner_only(tenant_a, role):
    client = role_client(tenant_a, role, "+243990009998")
    assert client.post(f"{B}/subscribe/", SUBSCRIBE, format="json").status_code == 403
    for url in ("change-plan/", "cancel/", "resume/"):
        assert client.post(f"{B}/{url}", {"plan": "pro"}, format="json").status_code == 403, url
    with tenant_context(tenant_a.tenant.id):
        assert SubscriptionPayment.objects.count() == 0


def test_anonymous_cannot_read(db):
    from rest_framework.test import APIClient

    assert APIClient().get(f"{B}/subscription/").status_code == 401


# --- sandbox et webhook ----------------------------------------------------------------------


def test_sandbox_confirm_failure_and_unknown(tenant_a):
    ref = tenant_a.client().post(f"{B}/subscribe/", SUBSCRIBE, format="json").json()["payment"]["reference"]
    assert sandbox_confirm(ref, "failed").json()["status"] == "failed"
    assert get_sub(tenant_a).state == "trial"
    assert sandbox_confirm("KOM-INCONNU").status_code == 404
    assert sandbox_confirm(ref, "bogus").status_code == 400


def test_sandbox_unavailable_unless_configured(tenant_a):
    ref = tenant_a.client().post(f"{B}/subscribe/", SUBSCRIBE, format="json").json()["payment"]["reference"]
    with override_settings(BILLING_PROVIDER="mobilemoney"):
        assert sandbox_confirm(ref).status_code == 404
    assert get_sub(tenant_a).state == "trial"
    with override_settings(BILLING_PROVIDER="sandbox"):
        assert sandbox_confirm(ref).status_code == 200


@override_settings(BILLING_WEBHOOK_SECRET=SECRET)
def test_webhook_valid_signature_confirms_and_replay_is_idempotent(tenant_a):
    ref = tenant_a.client().post(f"{B}/subscribe/", SUBSCRIBE, format="json").json()["payment"]["reference"]
    r = post_webhook(ref, "confirmed")
    assert r.status_code == 200 and r.json() == {"reference": ref, "status": "confirmed"}
    end = get_sub(tenant_a).current_period_end
    assert post_webhook(ref, "confirmed").status_code == 200  # rejeu
    assert get_sub(tenant_a).current_period_end == end
    with tenant_context(tenant_a.tenant.id):
        assert AuditEntry.objects.filter(action="subscription.payment_confirmed").count() == 1


@override_settings(BILLING_WEBHOOK_SECRET=SECRET)
def test_webhook_invalid_or_missing_signature_rejected(tenant_a):
    ref = tenant_a.client().post(f"{B}/subscribe/", SUBSCRIBE, format="json").json()["payment"]["reference"]
    assert post_webhook(ref, "confirmed", signature="0" * 64).status_code == 403
    assert post_webhook(ref, "confirmed", signature=None).status_code == 403
    assert post_webhook(ref, "confirmed", signature="").status_code == 403
    assert get_sub(tenant_a).state == "trial"


def test_webhook_refused_without_configured_secret(tenant_a):
    ref = tenant_a.client().post(f"{B}/subscribe/", SUBSCRIBE, format="json").json()["payment"]["reference"]
    assert post_webhook(ref, "confirmed").status_code == 403  # signature valide mais secret absent
    assert post_webhook(ref, "confirmed", signature="sha256=").status_code == 403
    assert get_sub(tenant_a).state == "trial"


@override_settings(BILLING_WEBHOOK_SECRET=SECRET)
def test_webhook_unknown_provider_reference_and_bad_payload(tenant_a):
    assert post_webhook("KOM-INCONNU", "confirmed").status_code == 404
    assert post_webhook("KOM-X", "confirmed", provider="autre").status_code == 404
    assert post_webhook("KOM-X", "bogus").status_code == 400


@override_settings(BILLING_WEBHOOK_SECRET=SECRET)
def test_webhook_failed_then_late_confirmation_ignored(tenant_a):
    ref = tenant_a.client().post(f"{B}/subscribe/", SUBSCRIBE, format="json").json()["payment"]["reference"]
    assert post_webhook(ref, "failed").json()["status"] == "failed"
    assert post_webhook(ref, "confirmed").json()["status"] == "failed"
    assert get_sub(tenant_a).state == "trial"


# --- isolation -----------------------------------------------------------------------------


@override_settings(BILLING_WEBHOOK_SECRET=SECRET)
def test_isolation_between_tenants(tenant_a, tenant_b):
    ref_a = tenant_a.client().post(f"{B}/subscribe/", SUBSCRIBE, format="json").json()["payment"]["reference"]
    # B ne voit ni l'abonnement, ni les paiements, ni les rappels de A
    update_sub(tenant_a, trial_ends_at=NOW)
    services.tick(NOW - timedelta(days=3))
    cb = tenant_b.client()
    assert cb.get(f"{B}/payments/").json()["results"] == []
    assert cb.get(f"{B}/notices/").json()["results"] == []
    assert cb.get(f"{B}/subscription/").json()["state"] == "trial"
    pid = tenant_a.client().get(f"{B}/payments/").json()["results"][0]["id"]
    assert cb.get(f"{B}/payments/{pid}/receipt/").status_code == 404
    with tenant_context(tenant_b.tenant.id):
        assert Subscription.objects.get().tenant_id == tenant_b.tenant.id
        assert SubscriptionPayment.objects.count() == 0
    # Un webhook ne confirme que le paiement de SA référence
    ref_b = cb.post(f"{B}/subscribe/", {**SUBSCRIBE, "plan": "essentiel"}, format="json").json()["payment"][
        "reference"]
    assert post_webhook(ref_b, "confirmed").status_code == 200
    assert get_sub(tenant_b).state == "active" and get_sub(tenant_b).plan.code == "essentiel"
    assert get_sub(tenant_a).state == "trial"
    with tenant_context(tenant_a.tenant.id):
        assert SubscriptionPayment.objects.get(reference=ref_a).status == "pending"
    with tenant_context(tenant_b.tenant.id):  # A n'est pas atteignable depuis le contexte de B
        assert not SubscriptionPayment.objects.filter(reference=ref_a).exists()


def test_payment_reference_has_no_rls_and_leaks_nothing(tenant_a, tenant_b):
    ref = tenant_a.client().post(f"{B}/subscribe/", SUBSCRIBE, format="json").json()["payment"]["reference"]
    # lisible hors contexte de tenant (webhook) ...
    assert PaymentReference.objects.get(reference=ref).tenant_id == tenant_a.tenant.id
    # ... alors que les tables métier sont invisibles sans contexte de tenant
    activate_tenant(None)
    assert Subscription.objects.count() == 0 and SubscriptionPayment.objects.count() == 0
    # la table ne contient que la référence opaque et le tenant : aucune donnée métier
    assert {f.name for f in PaymentReference._meta.get_fields()} == {"id", "reference", "tenant_id"}
    # et aucune route ne l'expose
    for url in ("payments/", "subscription/", "plans/", "notices/"):
        assert ref not in tenant_b.client().get(f"{B}/{url}").content.decode()


# --- audit ---------------------------------------------------------------------------------


def test_audit_actions(tenant_a):
    client = tenant_a.client()
    ref = client.post(f"{B}/subscribe/", {**SUBSCRIBE, "plan": "essentiel"}, format="json").json()["payment"][
        "reference"]
    sandbox_confirm(ref)
    r = client.post(f"{B}/change-plan/", {"plan": "pro"}, format="json")
    sandbox_confirm(r.json()["payment"]["reference"])
    client.post(f"{B}/cancel/")
    services.tick(timezone.now() + timedelta(days=40))
    with tenant_context(tenant_a.tenant.id):
        entries = {e.action: e for e in AuditEntry.objects.all().order_by("created_at")}
        assert {"subscription.start", "subscription.payment_confirmed", "subscription.plan_change",
                "subscription.cancel", "subscription.state_change"} <= set(entries)
        assert entries["subscription.cancel"].user == tenant_a.user
        assert entries["subscription.state_change"].user is None
        assert entries["subscription.start"].entity_type == "subscription"
