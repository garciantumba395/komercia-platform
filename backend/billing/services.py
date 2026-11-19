"""Logique métier de l'abonnement : états, souscription, paiement, changement de plan, rappels.

Toutes les fonctions qui dépendent du temps acceptent `now` (injectable pour les tests).
Elles s'exécutent dans un contexte de tenant actif (`core.tenancy.tenant_context`) : la RLS isole
les abonnements. Seules les tables globales (`Plan`, `PaymentReference`) sont lisibles sans contexte.
"""

from __future__ import annotations

import calendar
import math
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException

from audit import services as audit
from core.tenancy import get_current_tenant_id, tenant_context
from currency.money import convert_minor
from currency.services import current_rate

from .models import (
    BillingNotice,
    PaymentReference,
    Plan,
    Subscription,
    SubscriptionPayment,
)
from .providers import get_provider, provider_code

GRACE_DAYS = 7
TRIAL_DAYS = 14
TRIAL_PLAN_CODE = "pro"  # l'essai donne accès aux fonctions (et limites) du plan Pro
PENDING_PAYMENT_TTL = timedelta(hours=24)
READ_ONLY_STATES = {Subscription.READ_ONLY}


# --------------------------------------------------------------------------- erreurs


class BillingError(APIException):
    """Erreur métier de facturation (400) : {"detail": message, "code": code}."""

    status_code = 400

    def __init__(self, detail: str, code: str = "billing_error", status_code: int | None = None):
        super().__init__({"detail": detail, "code": code})
        if status_code is not None:
            self.status_code = status_code


class PlanLimitReached(BillingError):
    status_code = 403

    def __init__(self, detail: str):
        super().__init__(detail, "plan_limit_reached")


# --------------------------------------------------------------------------- dates


def add_months(value: datetime, months: int) -> datetime:
    """Ajoute des mois calendaires en gardant le dernier jour du mois si le jour n'existe pas
    (31 janvier + 1 mois = 28 ou 29 février)."""
    index = value.month - 1 + months
    year, month = value.year + index // 12, index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _now(now):
    return now or timezone.now()


def _days_between(end: datetime, now: datetime) -> int:
    """Nombre de jours calendaires (fuseau local) entre `now` et `end`."""
    return (timezone.localtime(end).date() - timezone.localtime(now).date()).days


def days_left(end: datetime | None, now: datetime) -> int:
    if end is None or end <= now:
        return 0
    return math.ceil((end - now) / timedelta(days=1))


# --------------------------------------------------------------------------- états


def evaluate(sub: Subscription, now: datetime) -> dict:
    """Fonction pure : champs d'état après application de toutes les transitions dues à `now`."""
    state, grace, read_only_since = sub.state, sub.grace_ends_at, sub.read_only_since
    while True:
        if state == Subscription.TRIAL and sub.trial_ends_at and now >= sub.trial_ends_at:
            state, read_only_since = Subscription.READ_ONLY, sub.trial_ends_at
        elif state == Subscription.ACTIVE and sub.current_period_end and now >= sub.current_period_end:
            if sub.cancel_at_period_end:
                state, read_only_since = Subscription.READ_ONLY, sub.current_period_end
            else:
                state = Subscription.PAST_DUE
                grace = sub.current_period_end + timedelta(days=GRACE_DAYS)
        elif state == Subscription.PAST_DUE and grace and now >= grace:
            state, read_only_since = Subscription.READ_ONLY, grace
        else:
            break
    return {"state": state, "grace_ends_at": grace, "read_only_since": read_only_since}


def effective_state(sub: Subscription, now=None) -> str:
    return evaluate(sub, _now(now))["state"]


_STATE_FIELDS = ("state", "grace_ends_at", "read_only_since")


