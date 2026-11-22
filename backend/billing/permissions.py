from rest_framework.exceptions import APIException
from rest_framework.permissions import SAFE_METHODS, BasePermission

from . import clock, services

EXEMPT_PREFIXES = ("/api/v1/auth/", "/api/v1/billing/", "/api/v1/sync/", "/api/v1/backups/")
INACTIVE_MESSAGE = ("Votre essai ou votre abonnement est terminé. "
                    "Abonnez-vous pour continuer à enregistrer des ventes.")


class SubscriptionInactive(APIException):
    status_code = 402
    default_detail = {"detail": INACTIVE_MESSAGE, "code": "subscription_inactive"}
    default_code = "subscription_inactive"


def is_exempt_path(path: str) -> bool:
    """Chemins toujours autorisés : authentification, facturation, synchronisation (rejeu des ventes
    déjà faites hors ligne) et exports (KOM-074, KOM-079)."""
    return path.startswith(EXEMPT_PREFIXES) or "/export" in path


class SubscriptionWriteAllowed(BasePermission):
    """Refuse (402) les écritures quand l'abonnement est en lecture seule.

    À ajouter après l'authentification dans `DEFAULT_PERMISSION_CLASSES`.
    """

    def has_permission(self, request, view):
        tenant = getattr(request, "tenant", None)
        if tenant is not None:
            clock.touch()  # horloge qui ne recule jamais (au plus une écriture SQL par minute)
        if request.method in SAFE_METHODS or is_exempt_path(request.path):
            return True
        if tenant is None:  # non authentifié : les autres permissions décident
            return True
        if services.is_write_allowed(tenant):
            return True
        raise SubscriptionInactive()
