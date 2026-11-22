"""Indicateurs de la plateforme (KOM-083) : fonctions pures de `now`."""

# ruff: noqa: E501

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from accounts.models import Tenant
from backoffice import metrics
from billing.models import Plan, Subscription, SubscriptionPayment
from core.tenancy import tenant_context
from currency.models import ExchangeRate  # noqa: F401  (assure le chargement des modèles)

from .helpers import DAY, DB, agent_client, make_sale

pytestmark = DB

NOW = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)


def _sub(handle, **fields):
    with tenant_context(handle.tenant.id):
        sub = Subscription.objects.get()
        for k, v in fields.items():
            setattr(sub, k, v)
        sub.save()
        return sub


def _get_sub(handle):
    with tenant_context(handle.tenant.id):
        return Subscription.objects.get()


def _payment(handle, sub, status="confirmed", confirmed_at=None, created_at=None, plan_code="pro", suffix=""):
    with tenant_context(handle.tenant.id):
        plan = Plan.objects.get(code=plan_code)
        payment = SubscriptionPayment.objects.create(
            subscription=sub, plan=plan, amount_minor=plan.price_usd_minor, currency="USD",
            amount_usd_minor=plan.price_usd_minor, provider="sandbox", status=status, confirmed_at=confirmed_at,
        )
        if created_at:
            SubscriptionPayment.objects.filter(pk=payment.pk).update(created_at=created_at)
        return payment


def _make(n, base="+24399300"):
    from core.conftest_helpers import make_tenant

    return [make_tenant(f"Entreprise {i}", f"{base}{i:04d}") for i in range(n)]


def test_empty_platform_has_no_division_errors():
    result = metrics.platform_metrics(NOW)
    assert result["revenue"] == {"mrr_usd_minor": 0, "paying_count": 0, "arpu_usd_minor": None}
    assert result["conversion"]["rate_pct"] is None and result["churn"]["rate_pct"] is None
    assert result["payments"]["rate_pct"] is None and result["sales_7d"] == []


def test_state_counts_signups_and_suspension():
    a, b, c, d, e = _make(5)
    pro = Plan.objects.get(code="pro")
    _sub(a, state="trial")
    _sub(b, state="active", plan=pro)
    _sub(c, state="past_due", plan=pro)
    _sub(d, state="read_only")
    _sub(e, state="active", plan=pro)
    Tenant.objects.filter(pk=e.tenant.id).update(is_active=False)  # suspendue : sortie des états
    Tenant.objects.filter(pk=a.tenant.id).update(created_at=NOW - timedelta(days=3))
    Tenant.objects.filter(pk=b.tenant.id).update(created_at=NOW - timedelta(days=20))
    Tenant.objects.filter(pk__in=[c.tenant.id, d.tenant.id, e.tenant.id]).update(created_at=NOW - timedelta(days=90))
    t = metrics.tenant_counts(NOW)
    assert t["active"] == 4 and t["suspended"] == 1
    assert (t["trial"], t["subscribed"], t["past_due"], t["read_only"]) == (1, 1, 1, 1)
    assert t["signups_7d"] == 1 and t["signups_30d"] == 2


def test_mrr_arpu_exclude_trials_read_only_and_suspended():
    a, b, c, d, e = _make(5)
    pro, business = Plan.objects.get(code="pro"), Plan.objects.get(code="business")
    _sub(a, state="active", plan=pro)  # 1500
    _sub(b, state="past_due", plan=business)  # 4000
    _sub(c, state="trial", plan=pro)  # exclu
    _sub(d, state="read_only", plan=pro)  # exclu
    _sub(e, state="active", plan=pro)
    Tenant.objects.filter(pk=e.tenant.id).update(is_active=False)  # exclue
    r = metrics.revenue(NOW)
    assert r == {"mrr_usd_minor": 5500, "paying_count": 2, "arpu_usd_minor": 2750}


def test_trial_conversion_over_finished_trials():
    a, b, c, d = _make(4)
    for handle, ended in ((a, True), (b, True), (c, True), (d, False)):
        _sub(handle, trial_ends_at=NOW - DAY if ended else NOW + DAY)
    _payment(a, _get_sub(a), confirmed_at=NOW - DAY)
    _payment(b, _get_sub(b), status="failed")
    result = metrics.trial_conversion(NOW)
    assert result["ended_trials"] == 3 and result["converted"] == 1
    assert round(result["rate_pct"], 2) == 33.33


def test_monthly_churn():
    a, b, c, d = _make(4)
    month_before = datetime(2026, 5, 10, tzinfo=UTC)
    subs = {}
    for name, handle in zip("abcd", (a, b, c, d), strict=True):
        subs[name] = _sub(handle, state="active")
        _payment(handle, subs[name], confirmed_at=month_before)
    _sub(b, state="read_only", read_only_since=datetime(2026, 6, 5, tzinfo=UTC))  # perdu ce mois
    _sub(c, canceled_at=datetime(2026, 6, 8, tzinfo=UTC))  # résilié ce mois
    _sub(d, state="read_only", read_only_since=datetime(2026, 4, 1, tzinfo=UTC))  # déjà perdu avant : hors base
    result = metrics.monthly_churn(NOW)
    assert result["start_subscribers"] == 3 and result["lost"] == 2
    assert round(result["rate_pct"], 2) == 66.67


def test_sales_7_days_per_currency_without_mixing():
    a, b = _make(2)
    make_sale(a, "A-1", total=1000, currency="CDF", age=timedelta(days=1))
    make_sale(a, "A-2", total=2500, currency="CDF", age=timedelta(days=2))
    make_sale(b, "B-1", total=999, currency="USD", age=timedelta(days=1))
    make_sale(b, "B-2", total=50000, currency="CDF", age=timedelta(days=30))  # trop ancienne
    make_sale(b, "B-3", total=70000, currency="CDF", status="cancelled")  # annulée
    from django.utils import timezone

    rows = metrics.sales_last_7_days(timezone.now())
    assert rows == [{"currency": "CDF", "count": 2, "total_minor": 3500},
                    {"currency": "USD", "count": 1, "total_minor": 999}]


def test_payment_success_rate():
    (a,) = _make(1)
    sub = _get_sub(a)
    recent = NOW - 2 * DAY
    for status in ("confirmed", "confirmed", "confirmed", "failed"):
        _payment(a, sub, status=status, confirmed_at=recent if status == "confirmed" else None, created_at=recent)
    _payment(a, sub, status="pending", created_at=recent)  # ignoré
    _payment(a, sub, status="expired", created_at=NOW - 60 * DAY)  # hors fenêtre
    result = metrics.payment_success(NOW)
    assert result["confirmed"] == 3 and result["finished"] == 4 and result["rate_pct"] == 75.0


def test_dashboard_page_renders_definitions(tenant_a):
    html = agent_client().get("/ops/").content.decode()
    for label in ("MRR", "ARPU", "Churn", "Conversion", "Suspendues", "En essai", "Lecture seule"):
        assert label in html
    assert str(Decimal("0")) is not None
