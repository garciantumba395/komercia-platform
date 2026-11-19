from datetime import timedelta
from decimal import Decimal

import pytest

from audit.models import AuditEntry
from billing import services
from billing.models import AppendOnlyError, PaymentReference, Subscription, SubscriptionPayment
from billing.services import BillingError
from core.tenancy import tenant_context

from .helpers import NOW, activate, add_stores, confirm, get_sub, make_payment, set_rate, update_sub

pytestmark = pytest.mark.django_db


def test_first_subscription_usd(tenant_a):
    payment = make_payment(tenant_a, "pro", "USD")
    assert (payment.amount_minor, payment.currency, payment.amount_usd_minor) == (1500, "USD", 1500)
    assert payment.rate_cdf_per_usd is None and payment.status == "pending"
    assert PaymentReference.objects.get(reference=payment.reference).tenant_id == tenant_a.tenant.id
    assert get_sub(tenant_a).state == "trial"  # rien ne change avant la confirmation
    confirm(tenant_a, payment, NOW)
    sub = get_sub(tenant_a)
    assert sub.state == "active" and sub.plan.code == "pro"
    assert sub.current_period_start == NOW
    assert sub.current_period_end == NOW.replace(month=4)


def test_first_subscription_cdf_converts_and_freezes_rate(tenant_a):
    set_rate(tenant_a, "2750.5")
    payment = make_payment(tenant_a, "pro", "CDF")
    assert payment.amount_minor == 41258  # 15 USD x 2750,5 = 41257,5 -> arrondi ROUND_HALF_UP
    assert (payment.currency, payment.amount_usd_minor) == ("CDF", 1500)
    assert payment.rate_cdf_per_usd == Decimal("2750.5")
    set_rate(tenant_a, "3000")  # un nouveau taux ne change pas le paiement existant
    with tenant_context(tenant_a.tenant.id):
        assert SubscriptionPayment.objects.get().rate_cdf_per_usd == Decimal("2750.5")


def test_cdf_without_rate_is_a_clear_400(tenant_a):
    with pytest.raises(BillingError) as exc:
        make_payment(tenant_a, "pro", "CDF")
    assert exc.value.status_code == 400 and exc.value.detail["code"] == "no_exchange_rate"
    r = tenant_a.client().post("/api/v1/billing/subscribe/",
                               {"plan": "pro", "currency": "CDF", "phone": "+243990000001"}, format="json")
    assert r.status_code == 400 and "taux" in r.json()["detail"]


def test_confirmation_is_idempotent(tenant_a):
    payment = make_payment(tenant_a)
    confirm(tenant_a, payment, NOW)
    end = get_sub(tenant_a).current_period_end
    _, changed = confirm(tenant_a, payment, NOW + timedelta(days=1))
    assert changed is False and get_sub(tenant_a).current_period_end == end
    with tenant_context(tenant_a.tenant.id):
        assert AuditEntry.objects.filter(action="subscription.payment_confirmed").count() == 1
        assert AuditEntry.objects.filter(action="subscription.start").count() == 1


def test_early_payment_extends_from_due_date(tenant_a):
    sub = activate(tenant_a, now=NOW)
    due = sub.current_period_end
    second = make_payment(tenant_a, "pro")
    confirm(tenant_a, second, NOW + timedelta(days=5))  # payé 5 jours trop tôt
    sub = get_sub(tenant_a)
    assert sub.current_period_start == due
    assert sub.current_period_end == due.replace(month=5)  # aucun jour perdu
    assert sub.state == "active"


def test_late_payment_restarts_from_now_and_clears_delay(tenant_a):
    sub = activate(tenant_a, now=NOW)
    late = sub.current_period_end + timedelta(days=3)
    services.tick(late)
    assert get_sub(tenant_a).state == "past_due"
    confirm(tenant_a, make_payment(tenant_a), late)
    sub = get_sub(tenant_a)
    assert sub.state == "active" and sub.grace_ends_at is None and sub.read_only_since is None
    assert sub.current_period_start == late


def test_payment_after_read_only_reactivates(tenant_a):
    update_sub(tenant_a, trial_ends_at=NOW)
    services.tick(NOW + timedelta(days=2))
    assert get_sub(tenant_a).state == "read_only"
    confirm(tenant_a, make_payment(tenant_a, "essentiel"), NOW + timedelta(days=3))
    sub = get_sub(tenant_a)
    assert sub.state == "active" and sub.read_only_since is None and sub.plan.code == "essentiel"


