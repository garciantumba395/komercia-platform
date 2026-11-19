"""Sélection du fournisseur de paiement.

TODO : brancher l'agrégateur Mobile Money réel (M-Pesa, Orange Money, Airtel Money via un
agrégateur) en ajoutant une sous-classe de PaymentProvider ici ; un seul fournisseur concret
(sandbox) existe pour l'instant.
"""

from django.conf import settings

from .base import PaymentProvider
from .sandbox import SandboxProvider

PROVIDERS: dict[str, type[PaymentProvider]] = {"sandbox": SandboxProvider}


def provider_code() -> str:
    return getattr(settings, "BILLING_PROVIDER", "sandbox")


def get_provider(code: str | None = None) -> PaymentProvider:
    code = code or provider_code()
    try:
        return PROVIDERS[code]()
    except KeyError:
        raise LookupError(f"Fournisseur de paiement inconnu : {code}") from None
