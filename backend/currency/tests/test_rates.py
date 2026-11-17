"""Modèle ExchangeRate, services, API, isolation, permissions et immuabilité."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Membership, Store, User
from core.tenancy import tenant_context
from currency import services
from currency.models import AppendOnlyError, ExchangeRate
from currency.money import compute_payment

pytestmark = pytest.mark.django_db

URL = "/api/v1/currency/rates/"


def make_rate(handle, rate="2800", store=None, effective_at=None, **extra):
    with tenant_context(handle.tenant.id):
        return ExchangeRate.objects.create(
            store=store,
            rate_cdf_per_usd=Decimal(rate),
            effective_at=effective_at or timezone.now() - timedelta(minutes=1),
            set_by=handle.user,
            **extra,
        )


def client_with_role(handle, role, phone):
    user = User.objects.create_user(phone=phone, password="MotDePasse-123!", full_name=role)
    Membership.objects.create(user=user, tenant=handle.tenant, role=role)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
    return api


# --- services ----------------------------------------------------------------------------------
def test_current_rate_none_and_snapshot_raises(tenant_a):
    with tenant_context(tenant_a.tenant.id):
        assert services.current_rate() is None
        with pytest.raises(services.NoExchangeRate):
            services.snapshot()
        with pytest.raises(services.NoExchangeRate):
            services.snapshot(tenant_a.store.id)


def test_current_rate_is_latest_effective(tenant_a):
    now = timezone.now()
    make_rate(tenant_a, "2700", effective_at=now - timedelta(days=2))
    newest = make_rate(tenant_a, "2850.5", effective_at=now - timedelta(hours=1))
    make_rate(tenant_a, "3000", effective_at=now + timedelta(days=1))  # futur : pas encore effectif
    with tenant_context(tenant_a.tenant.id):
        assert services.current_rate().id == newest.id
        snap = services.snapshot()
    assert set(snap) == {"rate", "rate_id", "effective_at"}
    assert snap["rate"] == Decimal("2850.5") and isinstance(snap["rate"], Decimal)
    assert snap["rate_id"] == newest.id and snap["effective_at"] == newest.effective_at


def test_store_rate_overrides_tenant_default_with_fallback(tenant_a):
    other_store = _second_store(tenant_a)
    default = make_rate(tenant_a, "2800")
    specific = make_rate(tenant_a, "2900", store=tenant_a.store)
    with tenant_context(tenant_a.tenant.id):
        assert services.current_rate(tenant_a.store.id).id == specific.id
        assert services.current_rate(other_store.id).id == default.id  # repli sur le défaut
        assert services.current_rate().id == default.id
        assert services.snapshot(tenant_a.store.id)["rate"] == Decimal("2900")


def _second_store(handle):
    with tenant_context(handle.tenant.id):
        return Store.objects.create(name="Annexe")


def test_snapshot_is_frozen_when_rate_changes(tenant_a):
    """Le taux figé sur une vente ne bouge pas quand un nouveau taux est défini (KOM-037)."""
    make_rate(tenant_a, "2800", effective_at=timezone.now() - timedelta(days=1))
    with tenant_context(tenant_a.tenant.id):
        frozen = services.snapshot()
    new = make_rate(tenant_a, "2950")
    with tenant_context(tenant_a.tenant.id):
        assert services.snapshot()["rate"] == Decimal("2950")
        assert services.snapshot()["rate_id"] == new.id
        old = ExchangeRate.objects.get(id=frozen["rate_id"])
    assert frozen["rate"] == Decimal("2800") == old.rate_cdf_per_usd
    # Les calculs avec le taux figé restent identiques
    before = compute_payment(100_000, "CDF", [(1000, "USD")], frozen["rate"], "CDF")
    after = compute_payment(100_000, "CDF", [(1000, "USD")], old.rate_cdf_per_usd, "CDF")
    assert before == after and before.paid_minor == 28000


# --- append-only -------------------------------------------------------------------------------
def test_orm_cannot_update_or_delete(tenant_a):
    rate = make_rate(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        rate.rate_cdf_per_usd = Decimal("1")
        with pytest.raises(AppendOnlyError):
            rate.save()
        with pytest.raises(AppendOnlyError):
            rate.delete()
        with pytest.raises(AppendOnlyError):
            ExchangeRate.objects.all().update(note="x")
        with pytest.raises(AppendOnlyError):
            ExchangeRate.objects.all().delete()
        with pytest.raises(AppendOnlyError):
            ExchangeRate.objects.filter(id=rate.id).delete()


def test_database_trigger_blocks_raw_update_and_delete(tenant_a):
    rate = make_rate(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        for sql in (
            "UPDATE currency_exchangerate SET rate_cdf_per_usd = 1 WHERE id = %s",
            "UPDATE currency_exchangerate SET note = 'x'",
            "DELETE FROM currency_exchangerate WHERE id = %s",
            "DELETE FROM currency_exchangerate",
        ):
            with pytest.raises(DatabaseError, match="ajout seul"):
                with transaction.atomic(), connection.cursor() as cur:
                    cur.execute(sql, [rate.id] if "%s" in sql else None)
        assert ExchangeRate.objects.get(id=rate.id).rate_cdf_per_usd == Decimal("2800")


def test_database_rejects_non_positive_rate(tenant_a):
    with tenant_context(tenant_a.tenant.id):
        for bad in ("0", "-5"):
            with pytest.raises(DatabaseError):
                with transaction.atomic():
                    ExchangeRate.objects.create(rate_cdf_per_usd=Decimal(bad), set_by=tenant_a.user)


# --- API ---------------------------------------------------------------------------------------
def test_api_create_and_list(tenant_a):
    api = tenant_a.client()
    res = api.post(URL, {"rate_cdf_per_usd": "2850.5", "note": "Marché du matin"}, format="json")
    assert res.status_code == 201, res.data
    assert res.data["rate_cdf_per_usd"] == "2850.5000" and res.data["store"] is None
    assert res.data["set_by"] == str(tenant_a.user.id)
    res2 = api.post(
        URL, {"rate_cdf_per_usd": "2900", "store": str(tenant_a.store.id)}, format="json"
    )
    assert res2.status_code == 201
    listing = api.get(URL)
    assert listing.status_code == 200 and listing.data["count"] == 2
    only_store = api.get(URL, {"store": str(tenant_a.store.id)}).data["results"]
    assert [r["rate_cdf_per_usd"] for r in only_store] == ["2900.0000"]
    only_default = api.get(URL, {"store": "default"}).data["results"]
    assert [r["rate_cdf_per_usd"] for r in only_default] == ["2850.5000"]
    assert api.get(URL, {"store": "pas-un-uuid"}).status_code == 400
    assert api.get(f"{URL}{res.data['id']}/").status_code == 200


def test_api_history_is_ordered_newest_first(tenant_a):
    now = timezone.now()
    make_rate(tenant_a, "2700", effective_at=now - timedelta(days=3))
    make_rate(tenant_a, "2800", effective_at=now - timedelta(days=1))
    make_rate(tenant_a, "2750", effective_at=now - timedelta(days=2))
    rates = [r["rate_cdf_per_usd"] for r in tenant_a.client().get(URL).data["results"]]
    assert rates == ["2800.0000", "2750.0000", "2700.0000"]


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "", None, "0.00001", "99999999999.0"])
def test_api_rejects_invalid_rate(tenant_a, bad):
    res = tenant_a.client().post(URL, {"rate_cdf_per_usd": bad}, format="json")
    assert res.status_code == 400
    with tenant_context(tenant_a.tenant.id):
        assert ExchangeRate.objects.count() == 0


def test_api_current(tenant_a):
    api = tenant_a.client()
    assert api.get(URL + "current/").status_code == 404
    make_rate(tenant_a, "2800")
    res = api.get(URL + "current/")
    assert res.status_code == 200 and res.data["rate_cdf_per_usd"] == "2800.0000"
    assert res.data["is_store_rate"] is False
    make_rate(tenant_a, "2900", store=tenant_a.store)
    res = api.get(URL + "current/", {"store": str(tenant_a.store.id)})
    assert res.data["rate_cdf_per_usd"] == "2900.0000" and res.data["is_store_rate"] is True
    assert api.get(URL + "current/").data["rate_cdf_per_usd"] == "2800.0000"
    assert api.get(URL + "current/", {"store": "x"}).status_code == 400


def test_api_has_no_update_or_delete(tenant_a):
    api = tenant_a.client()
    rate = make_rate(tenant_a)
    detail = f"{URL}{rate.id}/"
    refused = (403, 405)  # refus par défaut de HasRole (action inconnue) ou méthode non autorisée
    assert api.put(detail, {"rate_cdf_per_usd": "1"}, format="json").status_code in refused
    assert api.patch(detail, {"rate_cdf_per_usd": "1"}, format="json").status_code in refused
    assert api.delete(detail).status_code in refused
    assert api.put(URL, {}, format="json").status_code in refused
    assert api.delete(URL).status_code in refused
    with tenant_context(tenant_a.tenant.id):
        assert ExchangeRate.objects.get(id=rate.id).rate_cdf_per_usd == Decimal("2800")


def test_api_requires_authentication(db):
    assert APIClient().get(URL).status_code in (401, 403)
    assert APIClient().post(URL, {"rate_cdf_per_usd": "2800"}, format="json").status_code in (401, 403)


# --- permissions (matrice section 19) ----------------------------------------------------------
@pytest.mark.parametrize(
    "role,can_write",
    [("owner", True), ("manager", True), ("cashier", False), ("storekeeper", False), ("accountant", False)],
)
def test_permissions_by_role(tenant_a, role, can_write):
    make_rate(tenant_a)
    phones = {"owner": 1, "manager": 2, "cashier": 3, "storekeeper": 4, "accountant": 5}
    api = client_with_role(tenant_a, role, f"+2439910000{phones[role]}")
    assert api.get(URL).status_code == 200
    assert api.get(URL + "current/").status_code == 200
    res = api.post(URL, {"rate_cdf_per_usd": "2900"}, format="json")
    assert res.status_code == (201 if can_write else 403)


# --- isolation multi-tenant --------------------------------------------------------------------
def test_tenant_isolation(tenant_a, tenant_b):
    make_rate(tenant_a, "2800")
    rate_b = make_rate(tenant_b, "3100", note="B")
    make_rate(tenant_b, "3200", store=tenant_b.store)
    api_a, api_b = tenant_a.client(), tenant_b.client()

    assert [r["rate_cdf_per_usd"] for r in api_a.get(URL).data["results"]] == ["2800.0000"]
    assert api_b.get(URL).data["count"] == 2
    assert api_a.get(URL + "current/").data["rate_cdf_per_usd"] == "2800.0000"
    assert api_a.get(f"{URL}{rate_b.id}/").status_code == 404
    # filtrer sur une boutique d'un autre tenant ne révèle rien
    assert api_a.get(URL, {"store": str(tenant_b.store.id)}).data["count"] == 0
    # impossible de créer un taux pour la boutique d'un autre tenant
    res = api_a.post(
        URL, {"rate_cdf_per_usd": "1", "store": str(tenant_b.store.id)}, format="json"
    )
    assert res.status_code == 400
    # le tenant ne peut pas être imposé par le client
    res = api_a.post(
        URL, {"rate_cdf_per_usd": "2810", "tenant": str(tenant_b.tenant.id)}, format="json"
    )
    assert res.status_code == 201
    with tenant_context(tenant_b.tenant.id):
        assert ExchangeRate.objects.count() == 2


def test_services_and_db_are_tenant_scoped(tenant_a, tenant_b):
    make_rate(tenant_a, "2800")
    with tenant_context(tenant_b.tenant.id):
        assert services.current_rate() is None
        assert ExchangeRate.objects.count() == 0
        with pytest.raises(services.NoExchangeRate):
            services.snapshot()
    with transaction.atomic(), connection.cursor() as cur:  # sans tenant actif : aucune ligne
        cur.execute("SELECT count(*) FROM currency_exchangerate")
        assert cur.fetchone()[0] == 0
