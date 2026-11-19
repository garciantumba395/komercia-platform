from datetime import timedelta

import pytest
from django.utils import timezone

from audit.models import AuditEntry
from billing import services
from billing.models import Plan, Subscription
from billing.services import add_months, evaluate, is_write_allowed, refresh_state
from core.tenancy import tenant_context

from .helpers import NOW, activate, get_sub, update_sub

pytestmark = pytest.mark.django_db


def test_trial_created_on_registration(tenant_a):
    before = timezone.now()
    sub = get_sub(tenant_a)
    assert sub.state == "trial" and sub.plan_id is None
    assert before + timedelta(days=14) - timedelta(seconds=60) <= sub.trial_ends_at
    assert sub.trial_ends_at <= timezone.now() + timedelta(days=14)
    assert is_write_allowed(tenant_a.tenant)


def test_initial_plans_seeded(db):
    plans = {p.code: p for p in Plan.objects.all()}
    def spec(p):
        return (p.price_usd_minor, p.max_stores, p.max_users, p.extra_store_price_usd_minor)

    assert spec(plans["essentiel"]) == (500, 1, 3, 0)
    assert spec(plans["pro"]) == (1500, 3, 10, 0)
    assert spec(plans["business"]) == (4000, 10, 50, 1000)


def test_trial_to_read_only(tenant_a):
    update_sub(tenant_a, trial_ends_at=NOW)
    sub = get_sub(tenant_a)
    assert evaluate(sub, NOW - timedelta(seconds=1))["state"] == "trial"
    assert is_write_allowed(sub, NOW - timedelta(seconds=1))
    assert not is_write_allowed(sub, NOW)
    with tenant_context(tenant_a.tenant.id):
        refresh_state(Subscription.objects.get(), NOW + timedelta(days=1))
        sub = Subscription.objects.get()
    assert sub.state == "read_only" and sub.read_only_since == NOW


def test_active_past_due_read_only_with_grace(tenant_a):
    sub = activate(tenant_a, now=NOW)
    end = sub.current_period_end
    assert sub.state == "active"
    assert evaluate(sub, end - timedelta(seconds=1))["state"] == "active"
    past_due = evaluate(sub, end)
    assert past_due["state"] == "past_due" and past_due["grace_ends_at"] == end + timedelta(days=7)
    assert is_write_allowed(sub, end + timedelta(days=6))  # grâce : toutes les fonctions
    assert not is_write_allowed(sub, end + timedelta(days=7))
    with tenant_context(tenant_a.tenant.id):
        sub = refresh_state(Subscription.objects.get(), end + timedelta(days=1))
        assert sub.state == "past_due"
        sub = refresh_state(sub, end + timedelta(days=8))
    assert sub.state == "read_only" and sub.read_only_since == end + timedelta(days=7)


def test_cancel_at_period_end_goes_read_only_without_grace(tenant_a):
    sub = activate(tenant_a, now=NOW)
    with tenant_context(tenant_a.tenant.id):
        services.cancel(Subscription.objects.get(), now=NOW + timedelta(days=2))
        sub = Subscription.objects.get()
    assert sub.cancel_at_period_end and is_write_allowed(sub, sub.current_period_end - timedelta(seconds=1))
    result = evaluate(sub, sub.current_period_end)
    assert result["state"] == "read_only" and result["read_only_since"] == sub.current_period_end


def test_state_changes_are_audited_without_user(tenant_a):
    update_sub(tenant_a, trial_ends_at=NOW)
    services.tick(NOW + timedelta(hours=1))
    with tenant_context(tenant_a.tenant.id):
        entry = AuditEntry.objects.get(action="subscription.state_change")
        assert entry.user is None and entry.after["state"] == "read_only"
        assert entry.before["state"] == "trial"
    assert get_sub(tenant_a).state == "read_only"


@pytest.mark.parametrize("start,expected", [
    ((2026, 1, 31), (2026, 2, 28)),
    ((2028, 1, 31), (2028, 2, 29)),   # année bissextile
    ((2026, 12, 31), (2027, 1, 31)),  # passage d'année
    ((2026, 12, 15), (2027, 1, 15)),
    ((2026, 3, 31), (2026, 4, 30)),
])
def test_add_months(start, expected):
    from datetime import UTC, datetime

    result = add_months(datetime(*start, 10, 30, tzinfo=UTC), 1)
    assert (result.year, result.month, result.day) == expected
    assert (result.hour, result.minute) == (10, 30)


def test_tick_is_idempotent(tenant_a):
    update_sub(tenant_a, trial_ends_at=NOW)
    first = services.tick(NOW + timedelta(days=1))
    second = services.tick(NOW + timedelta(days=1))
    assert first["state_changes"] == 1 and second["state_changes"] == 0
    assert second["notices"] == 0
