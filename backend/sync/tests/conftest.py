import pytest


@pytest.fixture(autouse=True)
def no_pull_lag(settings):
    """Les tests lisent leurs données immédiatement : pas de délai de sécurité sur le `pull`."""
    settings.SYNC_PULL_LAG_SECONDS = 0
