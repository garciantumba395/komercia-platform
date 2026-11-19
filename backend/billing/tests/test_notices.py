from datetime import timedelta

import pytest

from billing import services
from billing.models import BillingNotice
from core.tenancy import tenant_context

from .helpers import NOW, activate, get_sub, update_sub

pytestmark = pytest.mark.django_db


def kinds(handle):
    with tenant_context(handle.tenant.id):
        return sorted(BillingNotice.objects.values_list("kind", flat=True))


@pytest.mark.parametrize("days_before,kind", [(7, "trial_7"), (5, "trial_7"), (3, "trial_3"),
                                              (1, "trial_1")])
def test_trial_notices(tenant_a, days_before, kind):
    update_sub(tenant_a, trial_ends_at=NOW)
    services.tick(NOW - timedelta(days=days_before))
    assert kinds(tenant_a) == [kind]


def test_trial_last_day_notice(tenant_a):
    update_sub(tenant_a, trial_ends_at=NOW)
    services.tick(NOW - timedelta(hours=2))
    assert kinds(tenant_a) == ["trial_0"]


def test_no_trial_notice_before_7_days(tenant_a):
    update_sub(tenant_a, trial_ends_at=NOW)
    services.tick(NOW - timedelta(days=10))
    assert kinds(tenant_a) == []


def test_trial_sequence_without_duplicates_on_replay(tenant_a):
    update_sub(tenant_a, trial_ends_at=NOW)
    for days in (7, 3, 1):
        for _ in range(3):  # tick rejoué
            services.tick(NOW - timedelta(days=days))
    services.tick(NOW - timedelta(hours=1))
    services.tick(NOW - timedelta(hours=1))
    services.tick(NOW + timedelta(minutes=1))
    services.tick(NOW + timedelta(minutes=2))
    assert kinds(tenant_a) == ["read_only", "trial_0", "trial_1", "trial_3", "trial_7"]


def test_due_notices_and_overdue_sequence(tenant_a):
    sub = activate(tenant_a, now=NOW)
    end = sub.current_period_end
    for delta in (timedelta(days=7), timedelta(days=3), timedelta(hours=2)):
        services.tick(end - delta)
        services.tick(end - delta)
    assert kinds(tenant_a) == ["due_0", "due_3", "due_7"]
    services.tick(end + timedelta(days=1))
    services.tick(end + timedelta(days=2))
    services.tick(end + timedelta(days=7))
    services.tick(end + timedelta(days=8))
    assert kinds(tenant_a) == ["due_0", "due_3", "due_7", "past_due", "read_only"]


def test_new_period_gets_new_due_notices(tenant_a):
    sub = activate(tenant_a, now=NOW)
    services.tick(sub.current_period_end - timedelta(days=7))
    from .helpers import confirm, make_payment

    confirm(tenant_a, make_payment(tenant_a), sub.current_period_end - timedelta(days=6))
    sub = get_sub(tenant_a)
    services.tick(sub.current_period_end - timedelta(days=7))
    assert kinds(tenant_a) == ["due_7", "due_7"]  # une par échéance


def test_no_due_notice_when_cancelled(tenant_a):
    sub = activate(tenant_a, now=NOW)
    with tenant_context(tenant_a.tenant.id):
        services.cancel(get_sub(tenant_a), now=NOW)
    services.tick(sub.current_period_end - timedelta(days=3))
    assert kinds(tenant_a) == []


def test_notices_api_and_mark_read(tenant_a, tenant_b):
    update_sub(tenant_a, trial_ends_at=NOW)
    services.tick(NOW - timedelta(days=3))
    client = tenant_a.client()
    rows = client.get("/api/v1/billing/notices/").json()["results"]
    assert [r["kind"] for r in rows] == ["trial_3"] and rows[0]["read_at"] is None
    assert tenant_b.client().get("/api/v1/billing/notices/").json()["results"] == []
    assert tenant_b.client().post(f"/api/v1/billing/notices/{rows[0]['id']}/read/").status_code == 404
    r = client.post(f"/api/v1/billing/notices/{rows[0]['id']}/read/")
    assert r.status_code == 200 and r.json()["read_at"] is not None
    assert client.get("/api/v1/billing/notices/?unread=1").json()["results"] == []
