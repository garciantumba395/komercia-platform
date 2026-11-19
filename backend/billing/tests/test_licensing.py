"""Clés de licence hors ligne : format, activation, horloge qui ne recule pas, paiement en ligne désactivé."""

import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.utils import timezone

from audit.models import AuditEntry
from billing import clock, licensing
from billing import license_codec as codec
from billing.models import LicenseKeyUse, LicenseState, SubscriptionPayment
from billing.services import add_months, summary
from core.tenancy import tenant_context

from .helpers import activate as pay_and_activate
from .helpers import add_stores, get_sub, role_client, update_sub

pytestmark = pytest.mark.django_db

ACTIVATE = "/api/v1/billing/license/activate/"
KEYGEN = Path(__file__).resolve().parents[3] / "tools" / "license" / "keygen.py"


def _pem_private(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


@pytest.fixture
def keys(tmp_path, settings):
    """Paire de test : la clé publique remplace celle embarquée ; la privée sert à émettre."""
    private = Ed25519PrivateKey.generate()
    pub = tmp_path / "pub.pem"
    pub.write_bytes(private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    priv = tmp_path / "priv.pem"
    priv.write_bytes(_pem_private(private))
    settings.KOMERSIA_LICENSE_PUBLIC_KEY_FILE = str(pub)
    settings.BILLING_PROVIDER = ""  # édition locale : pas de paiement en ligne
    return private, priv


@pytest.fixture
def iid():
    return licensing.get_license_state().installation_id


def make_key(keys, installation, plan="pro", months=1, issued_at=None, nonce=None):
    payload = codec.LicensePayload(
        installation_id=installation, plan=plan, months=months,
        issued_at=int(time.time()) if issued_at is None else issued_at, nonce=nonce or codec.new_nonce())
    return codec.issue_key(keys[0], payload)


def post_key(handle, key):
    return handle.client().post(ACTIVATE, {"key": key}, format="json")


# --------------------------------------------------------------------------- format


def test_codec_roundtrip_and_alphabet(keys):
    installation = uuid.uuid4()
    key = make_key(keys, installation, "business", 12)
    assert all(len(g) == 5 for g in key.split("-")[:-1]) and 1 <= len(key.split("-")[-1]) <= 5
    assert not set(key.replace("-", "")) & set("01OI")
    body, signature = codec.decode_key(key.lower().replace("-", "  "))  # minuscules et espaces acceptés
    payload = codec.unpack_payload(body)
    assert (payload.installation_id, payload.plan, payload.months) == (installation, "business", 12)
    codec.verify_signature(keys[0].public_key(), body, signature)


def test_typo_is_caught_by_checksum_before_signature(keys, monkeypatch):
    key = make_key(keys, uuid.uuid4())
    chars = list(key)
    i = next(n for n, c in enumerate(chars) if c != "-")
    chars[i] = "A" if chars[i] != "A" else "B"
    # la vérification cryptographique ne doit même pas être tentée
    monkeypatch.setattr(codec, "verify_signature", lambda *a: pytest.fail("signature vérifiée"))
    with pytest.raises(codec.LicenseError) as err:
        codec.decode_key("".join(chars))
    assert err.value.message == "Clé mal saisie : vérifiez les caractères."
    for bad in ("", "ABC0O-1I", key[:-6], key + "AAAAA"):
        with pytest.raises(codec.LicenseError):
            codec.decode_key(bad)


def test_short_code_format_and_stability():
    u = uuid.uuid4()
    code = codec.short_code(u)
    assert len(code) == 9 and code[4] == "-" and code == codec.short_code(u)
    assert codec.short_code(uuid.uuid4()) != code


# --------------------------------------------------------------------------- identifiant d'installation


def test_installation_endpoint_roles_and_singleton(tenant_a):
    r = tenant_a.client().get("/api/v1/billing/installation/")
    assert r.status_code == 200
    data = r.json()
    assert uuid.UUID(data["installation_id"]) and data["short_code"] == codec.short_code(
        uuid.UUID(data["installation_id"]))
    assert tenant_a.client().get("/api/v1/billing/installation/").json() == data  # stable
    assert LicenseState.objects.count() == 1
    for role, phone in (("manager", "+243990000011"), ("accountant", "+243990000012")):
        assert role_client(tenant_a, role, phone).get("/api/v1/billing/installation/").status_code == 200
    cashier = role_client(tenant_a, "cashier", "+243990000013")
    assert cashier.get("/api/v1/billing/installation/").status_code == 403
    assert tenant_a.client().post("/api/v1/billing/installation/", {}).status_code == 405
    assert tenant_a.client().put("/api/v1/billing/installation/", {"installation_id": str(uuid.uuid4())}
                                 ).status_code == 405


# --------------------------------------------------------------------------- activation


def test_activation_from_trial(tenant_a, keys, iid):
    before = timezone.now()
    r = post_key(tenant_a, make_key(keys, iid, "pro", 1))
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["state"] == "active" and body["plan"] == "pro" and body["is_write_allowed"]
    sub = get_sub(tenant_a)
    assert sub.state == "active" and sub.grace_ends_at is None and sub.read_only_since is None
    assert abs(sub.current_period_end - add_months(before, 1)) < timedelta(minutes=1)
    with tenant_context(tenant_a.tenant.id):
        payment = SubscriptionPayment.objects.get()
        assert (payment.provider, payment.amount_minor, payment.months, payment.status) == (
            "license", 0, 1, "confirmed")
        assert payment.reference == LicenseKeyUse.objects.get().nonce
        entry = AuditEntry.objects.get(action="subscription.license_activated")
        assert entry.user_id == tenant_a.user.id and entry.after["plan"] == "pro"
        assert AuditEntry.objects.filter(action="subscription.start").exists()
    receipt = tenant_a.client().get(f"/api/v1/billing/payments/{payment.id}/receipt/?format=text")
    assert receipt.status_code == 200 and "Clé de licence" in receipt.content.decode()
    payments = tenant_a.client().get("/api/v1/billing/payments/").json()["results"]
    assert payments[0]["provider"] == "license" and payments[0]["months"] == 1


def test_same_key_twice_is_refused_and_extends_once(tenant_a, keys, iid):
    key = make_key(keys, iid)
    assert post_key(tenant_a, key).status_code == 200
    end = get_sub(tenant_a).current_period_end
    r = post_key(tenant_a, key.lower())
    assert r.status_code == 409 and r.json()["code"] == "license_already_used"
    assert r.json()["detail"] == "Cette clé a déjà été utilisée."
    assert get_sub(tenant_a).current_period_end == end
    assert LicenseKeyUse.objects.count() == 1


def test_same_key_refused_for_another_tenant(tenant_a, tenant_b, keys, iid):
    key = make_key(keys, iid)
    assert post_key(tenant_a, key).status_code == 200
    assert post_key(tenant_b, key).status_code == 409
    assert get_sub(tenant_b).state == "trial"


def test_advance_payment_loses_no_days(tenant_a, keys, iid):
    assert post_key(tenant_a, make_key(keys, iid, months=1)).status_code == 200
    first_end = get_sub(tenant_a).current_period_end
    assert post_key(tenant_a, make_key(keys, iid, months=3)).status_code == 200
    assert get_sub(tenant_a).current_period_end == add_months(first_end, 3)


def test_wrong_installation_refused_with_clear_message(tenant_a, keys, iid):
    r = post_key(tenant_a, make_key(keys, uuid.uuid4()))
    assert r.status_code == 400 and r.json()["code"] == "license_wrong_installation"
    assert "autre ordinateur" in r.json()["detail"] and codec.short_code(iid) in r.json()["detail"]
    assert get_sub(tenant_a).state == "trial" and not LicenseKeyUse.objects.exists()


def test_key_signed_by_someone_else_is_refused(tenant_a, keys, iid):
    other = (Ed25519PrivateKey.generate(),)
    r = post_key(tenant_a, make_key(other, iid))
    assert r.status_code == 400 and r.json()["code"] == "license_bad_signature"
    assert not LicenseKeyUse.objects.exists()


def test_malformed_key_message(tenant_a, keys, iid):
    key = make_key(keys, iid)
    broken = key[:-2] + ("AB" if key[-2:] != "AB" else "CD")
    r = post_key(tenant_a, broken)
    assert r.status_code == 400 and r.json() == {
        "detail": "Clé mal saisie : vérifiez les caractères.", "code": "license_malformed"}
    assert post_key(tenant_a, "").status_code == 400


def test_old_key_refused_and_age_is_configurable(tenant_a, keys, iid, settings):
    old = int((timezone.now() - timedelta(days=91)).timestamp())
    r = post_key(tenant_a, make_key(keys, iid, issued_at=old))
    assert r.status_code == 400 and r.json()["code"] == "license_too_old"
    ok = int((timezone.now() - timedelta(days=89)).timestamp())
    assert post_key(tenant_a, make_key(keys, iid, issued_at=ok)).status_code == 200
    settings.KOMERSIA_LICENSE_MAX_KEY_AGE_DAYS = 10
    eleven = int((timezone.now() - timedelta(days=11)).timestamp())
    r = post_key(tenant_a, make_key(keys, iid, issued_at=eleven))
    assert r.json()["code"] == "license_too_old"


def test_unknown_plan_refused(tenant_a, keys, iid):
    r = post_key(tenant_a, make_key(keys, iid, plan="platine"))
    assert r.status_code == 400 and r.json()["code"] == "unknown_plan"


def test_owner_only(tenant_a, keys, iid):
    key = make_key(keys, iid)
    for role, phone in (("manager", "+243990000021"), ("accountant", "+243990000022"),
                        ("cashier", "+243990000023")):
        client = role_client(tenant_a, role, phone)
        assert client.post(ACTIVATE, {"key": key}, format="json").status_code == 403
    assert not LicenseKeyUse.objects.exists()
    from rest_framework.test import APIClient
    assert APIClient().post(ACTIVATE, {"key": key}, format="json").status_code in (401, 403)


def test_activation_reactivates_read_only_account(tenant_a, keys, iid):
    update_sub(tenant_a, trial_ends_at=timezone.now() - timedelta(minutes=1))
    c = tenant_a.client()
    assert c.get("/api/v1/billing/subscription/").json()["state"] == "read_only"
    assert c.post("/api/v1/billing/notices/1/read/").status_code != 402  # route facturation exemptée
    assert post_key(tenant_a, make_key(keys, iid, "essentiel")).status_code == 200
    assert c.get("/api/v1/billing/subscription/").json()["state"] == "active"


def test_activation_clears_grace_and_read_only_after_late_payment(tenant_a, keys, iid, settings):
    now = timezone.now()
    settings.BILLING_PROVIDER = "sandbox"  # ancien paiement en ligne
    pay_and_activate(tenant_a, now=now - timedelta(days=35))
    settings.BILLING_PROVIDER = ""
    assert get_sub(tenant_a).state == "active"  # état stocké ; effectivement en retard
    assert summary(get_sub(tenant_a))["state"] == "past_due"
    assert post_key(tenant_a, make_key(keys, iid)).status_code == 200
    sub = get_sub(tenant_a)
    assert sub.grace_ends_at is None and sub.read_only_since is None
    assert summary(sub)["state"] == "active"


def test_upgrade_by_key_is_immediate_without_prorata(tenant_a, keys, iid):
    assert post_key(tenant_a, make_key(keys, iid, "essentiel")).status_code == 200
    assert post_key(tenant_a, make_key(keys, iid, "business")).json()["plan"] == "business"
    with tenant_context(tenant_a.tenant.id):
        assert SubscriptionPayment.objects.filter(provider="license").count() == 2


def test_lower_plan_refused_when_usage_exceeds_limits(tenant_a, keys, iid):
    add_stores(tenant_a, 2)  # 3 boutiques actives, essentiel : 1 seule
    key = make_key(keys, iid, "essentiel")
    r = post_key(tenant_a, key)
    assert r.status_code == 400 and r.json()["code"] == "limits_exceeded"
    assert "Le plan Essentiel est limité à 1 boutique" in r.json()["detail"]
    assert not LicenseKeyUse.objects.exists()  # la clé n'est pas consommée
    assert get_sub(tenant_a).state == "trial"


def test_activation_disabled_when_online_payments_are_configured(tenant_a, keys, iid, settings):
    settings.BILLING_PROVIDER = "sandbox"
    r = post_key(tenant_a, make_key(keys, iid))
    assert r.status_code == 400 and r.json()["code"] == "license_disabled"
    settings.KOMERSIA_EDITION = "local"  # l'édition locale active toujours la licence
    assert post_key(tenant_a, make_key(keys, iid)).status_code == 200


# --------------------------------------------------------------------------- Mobile Money désactivé


def test_online_payments_disabled_without_provider(tenant_a, keys):
    c = tenant_a.client()
    body = {"plan": "pro", "currency": "USD", "phone": "+243990000001"}
    for url in ("subscribe/", "change-plan/"):
        r = c.post("/api/v1/billing/" + url, body, format="json")
        assert r.status_code == 400
        assert r.json() == {"detail": "Le paiement en ligne n'est pas disponible. "
                                      "Activez une clé de licence.",
                            "code": "payments_disabled"}
    s = c.get("/api/v1/billing/subscription/").json()
    assert s["payments_enabled"] is False and s["license_enabled"] is True
    assert c.get("/api/v1/billing/plans/").status_code == 200
    with tenant_context(tenant_a.tenant.id):
        assert not SubscriptionPayment.objects.exists()


def test_summary_flags_with_sandbox_provider(tenant_a, settings):
    settings.BILLING_PROVIDER = "sandbox"
    s = tenant_a.client().get("/api/v1/billing/subscription/").json()
    assert s["payments_enabled"] is True and s["license_enabled"] is False
    settings.KOMERSIA_EDITION = "local"
    assert tenant_a.client().get("/api/v1/billing/subscription/").json()["license_enabled"] is True


# --------------------------------------------------------------------------- horloge


def set_last_seen(value):
    LicenseState.objects.update_or_create(pk=1, defaults={"last_seen_at": value})
    clock.reset_cache()


def test_clock_rollback_does_not_extend_trial(tenant_a):
    now = timezone.now()
    update_sub(tenant_a, trial_ends_at=now + timedelta(days=2))
    set_last_seen(now + timedelta(days=40))  # l'horloge est ensuite ramenée 40 jours en arrière
    c = tenant_a.client()
    s = c.get("/api/v1/billing/subscription/").json()
    assert s["state"] == "read_only" and s["is_write_allowed"] is False
    assert c.post("/api/v1/catalog/categories/", {"name": "X"}, format="json").status_code == 402
    assert clock.effective_now() >= now + timedelta(days=40)


def test_clock_rollback_does_not_extend_subscription(tenant_a):
    now = timezone.now()
    pay_and_activate(tenant_a, now=now - timedelta(days=5))
    set_last_seen(now + timedelta(days=40))
    s = tenant_a.client().get("/api/v1/billing/subscription/").json()
    assert s["state"] == "read_only"


def test_effective_now_never_goes_back_after_touch(tenant_a):
    ahead = timezone.now() + timedelta(days=3)
    set_last_seen(ahead)
    clock.touch()
    assert clock.effective_now() >= ahead
    assert LicenseState.objects.get().last_seen_at == ahead  # pas reculé en base


def test_touch_writes_at_most_once_per_minute(tenant_a, django_assert_num_queries):
    set_last_seen(timezone.now() - timedelta(hours=1))
    clock.touch()
    first = LicenseState.objects.get().last_seen_at
    assert first > timezone.now() - timedelta(minutes=1)
    with django_assert_num_queries(0):
        clock.touch()
        clock.touch()
        clock.effective_now()


def test_authenticated_request_updates_last_seen(tenant_a):
    set_last_seen(timezone.now() - timedelta(days=2))
    tenant_a.client().get("/api/v1/billing/installation/")
    assert LicenseState.objects.get().last_seen_at > timezone.now() - timedelta(minutes=1)


def test_billing_tick_updates_last_seen(tenant_a):
    from billing import services

    set_last_seen(timezone.now() - timedelta(days=2))
    services.tick()
    assert LicenseState.objects.get().last_seen_at > timezone.now() - timedelta(minutes=1)


def test_absurd_forward_jump_is_not_recorded(tenant_a, caplog):
    old = timezone.now() - timedelta(days=500)
    set_last_seen(old)
    with caplog.at_level("WARNING", logger="billing.clock"):
        clock.touch()
    assert LicenseState.objects.get().last_seen_at == old
    assert "aberrante" in caplog.text
    # un saut raisonnable (moins de 400 jours) est enregistré
    set_last_seen(timezone.now() - timedelta(days=399))
    clock.touch()
    assert LicenseState.objects.get().last_seen_at > timezone.now() - timedelta(minutes=1)


def test_activation_uses_non_receding_clock(tenant_a, keys, iid):
    ahead = timezone.now() + timedelta(days=40)
    set_last_seen(ahead)
    assert post_key(tenant_a, make_key(keys, iid)).status_code == 200
    assert get_sub(tenant_a).current_period_end >= add_months(ahead, 1)


# --------------------------------------------------------------------------- outil du vendeur


def run_keygen(*args):
    return subprocess.run([sys.executable, str(KEYGEN), *args], capture_output=True, text=True, timeout=60)


def test_keygen_roundtrip_with_application(tenant_a, keys, iid):
    _, priv = keys
    short = codec.short_code(iid)
    done = run_keygen("issue", "--installation", f"{short} {iid}", "--plan", "pro", "--months", "2",
                      "--key-file", str(priv))
    assert done.returncode == 0, done.stderr
    key = done.stdout.strip()
    r = post_key(tenant_a, key)
    assert r.status_code == 200 and r.json()["plan"] == "pro"
    with tenant_context(tenant_a.tenant.id):
        assert SubscriptionPayment.objects.get().months == 2


def test_keygen_checks_short_code_and_months(keys, iid):
    _, priv = keys
    bad = run_keygen("issue", "--installation", f"ZZZZ-ZZZZ {iid}", "--plan", "pro", "--key-file", str(priv))
    assert bad.returncode != 0 and "ne correspond pas" in bad.stderr
    no_uuid = run_keygen("issue", "--installation", codec.short_code(iid), "--plan", "pro",
                         "--key-file", str(priv))
    assert no_uuid.returncode != 0
    assert run_keygen("issue", "--installation", str(iid), "--plan", "pro", "--months", "13",
                      "--key-file", str(priv)).returncode != 0


def test_keygen_init_creates_private_key_with_restricted_rights(tmp_path):
    done = run_keygen("init", "--out-dir", str(tmp_path))
    assert done.returncode == 0, done.stderr
    private = tmp_path / "komersia-license-private.pem"
    assert private.exists() and oct(private.stat().st_mode & 0o777) == "0o600"
    assert (tmp_path / "license_public_key.pem").exists() and "HORS du dépôt" in done.stdout
    assert run_keygen("init", "--out-dir", str(tmp_path)).returncode != 0  # refus d'écraser


def test_embedded_public_key_loads_and_private_key_is_not_in_repo():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    assert isinstance(licensing._load_public_key(str(licensing.DEFAULT_PUBLIC_KEY_FILE), 0), Ed25519PublicKey)
    root = Path(__file__).resolve().parents[3]
    tracked = subprocess.run(["git", "ls-files", "tools/license"], cwd=root, capture_output=True, text=True)
    assert "private" not in tracked.stdout
    assert "BEGIN PRIVATE KEY" not in licensing.DEFAULT_PUBLIC_KEY_FILE.read_text()