def refresh_state(sub: Subscription, now=None) -> Subscription:
    """Applique et enregistre les transitions dues. Audit `subscription.state_change` (user=None)."""
    now = _now(now)
    new = evaluate(sub, now)
    if all(getattr(sub, f) == new[f] for f in _STATE_FIELDS):
        return sub
    before = {f: getattr(sub, f) for f in _STATE_FIELDS}
    fields = list(_STATE_FIELDS)
    plan_before = sub.plan_id
    # Fin de période payée : un plan inférieur programmé s'applique maintenant.
    if (sub.state == Subscription.ACTIVE and new["state"] != Subscription.ACTIVE
            and sub.pending_plan_id and not sub.cancel_at_period_end):
        sub.plan_id, sub.pending_plan_id = sub.pending_plan_id, None
        fields += ["plan", "pending_plan"]
    for f in _STATE_FIELDS:
        setattr(sub, f, new[f])
    sub.save(update_fields=fields + ["updated_at"])
    audit.log("subscription.state_change", "subscription", sub.id, before=before,
              after={**new, "plan_id": sub.plan_id}, user=None,
              reason="Transition automatique de l'abonnement")
    if plan_before != sub.plan_id:
        audit.log("subscription.plan_change", "subscription", sub.id, before={"plan_id": plan_before},
                  after={"plan_id": sub.plan_id}, user=None,
                  reason="Application du plan inférieur en fin de période")
    return sub


def _subscription_of(tenant) -> Subscription | None:
    tenant_id = getattr(tenant, "id", tenant)
    if get_current_tenant_id() == tenant_id:
        return Subscription.objects.filter(tenant_id=tenant_id).first()
    with tenant_context(tenant_id):
        return Subscription.objects.filter(tenant_id=tenant_id).first()


def ensure_subscription(tenant_id, now=None) -> tuple[Subscription, bool]:
    """Abonnement du tenant, créé en essai de 14 jours s'il n'existe pas (idempotent).

    Dans le contexte du tenant."""
    return Subscription.objects.get_or_create(
        tenant_id=tenant_id,
        defaults={"state": Subscription.TRIAL, "trial_ends_at": _now(now) + timedelta(days=TRIAL_DAYS)},
    )


def is_write_allowed(tenant_or_subscription, now=None) -> bool:
    """Vrai en essai, actif et en retard (période de grâce) ; faux en lecture seule.

    L'état est calculé à la volée : il est juste même si `billing_tick` n'a pas encore tourné.
    Un tenant sans abonnement (non migré) n'est pas bloqué.
    """
    sub = tenant_or_subscription
    if not isinstance(sub, Subscription):
        sub = _subscription_of(sub)
        if sub is None:
            return True
    return effective_state(sub, now) != Subscription.READ_ONLY


def is_write_allowed_at(tenant_or_subscription, when: datetime, now=None) -> bool:
    """Une opération faite hors ligne à `when` (device_time) est-elle acceptable à la synchronisation ?

    Vrai tant que l'abonnement n'est pas en lecture seule ; en lecture seule, seules les opérations
    antérieures à `read_only_since` sont acceptées (KOM-074 : rejouer les ventes déjà faites).
    """
    sub = tenant_or_subscription
    if not isinstance(sub, Subscription):
        sub = _subscription_of(sub)
        if sub is None:
            return True
    since = evaluate(sub, _now(now))["read_only_since"]
    if since is None or evaluate(sub, _now(now))["state"] != Subscription.READ_ONLY:
        return True
    return when < since


# --------------------------------------------------------------------------- plans et limites


def plan_for(sub: Subscription | None) -> Plan | None:
    """Plan dont les fonctions et limites s'appliquent (plan « pro » pendant l'essai)."""
    if sub is not None and sub.plan_id:
        return sub.plan
    return Plan.objects.filter(code=TRIAL_PLAN_CODE).first()


def usage(tenant_id) -> dict:
    from accounts.models import Membership, Store

    return {
        "stores": Store.objects.filter(tenant_id=tenant_id, is_active=True).count(),
        "users": Membership.objects.filter(tenant_id=tenant_id, is_active=True).count(),
    }


_RESOURCE_LABELS = {"stores": ("boutique", "boutiques"), "users": ("utilisateur", "utilisateurs")}


def check_limit(tenant, resource: str) -> None:
    """Lève PlanLimitReached (403) si la création d'une boutique ou d'un utilisateur dépasserait le plan.

    À appeler AVANT la création, dans le contexte du tenant. `resource` : "stores" ou "users".
    """
    if resource not in _RESOURCE_LABELS:
        raise ValueError(f"Ressource inconnue : {resource!r}")
    tenant_id = getattr(tenant, "id", tenant)
    plan = plan_for(_subscription_of(tenant_id))
    if plan is None:
        return
    limit = getattr(plan, f"max_{resource}")
    used = usage(tenant_id)[resource]
    if used >= limit:
        singular, plural = _RESOURCE_LABELS[resource]
        noun = singular if limit == 1 else plural
        raise PlanLimitReached(
            f"Votre plan {plan.name} est limité à {limit} {noun}. "
            "Passez à un plan supérieur pour en ajouter."
        )


