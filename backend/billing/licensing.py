"""Clés de licence mensuelles (édition locale, sans serveur en ligne).

Le commerçant paie hors application, reçoit une clé signée (Ed25519) liée à CE PC, et la saisit dans
l'application. Le format est dans `license_codec` ; la clé publique est embarquée
(`license_public_key.pem`, surchargeable par le réglage ou la variable d'environnement
`KOMERSIA_LICENSE_PUBLIC_KEY_FILE`). La sécurité côté client est une dissuasion, pas une protection absolue.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit import services as audit

from . import clock, services
from . import license_codec as codec
from .models import LicenseKeyUse, LicenseState, Plan, Subscription, SubscriptionPayment

DEFAULT_PUBLIC_KEY_FILE = Path(__file__).resolve().parent / "license_public_key.pem"
DEFAULT_MAX_KEY_AGE_DAYS = 90
PROVIDER = "license"

short_code = codec.short_code


# --------------------------------------------------------------------------- réglages


def license_enabled() -> bool:
    """Licence active en édition locale, ou dès qu'aucun fournisseur de paiement en ligne n'est configuré."""
    return getattr(settings, "KOMERSIA_EDITION", None) == "local" or not services.payments_enabled()


def max_key_age() -> timedelta:
    days = getattr(settings, "KOMERSIA_LICENSE_MAX_KEY_AGE_DAYS", DEFAULT_MAX_KEY_AGE_DAYS)
    return timedelta(days=int(days))


def public_key_path() -> Path:
    configured = getattr(settings, "KOMERSIA_LICENSE_PUBLIC_KEY_FILE", None) \
        or os.environ.get("KOMERSIA_LICENSE_PUBLIC_KEY_FILE")
    return Path(configured) if configured else DEFAULT_PUBLIC_KEY_FILE


@lru_cache(maxsize=8)
def _load_public_key(path: str, mtime: float):
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    return load_pem_public_key(Path(path).read_bytes())


def public_key():
    path = public_key_path()
    try:
        return _load_public_key(str(path), path.stat().st_mtime)
    except (OSError, ValueError) as exc:
        raise services.BillingError(
            "La clé publique de licence est introuvable ou illisible : contactez le support Komersia.",
            "license_unavailable", 500) from exc


# --------------------------------------------------------------------------- identifiant d'installation


def get_license_state() -> LicenseState:
    """Ligne unique de l'installation, créée au premier besoin (identifiant UUID aléatoire)."""
    state = LicenseState.objects.filter(pk=1).first()
    if state is None:
        state, _ = LicenseState.objects.get_or_create(pk=1, defaults={"last_seen_at": timezone.now()})
    return state


def installation_info() -> dict:
    iid = get_license_state().installation_id
    return {"installation_id": str(iid), "short_code": codec.short_code(iid)}


# --------------------------------------------------------------------------- vérification


def _fail(message: str, code: str, status: int = 400):
    raise services.BillingError(message, code, status)


def verify_key(text: str, now: datetime | None = None) -> tuple[codec.LicensePayload, Plan]:
    """Vérifie une clé (somme de contrôle, signature, installation, plan, nonce, âge). Sans effet de bord."""
    now = now or clock.effective_now()
    try:
        body, signature = codec.decode_key(text)
        payload = codec.unpack_payload(body)
        codec.verify_signature(public_key(), body, signature)
    except codec.LicenseError as exc:
        _fail(exc.message, exc.code)
    if not codec.MIN_MONTHS <= payload.months <= codec.MAX_MONTHS:
        _fail("Clé invalide : durée hors limites.", "license_invalid")
    iid = get_license_state().installation_id
    if payload.installation_id != iid:
        _fail("Cette clé a été émise pour un autre ordinateur. Donnez à votre agent le code de CET "
              f"ordinateur : {codec.short_code(iid)}.", "license_wrong_installation")
    plan = Plan.objects.filter(code=payload.plan, is_active=True).first()
    if plan is None:
        _fail("Le plan de cette clé n'existe pas ou n'est plus proposé. Contactez votre agent Komersia.",
              "unknown_plan")
    if LicenseKeyUse.objects.filter(nonce=payload.nonce).exists():
        _fail("Cette clé a déjà été utilisée.", "license_already_used", 409)
    issued = datetime.fromtimestamp(payload.issued_at, UTC)
    if now - issued > max_key_age():
        _fail("Cette clé est trop ancienne (émise le "
              f"{timezone.localtime(issued):%d/%m/%Y}). Demandez une nouvelle clé à votre agent Komersia.",
              "license_too_old")
    return payload, plan


# --------------------------------------------------------------------------- activation


@transaction.atomic
def activate(sub: Subscription, key: str, *, user=None, now=None) -> Subscription:
    """Applique une clé : même logique qu'un paiement confirmé, mais prépayé hors application.

    Dans le contexte du tenant. Idempotent : une clé déjà utilisée est refusée (409).
    """
    if not license_enabled():
        _fail("Les clés de licence ne sont pas activées sur cette installation.", "license_disabled")
    now = now or clock.effective_now()
    sub = Subscription.objects.select_for_update().get(pk=sub.pk)
    payload, plan = verify_key(key, now)
    services._assert_plan_fits(plan, sub.tenant_id)  # plan inférieur : limites déjà dépassées => refus
    try:
        with transaction.atomic():
            LicenseKeyUse.objects.create(nonce=payload.nonce, tenant_id=sub.tenant_id, plan_code=plan.code,
                                         months=payload.months, used_at=now)
    except IntegrityError:
        _fail("Cette clé a déjà été utilisée.", "license_already_used", 409)
    start, end = services.extend_subscription(sub, plan, payload.months, now)
    payment = SubscriptionPayment.objects.create(
        subscription=sub, plan=plan, kind=SubscriptionPayment.KIND_SUBSCRIBE, amount_minor=0,
        currency="USD", amount_usd_minor=0, provider=PROVIDER, reference=payload.nonce,
        status=SubscriptionPayment.CONFIRMED, period_start=start, period_end=end, confirmed_at=now,
        months=payload.months)
    audit.log("subscription.license_activated", "subscription", sub.id, before=None,
              after={"nonce": payload.nonce, "plan": plan.code, "months": payload.months,
                     "period_start": start, "period_end": end, "payment_id": payment.id},
              user=user, reason="Activation par clé de licence")
    return sub
