"""Services du module currency, utilisés notamment par `sales` pour figer le taux sur une vente."""

from __future__ import annotations

from django.utils import timezone

from .models import ExchangeRate


class NoExchangeRate(Exception):
    """Aucun taux de change n'est défini (ni pour la boutique, ni par défaut pour l'entreprise)."""


def _latest(qs):
    return qs.filter(effective_at__lte=timezone.now()).order_by("-effective_at", "-created_at").first()


def current_rate(store_id=None) -> ExchangeRate | None:
    """Dernier taux effectif de la boutique, sinon du tenant (store nul) ; None si aucun.

    S'exécute dans le contexte de tenant actif (la RLS isole les entreprises).
    """
    if store_id is not None:
        rate = _latest(ExchangeRate.objects.filter(store_id=store_id))
        if rate is not None:
            return rate
    return _latest(ExchangeRate.objects.filter(store__isnull=True))


def snapshot(store_id=None) -> dict:
    """Valeurs à figer sur chaque vente : {"rate": Decimal, "rate_id": UUID, "effective_at": datetime}.

    Lève NoExchangeRate si aucun taux n'existe.
    """
    rate = current_rate(store_id)
    if rate is None:
        raise NoExchangeRate("Aucun taux de change défini : définissez le taux CDF/USD.")
    return {"rate": rate.rate_cdf_per_usd, "rate_id": rate.id, "effective_at": rate.effective_at}
