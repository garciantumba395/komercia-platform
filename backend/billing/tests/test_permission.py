from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from .helpers import activate, update_sub

pytestmark = [pytest.mark.django_db, override_settings(ROOT_URLCONF="billing.tests.probe_urls")]

MESSAGE = ("Votre essai ou votre abonnement est terminé. "
           "Abonnez-vous pour continuer à enregistrer des ventes.")


def expire_trial(handle):
    update_sub(handle, trial_ends_at=timezone.now() - timedelta(minutes=1))


def test_write_allowed_during_trial(tenant_a):
    assert tenant_a.client().post("/api/v1/probe/").status_code == 200


def test_402_when_read_only(tenant_a):
    expire_trial(tenant_a)
    r = tenant_a.client().post("/api/v1/probe/")
    assert r.status_code == 402
    assert r.json() == {"detail": MESSAGE, "code": "subscription_inactive"}


def test_safe_methods_allowed_when_read_only(tenant_a):
    expire_trial(tenant_a)
    c = tenant_a.client()
    assert c.get("/api/v1/probe/").status_code == 200
    assert c.head("/api/v1/probe/").status_code == 200
    assert c.options("/api/v1/probe/").status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/auth/probe/", "/api/v1/billing/probe/",
                                  "/api/v1/sync/probe/", "/api/v1/reports/export/"])
def test_exempt_paths_always_writable(tenant_a, path):
    expire_trial(tenant_a)
    assert tenant_a.client().post(path).status_code == 200


def test_past_due_still_writable_then_blocked_after_grace(tenant_a):
    sub = activate(tenant_a, now=timezone.now() - timedelta(days=31))
    assert sub.current_period_end < timezone.now()
    assert tenant_a.client().post("/api/v1/probe/").status_code == 200  # grâce
    update_sub(tenant_a, current_period_end=timezone.now() - timedelta(days=8))
    assert tenant_a.client().post("/api/v1/probe/").status_code == 402


def test_active_subscription_writable_and_tenant_isolated(tenant_a, tenant_b):
    expire_trial(tenant_a)
    activate(tenant_a, now=timezone.now())
    assert tenant_a.client().post("/api/v1/probe/").status_code == 200
    expire_trial(tenant_b)
    assert tenant_b.client().post("/api/v1/probe/").status_code == 402
    assert tenant_a.client().post("/api/v1/probe/").status_code == 200


def test_unauthenticated_gets_401_not_402(db):
    from rest_framework.test import APIClient

    assert APIClient().post("/api/v1/probe/").status_code == 401


