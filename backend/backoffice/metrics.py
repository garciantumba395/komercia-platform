"""Indicateurs de la plateforme (KOM-083). Fonctions pures de `now` (injectable), calculées en SQL via
des agrégats sur l'alias `ops` (lecture seule, toutes entreprises).

Définitions (reprises à l'écran) :

- Entreprises actives / suspendues : `Tenant.is_active` vrai / faux.
- États d'abonnement (essai, actif, en retard, lecture seule) : état STOCKÉ de l'abonnement (mis à jour par
  `billing_tick`), pour les entreprises NON suspendues. « Abonnées » = état actif.
- Nouvelles inscriptions : entreprises créées dans les 7 / 30 derniers jours.
- MRR (USD) : somme des prix mensuels du plan des abonnements ACTIFS et EN RETARD des entreprises non suspendues.
  Les essais, les lectures seules et les abonnements sans plan ne comptent pas. En centimes USD.
- ARPU : MRR / nombre d'abonnements comptés dans le MRR (None s'il n'y en a aucun).
- Taux de conversion essai -> abonné : parmi les abonnements dont l'essai est TERMINÉ (`trial_ends_at <= now`),
  part qui a au moins un paiement CONFIRMÉ (None s'il n'y a aucun essai terminé).
- Churn mensuel : mois calendaire en cours (fuseau de l'entreprise). Base = abonnements ayant un paiement confirmé
  avant le début du mois et pas déjà perdus à cette date (ni lecture seule ni résiliés avant). Perdus = parmi la
  base, ceux passés en lecture seule (`read_only_since`) ou résiliés (`canceled_at`) depuis le début du mois.
  Limite : un abonnement réactivé dans le mois n'est plus compté (l'historique des états n'est pas conservé).
- Ventes sur 7 jours : ventes TERMINÉES (non annulées) enregistrées sur le serveur dans les 7 derniers jours,
  nombre et total PAR DEVISE (jamais mélangées).
- Taux de paiements réussis : sur les paiements d'abonnement créés dans les 30 derniers jours,
  confirmés / (confirmés + échoués + expirés) ; les paiements en attente sont ignorés.
"""

# ruff: noqa: E501

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone

from accounts.models import Tenant
from billing.models import Subscription, SubscriptionPayment
from sales.models import Sale

OPS = "ops"


def _ratio(numerator: int, denominator: int):
    if not denominator:
        return None
    return float(Decimal(numerator * 100) / Decimal(denominator))


def month_start(now):
    local = timezone.localtime(now)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def tenant_counts(now, using=OPS) -> dict:
    tenants = Tenant.objects.using(using)
    states = dict(
        Subscription.objects.using(using)
        .filter(tenant__is_active=True)
        .values_list("state")
        .annotate(n=Count("id"))
    )
    return {
        "active": tenants.filter(is_active=True).count(),
        "suspended": tenants.filter(is_active=False).count(),
        "trial": states.get(Subscription.TRIAL, 0),
        "subscribed": states.get(Subscription.ACTIVE, 0),
        "past_due": states.get(Subscription.PAST_DUE, 0),
        "read_only": states.get(Subscription.READ_ONLY, 0),
        "signups_7d": tenants.filter(created_at__gte=now - timedelta(days=7)).count(),
        "signups_30d": tenants.filter(created_at__gte=now - timedelta(days=30)).count(),
    }


def revenue(now, using=OPS) -> dict:
    agg = (
        Subscription.objects.using(using)
        .filter(tenant__is_active=True, state__in=[Subscription.ACTIVE, Subscription.PAST_DUE], plan__isnull=False)
        .aggregate(mrr=Sum("plan__price_usd_minor"), n=Count("id"))
    )
    mrr, paying = int(agg["mrr"] or 0), agg["n"]
    arpu = None
    if paying:
        arpu = int((Decimal(mrr) / Decimal(paying)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {"mrr_usd_minor": mrr, "paying_count": paying, "arpu_usd_minor": arpu}


def trial_conversion(now, using=OPS) -> dict:
    ended = Subscription.objects.using(using).filter(trial_ends_at__lte=now)
    confirmed = SubscriptionPayment.objects.using(using).filter(
        subscription_id=OuterRef("pk"), status=SubscriptionPayment.CONFIRMED
    )
    total = ended.count()
    converted = ended.filter(Exists(confirmed)).count()
    return {"ended_trials": total, "converted": converted, "rate_pct": _ratio(converted, total)}


def monthly_churn(now, using=OPS) -> dict:
    start = month_start(now)
    paid_before = SubscriptionPayment.objects.using(using).filter(
        subscription_id=OuterRef("pk"), status=SubscriptionPayment.CONFIRMED, confirmed_at__lt=start
    )
    base = (
        Subscription.objects.using(using)
        .filter(Exists(paid_before))
        .exclude(read_only_since__lt=start)
        .exclude(canceled_at__lt=start)
    )
    lost = base.filter(
        Q(read_only_since__gte=start, read_only_since__lte=now) | Q(canceled_at__gte=start, canceled_at__lte=now)
    )
    start_count, lost_count = base.count(), lost.count()
    return {"start_subscribers": start_count, "lost": lost_count, "rate_pct": _ratio(lost_count, start_count)}


def sales_last_7_days(now, using=OPS) -> list[dict]:
    rows = (
        Sale.objects.using(using)
        .filter(created_at__gte=now - timedelta(days=7), status=Sale.COMPLETED)
        .values("currency")
        .annotate(count=Count("id"), total_minor=Sum("total_minor"))
        .order_by("currency")
    )
    return [{"currency": r["currency"], "count": r["count"], "total_minor": int(r["total_minor"] or 0)} for r in rows]


def payment_success(now, using=OPS) -> dict:
    counts = dict(
        SubscriptionPayment.objects.using(using)
        .filter(
            created_at__gte=now - timedelta(days=30),
            status__in=[SubscriptionPayment.CONFIRMED, SubscriptionPayment.FAILED, SubscriptionPayment.EXPIRED],
        )
        .values_list("status")
        .annotate(n=Count("id"))
    )
    confirmed = counts.get(SubscriptionPayment.CONFIRMED, 0)
    finished = sum(counts.values())
    return {"confirmed": confirmed, "finished": finished, "rate_pct": _ratio(confirmed, finished)}


def platform_metrics(now=None, using=OPS) -> dict:
    now = now or timezone.now()
    return {
        "now": now,
        "tenants": tenant_counts(now, using),
        "revenue": revenue(now, using),
        "conversion": trial_conversion(now, using),
        "churn": monthly_churn(now, using),
        "sales_7d": sales_last_7_days(now, using),
        "payments": payment_success(now, using),
    }
