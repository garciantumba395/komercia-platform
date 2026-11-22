from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from accounts.models import Tenant
from billing.models import Subscription
from core.tenancy import tenant_context

from .helpers import NOW, get_sub, update_sub

pytestmark = pytest.mark.django_db


def test_backfill_creates_trial_for_tenants_without_subscription_and_is_idempotent(tenant_a):
    legacy = Tenant.objects.create(name="Ancien commerce")  # le signal crée l'essai
    with tenant_context(legacy.id):
        Subscription.objects.all().delete()  # simule un tenant antérieur au module billing
        assert Subscription.objects.count() == 0
    call_command("billing_backfill")
    with tenant_context(legacy.id):
        sub = Subscription.objects.get()
    assert sub.state == "trial"
    assert timedelta(days=13, hours=23) < sub.trial_ends_at - timezone.now() <= timedelta(days=14)
    untouched = get_sub(tenant_a)
    call_command("billing_backfill")
    with tenant_context(legacy.id):
        assert Subscription.objects.get().id == sub.id
    assert get_sub(tenant_a).trial_ends_at == untouched.trial_ends_at


def test_tick_command(tenant_a):
    update_sub(tenant_a, trial_ends_at=NOW)
    call_command("billing_tick", now=(NOW + timedelta(days=1)).isoformat())
    assert get_sub(tenant_a).state == "read_only"
    call_command("billing_tick", now=(NOW + timedelta(days=1)).isoformat())
