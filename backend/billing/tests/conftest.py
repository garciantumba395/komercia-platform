import pytest


@pytest.fixture(autouse=True)
def sandbox_billing_provider(settings):
    """Les tests d'abonnement utilisent le fournisseur de test, quel que soit le réglage DEBUG.

    En production, le fournisseur « sandbox » n'est pas actif par défaut (voir komersia/settings.py).
    """
    settings.BILLING_PROVIDER = "sandbox"
    settings.BILLING_WEBHOOK_SECRET = getattr(settings, "BILLING_WEBHOOK_SECRET", None) or None


@pytest.fixture(autouse=True)
def fresh_clock_cache():
    """Le cache de l'horloge d'abonnement est par processus : on le vide avant et après chaque test."""
    from billing import clock

    clock.reset_cache()
    yield
    clock.reset_cache()