def test_failed_payment_changes_nothing(tenant_a):
    payment = make_payment(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        services.fail_payment(payment.reference)
        assert SubscriptionPayment.objects.get().status == "failed"
        _, changed = services.confirm_payment(payment.reference, NOW)  # un échec est définitif
        assert changed is False
    assert get_sub(tenant_a).state == "trial"


def test_payments_are_append_only(tenant_a):
    payment = make_payment(tenant_a)
    with tenant_context(tenant_a.tenant.id):
        p = SubscriptionPayment.objects.get()
        p.amount_minor = 1
        with pytest.raises(AppendOnlyError):
            p.save()
        with pytest.raises(AppendOnlyError):
            p.save(update_fields=["amount_minor"])
        with pytest.raises(AppendOnlyError):
            p.delete()
        with pytest.raises(AppendOnlyError):
            SubscriptionPayment.objects.all().delete()
    assert payment.reference


def test_pending_payments_expire_on_tick(tenant_a):
    make_payment(tenant_a)
    services.tick(NOW)  # création = maintenant réel : pas encore expiré
    from django.utils import timezone
    services.tick(timezone.now() + timedelta(days=2))
    with tenant_context(tenant_a.tenant.id):
        assert SubscriptionPayment.objects.get().status == "expired"


# --- changement de plan ---------------------------------------------------------------------


def test_upgrade_prorata_exact(tenant_a):
    sub = activate(tenant_a, now=NOW, plan="essentiel")
    start, end = sub.current_period_start, sub.current_period_end  # 10 mars -> 10 avril : 31 jours
    now = start + timedelta(days=10)  # 21 jours restants sur 31
    with tenant_context(tenant_a.tenant.id):
        kind, payment, _ = services.change_plan(
            Subscription.objects.get(), plan_code="pro", currency="USD", phone="", now=now)
    assert kind == "upgrade"
    # écart 1500 - 500 = 1000 centimes x 21 / 31 = 677,41... -> 677
    assert payment.amount_minor == 677 and payment.plan.code == "pro"
    assert get_sub(tenant_a).plan.code == "essentiel"  # pas d'effet avant paiement
    confirm(tenant_a, payment, now)
    sub = get_sub(tenant_a)
    assert sub.plan.code == "pro" and sub.current_period_end == end  # période inchangée


def test_upgrade_prorata_half_period_and_cdf(tenant_a):
    set_rate(tenant_a, "2800")
    sub = activate(tenant_a, now=NOW.replace(month=4), plan="pro")  # 10 avril -> 10 mai : 30 jours
    now = sub.current_period_start + timedelta(days=15)
    with tenant_context(tenant_a.tenant.id):
        _, payment, _ = services.change_plan(
            Subscription.objects.get(), plan_code="business", currency="CDF", phone="", now=now)
    # (4000 - 1500) x 15 / 30 = 1250 centimes = 12,50 USD = 35 000 CDF
    assert (payment.amount_usd_minor, payment.amount_minor, payment.currency) == (1250, 35000, "CDF")


def test_prorata_function_rounds_half_up():
    from datetime import UTC, datetime

    start = datetime(2026, 4, 1, tzinfo=UTC)
    end = start + timedelta(days=4)
    # 1 centime d'écart x 2 jours / 4 jours = 0,5 -> 1
    assert services.prorata_usd_minor(100, 101, start, end, start + timedelta(days=2)) == 1
    assert services.prorata_usd_minor(100, 101, start, end, end) == 0


def test_downgrade_is_deferred_to_period_end(tenant_a):
    sub = activate(tenant_a, now=NOW, plan="pro")
    now = NOW + timedelta(days=3)
    with tenant_context(tenant_a.tenant.id):
        kind, payment, _ = services.change_plan(
            Subscription.objects.get(), plan_code="essentiel", currency="USD", phone="", now=now,
            user=tenant_a.user)
    assert kind == "downgrade" and payment is None
    sub = get_sub(tenant_a)
    assert sub.plan.code == "pro" and sub.pending_plan.code == "essentiel"
    services.tick(sub.current_period_end + timedelta(minutes=1))
    sub = get_sub(tenant_a)
    assert sub.plan.code == "essentiel" and sub.pending_plan is None
    with tenant_context(tenant_a.tenant.id):
        assert AuditEntry.objects.filter(action="subscription.plan_change").count() == 2


def test_downgrade_refused_when_limits_exceeded(tenant_a):
    activate(tenant_a, now=NOW, plan="pro")
    add_stores(tenant_a, 1)  # 2 boutiques > 1 (essentiel)
    with tenant_context(tenant_a.tenant.id):
        with pytest.raises(BillingError) as exc:
            services.change_plan(Subscription.objects.get(), plan_code="essentiel", currency="USD",
                                 phone="", now=NOW)
    assert exc.value.detail["code"] == "limits_exceeded"
    assert "limité à 1" in exc.value.detail["detail"]
    assert get_sub(tenant_a).pending_plan is None


def test_subscribe_refused_when_usage_exceeds_plan(tenant_a):
    add_stores(tenant_a, 1)
    with pytest.raises(BillingError) as exc:
        make_payment(tenant_a, "essentiel")
    assert exc.value.detail["code"] == "limits_exceeded"


def test_change_plan_requires_active_subscription(tenant_a):
    with tenant_context(tenant_a.tenant.id):
        with pytest.raises(BillingError) as exc:
            services.change_plan(Subscription.objects.get(), plan_code="pro", currency="USD",
                                 phone="", now=NOW)
    assert exc.value.detail["code"] == "no_active_subscription"


# --- résiliation / reprise ------------------------------------------------------------------


def test_cancel_then_resume(tenant_a):
    sub = activate(tenant_a, now=NOW)
    with tenant_context(tenant_a.tenant.id):
        sub = services.cancel(Subscription.objects.get(), user=tenant_a.user, now=NOW + timedelta(days=1))
        assert sub.cancel_at_period_end and sub.canceled_at == NOW + timedelta(days=1)
        assert services.is_write_allowed(sub, NOW + timedelta(days=20))
        sub = services.resume(sub, user=tenant_a.user, now=NOW + timedelta(days=2))
        assert not sub.cancel_at_period_end and sub.canceled_at is None
        assert AuditEntry.objects.filter(action="subscription.cancel").count() == 2
    assert evaluate_state(get_sub(tenant_a), sub.current_period_end) == "past_due"


def evaluate_state(sub, now):
    return services.effective_state(sub, now)


def test_resume_refused_after_period_end(tenant_a):
    sub = activate(tenant_a, now=NOW)
    with tenant_context(tenant_a.tenant.id):
        services.cancel(Subscription.objects.get(), now=NOW + timedelta(days=1))
        with pytest.raises(BillingError) as exc:
            services.resume(Subscription.objects.get(), now=sub.current_period_end + timedelta(days=1))
    assert exc.value.detail["code"] == "cannot_resume"


def test_cancel_in_trial_refused(tenant_a):
    with tenant_context(tenant_a.tenant.id):
        with pytest.raises(BillingError):
            services.cancel(Subscription.objects.get(), now=NOW)


def test_payment_after_cancel_clears_cancellation(tenant_a):
    activate(tenant_a, now=NOW)
    with tenant_context(tenant_a.tenant.id):
        services.cancel(Subscription.objects.get(), now=NOW + timedelta(days=1))
    confirm(tenant_a, make_payment(tenant_a), NOW + timedelta(days=2))
    assert get_sub(tenant_a).cancel_at_period_end is False


# --- limites -------------------------------------------------------------------------------


def test_check_limit(tenant_a):
    from billing.services import PlanLimitReached, check_limit

    with tenant_context(tenant_a.tenant.id):
        check_limit(tenant_a.tenant, "stores")  # essai = plan Pro (3 boutiques) : 1 utilisée
    activate(tenant_a, now=NOW, plan="essentiel")
    with tenant_context(tenant_a.tenant.id):
        with pytest.raises(PlanLimitReached) as exc:
            check_limit(tenant_a.tenant, "stores")
        assert exc.value.status_code == 403 and exc.value.detail["code"] == "plan_limit_reached"
        check_limit(tenant_a.tenant, "users")  # 1 utilisateur sur 3
        with pytest.raises(ValueError):
            check_limit(tenant_a.tenant, "widgets")



def test_preview_matches_actual_change_plan(tenant_a):
    set_rate(tenant_a, "2800")
    sub = activate(tenant_a, now=NOW.replace(month=4), plan="pro")
    now = sub.current_period_start + timedelta(days=15)
    with tenant_context(tenant_a.tenant.id):
        current = Subscription.objects.get()
        preview = services.preview_change_plan(current, plan_code="business", currency="CDF", now=now)
        _, payment, _ = services.change_plan(current, plan_code="business", currency="CDF",
                                             phone="", now=now)
    assert preview["kind"] == "upgrade"
    assert (preview["amount_usd_minor"], preview["amount_minor"]) == (1250, 35000)
    assert preview["amount_minor"] == payment.amount_minor and preview["rate_cdf_per_usd"] == "2800.0000"


def test_preview_downgrade_and_same_plan_create_nothing(tenant_a):
    from billing.models import SubscriptionPayment

    sub = activate(tenant_a, now=NOW, plan="pro")
    with tenant_context(tenant_a.tenant.id):
        current = Subscription.objects.get()
        down = services.preview_change_plan(current, plan_code="essentiel", now=NOW)
        same = services.preview_change_plan(current, plan_code="pro", now=NOW)
        assert SubscriptionPayment.objects.count() == 1  # seul le paiement d'activation
    assert down["kind"] == "downgrade" and down["effective_at"] == sub.current_period_end
    assert same["kind"] == "same_plan"
    assert get_sub(tenant_a).pending_plan is None  # l'aperçu ne programme rien