def _assert_plan_fits(plan: Plan, tenant_id) -> None:
    used = usage(tenant_id)
    for resource, (_singular, plural) in _RESOURCE_LABELS.items():
        limit = getattr(plan, f"max_{resource}")
        if used[resource] > limit:
            raise BillingError(
                f"Le plan {plan.name} est limité à {limit} {plural} : vous en utilisez "
                f"actuellement {used[resource]}. Réduisez votre usage avant de choisir ce plan.",
                "limits_exceeded",
            )


# --------------------------------------------------------------------------- paiements


def _active_plan(code: str) -> Plan:
    plan = Plan.objects.filter(code=code, is_active=True).first()
    if plan is None:
        raise BillingError("Ce plan n'existe pas ou n'est plus proposé.", "unknown_plan")
    return plan


def price_in_currency(usd_minor: int, currency: str):
    """(montant dans la devise, équivalent USD, taux figé ou None). Erreur 400 sans taux (CDF)."""
    if currency == "USD":
        return usd_minor, usd_minor, None
    if currency != "CDF":
        raise BillingError("Devise de paiement invalide (USD ou CDF).", "invalid_currency")
    rate = current_rate()
    if rate is None:
        raise BillingError(
            "Aucun taux de change défini : définissez le taux CDF/USD avant de payer en CDF, "
            "ou payez en USD.", "no_exchange_rate")
    amount = convert_minor(usd_minor, "USD", "CDF", rate.rate_cdf_per_usd).amount_minor
    return amount, usd_minor, rate.rate_cdf_per_usd


def _create_payment(sub, plan, usd_minor, currency, phone, kind):
    amount, usd, rate = price_in_currency(usd_minor, currency)
    payment = SubscriptionPayment.objects.create(
        subscription=sub, plan=plan, kind=kind, amount_minor=amount, currency=currency,
        amount_usd_minor=usd, rate_cdf_per_usd=rate, provider=provider_code(), phone=phone,
    )
    PaymentReference.objects.create(reference=payment.reference, tenant_id=payment.tenant_id)
    info = get_provider(payment.provider).initiate(payment)
    instructions = info.get("instructions") or (
        f"Validez le paiement Mobile Money envoyé au {phone}. Référence : {payment.reference}.")
    return payment, instructions


@transaction.atomic
def subscribe(sub: Subscription, *, plan_code: str, currency: str, phone: str, now=None):
    """Première souscription, renouvellement ou changement de plan : crée un paiement `pending`."""
    plan = _active_plan(plan_code)
    _assert_plan_fits(plan, sub.tenant_id)
    return _create_payment(sub, plan, plan.price_usd_minor, currency, phone,
                           SubscriptionPayment.KIND_SUBSCRIBE)


def prorata_usd_minor(current_price: int, new_price: int, start: datetime, end: datetime,
                      now: datetime) -> int:
    """Complément dû pour passer à un plan plus cher : écart de prix x part restante de la période."""
    one = timedelta(microseconds=1)
    total = (end - start) // one
    remaining = max(0, min(total, (end - now) // one))
    if total <= 0:
        return 0
    value = Decimal(new_price - current_price) * Decimal(remaining) / Decimal(total)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@transaction.atomic
def change_plan(sub: Subscription, *, plan_code: str, currency: str, phone: str, user=None, now=None):
    """Retourne ("upgrade", payment, instructions), ("downgrade", None, None) ou ("unchanged", ...)."""
    now = _now(now)
    sub = Subscription.objects.select_for_update().get(pk=sub.pk)
    refresh_state(sub, now)
    if sub.state != Subscription.ACTIVE or sub.plan_id is None:
        raise BillingError("Souscrivez ou payez votre abonnement avant de changer de plan.",
                           "no_active_subscription")
    new = _active_plan(plan_code)
    if new.id == sub.plan_id:
        if sub.pending_plan_id:
            sub.pending_plan = None
            sub.save(update_fields=["pending_plan", "updated_at"])
            return "unchanged", None, None
        raise BillingError("Vous êtes déjà sur ce plan.", "same_plan")
    current = sub.plan
    if new.price_usd_minor > current.price_usd_minor:
        due = prorata_usd_minor(current.price_usd_minor, new.price_usd_minor,
                                sub.current_period_start, sub.current_period_end, now)
        if due <= 0:
            _apply_plan(sub, new, user)
            return "unchanged", None, None
        payment, instructions = _create_payment(sub, new, due, currency, phone,
                                                SubscriptionPayment.KIND_UPGRADE)
        return "upgrade", payment, instructions
    _assert_plan_fits(new, sub.tenant_id)
    before = {"plan_id": sub.plan_id, "pending_plan_id": sub.pending_plan_id}
    sub.pending_plan = new
    sub.save(update_fields=["pending_plan", "updated_at"])
    audit.log("subscription.plan_change", "subscription", sub.id, before=before,
              after={"plan_id": sub.plan_id, "pending_plan_id": new.id,
                     "effective_at": sub.current_period_end}, user=user,
              reason="Plan inférieur programmé en fin de période")
    return "downgrade", None, None


def preview_change_plan(sub: Subscription, *, plan_code: str, currency: str = "USD", now=None) -> dict:
    """Aperçu (sans rien créer) d'un changement de plan : complément à payer ou date d'effet.

    Mêmes règles que `change_plan` ; permet d'afficher le montant AVANT de payer.
    """
    now = _now(now)
    sub = refresh_state(Subscription.objects.get(pk=sub.pk), now)
    if sub.state != Subscription.ACTIVE or sub.plan_id is None:
        raise BillingError("Souscrivez ou payez votre abonnement avant de changer de plan.",
                           "no_active_subscription")
    new = _active_plan(plan_code)
    if new.id == sub.plan_id:
        return {"kind": "same_plan", "plan": new.code}
    current = sub.plan
    if new.price_usd_minor > current.price_usd_minor:
        due = prorata_usd_minor(current.price_usd_minor, new.price_usd_minor,
                                sub.current_period_start, sub.current_period_end, now)
        if due <= 0:
            return {"kind": "upgrade", "plan": new.code, "amount_usd_minor": 0, "amount_minor": 0,
                    "currency": currency, "rate_cdf_per_usd": None}
        amount, usd, rate = price_in_currency(due, currency)
        return {"kind": "upgrade", "plan": new.code, "amount_usd_minor": usd,
                "amount_minor": amount, "currency": currency,
                "rate_cdf_per_usd": None if rate is None else f"{rate:.4f}"}
    _assert_plan_fits(new, sub.tenant_id)
    return {"kind": "downgrade", "plan": new.code, "effective_at": sub.current_period_end}


def _apply_plan(sub: Subscription, plan: Plan, user, reason="Passage au plan supérieur"):
    before = {"plan_id": sub.plan_id}
    sub.plan, sub.pending_plan = plan, None
    sub.save(update_fields=["plan", "pending_plan", "updated_at"])
    audit.log("subscription.plan_change", "subscription", sub.id, before=before,
              after={"plan_id": plan.id}, user=user, reason=reason)


@transaction.atomic
def confirm_payment(reference: str, now=None) -> tuple[SubscriptionPayment, bool]:
    """Confirme un paiement (idempotent). Retourne (paiement, a_change).

    Verrouille l'abonnement puis le paiement : deux confirmations concurrentes ou rejouées ne
    prolongent qu'une fois. Doit être appelée dans le contexte du tenant du paiement.
    """
    now = _now(now)
    probe = SubscriptionPayment.objects.get(reference=reference)
    sub = Subscription.objects.select_for_update().get(pk=probe.subscription_id)
    payment = SubscriptionPayment.objects.select_for_update().get(pk=probe.pk)
    if payment.status in (SubscriptionPayment.CONFIRMED, SubscriptionPayment.FAILED):
        return payment, False

    first = sub.plan_id is None
    old_plan_id = sub.plan_id
    if payment.kind == SubscriptionPayment.KIND_UPGRADE:
        _apply_plan(sub, payment.plan, None)
        start, end = sub.current_period_start, sub.current_period_end
    else:
        start = now
        if sub.state == Subscription.ACTIVE and sub.current_period_end and sub.current_period_end > now:
            start = sub.current_period_end  # paiement anticipé : aucun jour perdu
        end = add_months(start, 1)
        before = {f: getattr(sub, f) for f in (*_STATE_FIELDS, "current_period_end")}
        sub.state = Subscription.ACTIVE
        sub.plan, sub.pending_plan = payment.plan, None
        sub.current_period_start, sub.current_period_end = start, end
        sub.grace_ends_at = sub.read_only_since = None
        sub.cancel_at_period_end, sub.canceled_at = False, None
        sub.save()
        if first:
            audit.log("subscription.start", "subscription", sub.id, before=before,
                      after={"plan_id": payment.plan_id, "current_period_end": end}, user=None,
                      reason="Première souscription")
        elif old_plan_id != payment.plan_id:
            audit.log("subscription.plan_change", "subscription", sub.id,
                      before={"plan_id": old_plan_id}, after={"plan_id": payment.plan_id},
                      user=None, reason="Changement de plan au renouvellement")
    payment.status = SubscriptionPayment.CONFIRMED
    payment.confirmed_at, payment.period_start, payment.period_end = now, start, end
    payment.save(update_fields=["status", "confirmed_at", "period_start", "period_end", "updated_at"])
    audit.log("subscription.payment_confirmed", "subscription_payment", payment.id, before=None,
              after={"reference": payment.reference, "amount_minor": payment.amount_minor,
                     "currency": payment.currency, "period_end": end}, user=None)
    return payment, True


@transaction.atomic
def fail_payment(reference: str) -> tuple[SubscriptionPayment, bool]:
    payment = SubscriptionPayment.objects.select_for_update().get(reference=reference)
    if payment.status != SubscriptionPayment.PENDING:
        return payment, False
    payment.status = SubscriptionPayment.FAILED
    payment.save(update_fields=["status", "updated_at"])
    return payment, True


def apply_provider_result(reference: str, status: str, now=None):
    """Point d'entrée commun du webhook et du sandbox : retrouve le tenant par `PaymentReference`
    (table sans RLS), active son contexte, puis confirme ou fait échouer le paiement.

    Retourne le paiement, ou None si la référence est inconnue. Idempotent.
    """
    ref = PaymentReference.objects.filter(reference=reference).first()
    if ref is None:
        return None
    with tenant_context(ref.tenant_id):
        if status == "confirmed":
            payment, _ = confirm_payment(reference, now)
        else:
            payment, _ = fail_payment(reference)
        return payment


# --------------------------------------------------------------------------- résiliation


@transaction.atomic
def cancel(sub: Subscription, *, user=None, now=None) -> Subscription:
    now = _now(now)
    sub = Subscription.objects.select_for_update().get(pk=sub.pk)
    refresh_state(sub, now)
    if sub.state not in (Subscription.ACTIVE, Subscription.PAST_DUE):
        raise BillingError("Aucun abonnement payant en cours à résilier.", "nothing_to_cancel")
    if not sub.cancel_at_period_end:
        sub.cancel_at_period_end, sub.canceled_at = True, now
        sub.save(update_fields=["cancel_at_period_end", "canceled_at", "updated_at"])
        audit.log("subscription.cancel", "subscription", sub.id, before={"cancel_at_period_end": False},
                  after={"cancel_at_period_end": True, "effective_at": sub.current_period_end},
                  user=user, reason="Résiliation en fin de période")
    return sub


@transaction.atomic
def resume(sub: Subscription, *, user=None, now=None) -> Subscription:
    now = _now(now)
    sub = Subscription.objects.select_for_update().get(pk=sub.pk)
    refresh_state(sub, now)
    if not (sub.cancel_at_period_end and sub.state == Subscription.ACTIVE
            and sub.current_period_end and sub.current_period_end > now):
        raise BillingError("Aucune résiliation à annuler : la période payée est terminée "
                           "ou l'abonnement n'est pas résilié.", "cannot_resume")
    sub.cancel_at_period_end, sub.canceled_at = False, None
    sub.save(update_fields=["cancel_at_period_end", "canceled_at", "updated_at"])
    audit.log("subscription.cancel", "subscription", sub.id, before={"cancel_at_period_end": True},
              after={"cancel_at_period_end": False}, user=user, reason="Reprise de l'abonnement")
    return sub


# --------------------------------------------------------------------------- résumé et rappels


def next_action(sub: Subscription, now: datetime) -> str | None:
    state = effective_state(sub, now)
    if state == Subscription.TRIAL:
        return "subscribe"
    if state == Subscription.READ_ONLY:
        return "pay" if sub.plan_id else "subscribe"
    if state == Subscription.PAST_DUE:
        return "pay"
    if not sub.cancel_at_period_end and days_left(sub.current_period_end, now) <= 7:
        return "pay"
    return None


def summary(sub: Subscription, now=None) -> dict:
    """Représentation de l'abonnement pour l'API (état calculé à `now`, sans écriture)."""
    now = _now(now)
    new = evaluate(sub, now)
    state = new["state"]
    end = {Subscription.TRIAL: sub.trial_ends_at, Subscription.ACTIVE: sub.current_period_end,
           Subscription.PAST_DUE: new["grace_ends_at"]}.get(state)
    plan = plan_for(sub)
    used = usage(sub.tenant_id)
    return {
        "state": state,
        "plan": sub.plan.code if sub.plan_id else None,
        "pending_plan": sub.pending_plan.code if sub.pending_plan_id else None,
        "trial_ends_at": sub.trial_ends_at,
        "current_period_end": sub.current_period_end,
        "grace_ends_at": new["grace_ends_at"],
        "read_only_since": new["read_only_since"],
        "cancel_at_period_end": sub.cancel_at_period_end,
        "days_left": days_left(end, now),
        "is_write_allowed": state != Subscription.READ_ONLY,
        "limits": {
            "max_stores": plan.max_stores if plan else None,
            "max_users": plan.max_users if plan else None,
            "stores_used": used["stores"],
            "users_used": used["users"],
        },
        "next_action": next_action(sub, now),
    }


def notice_kinds(sub: Subscription, now: datetime):
    """[(type, échéance)] des rappels dus à `now` (fonction pure, hors rappels d'état)."""
    state = sub.state
    if state == Subscription.TRIAL and sub.trial_ends_at and now < sub.trial_ends_at:
        d = _days_between(sub.trial_ends_at, now)
        kind = ("trial_0" if d <= 0 else "trial_1" if d == 1 else "trial_3" if d <= 3
                else "trial_7" if d <= 7 else None)
        return [(kind, sub.trial_ends_at)] if kind else []
    if (state == Subscription.ACTIVE and not sub.cancel_at_period_end and sub.current_period_end
            and now < sub.current_period_end):
        d = _days_between(sub.current_period_end, now)
        kind = "due_0" if d <= 0 else "due_3" if d <= 3 else "due_7" if d <= 7 else None
        return [(kind, sub.current_period_end)] if kind else []
    if state == Subscription.PAST_DUE:
        return [("past_due", sub.current_period_end)]
    if state == Subscription.READ_ONLY and sub.read_only_since:
        return [("read_only", sub.read_only_since)]
    return []


def generate_notices(sub: Subscription, now=None) -> list[BillingNotice]:
    now = _now(now)
    created = []
    for kind, due_at in notice_kinds(sub, now):
        notice, was_created = BillingNotice.objects.get_or_create(
            subscription=sub, kind=kind, due_at=due_at, defaults={"tenant_id": sub.tenant_id})
        if was_created:
            created.append(notice)
    return created


def tick(now=None) -> dict:
    """Applique les transitions et génère les rappels pour tous les abonnements (idempotent)."""
    from accounts.models import Tenant

    now = _now(now)
    stats = {"subscriptions": 0, "state_changes": 0, "notices": 0, "expired_payments": 0}
    for tenant_id in list(Tenant.objects.filter(is_active=True).values_list("id", flat=True)):
        with tenant_context(tenant_id):
            sub = Subscription.objects.select_for_update().filter(tenant_id=tenant_id).first()
            if sub is None:
                continue
            stats["subscriptions"] += 1
            old_state = sub.state
            refresh_state(sub, now)
            stats["state_changes"] += sub.state != old_state
            stats["notices"] += len(generate_notices(sub, now))
            stats["expired_payments"] += (
                SubscriptionPayment.objects
                .filter(subscription=sub, status=SubscriptionPayment.PENDING,
                        created_at__lt=now - PENDING_PAYMENT_TTL)
                .update(status=SubscriptionPayment.EXPIRED, updated_at=now)
            )
    return stats
