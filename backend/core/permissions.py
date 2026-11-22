"""Permissions par rôle, appliquées côté serveur (matrice : cahier des charges, section 19)."""

from rest_framework.permissions import BasePermission

OWNER = "owner"
MANAGER = "manager"
CASHIER = "cashier"
STOREKEEPER = "storekeeper"
ACCOUNTANT = "accountant"

ROLES = [
    (OWNER, "Propriétaire"),
    (MANAGER, "Gérant"),
    (CASHIER, "Caissier"),
    (STOREKEEPER, "Magasinier"),
    (ACCOUNTANT, "Comptable"),
]


class HasRole(BasePermission):
    """Autorise selon `view.allowed_roles`, ou `view.allowed_roles_by_action[action]`
    pour les ViewSets. Une vue sans rôles déclarés est refusée (refus par défaut)."""

    def has_permission(self, request, view):
        membership = getattr(request, "membership", None)
        if membership is None:
            return False
        roles = getattr(view, "allowed_roles", None)
        by_action = getattr(view, "allowed_roles_by_action", None)
        if by_action is not None:
            roles = by_action.get(getattr(view, "action", None), roles)
        if not (bool(roles) and membership.role in roles):
            return False
        # Les vues qui déclarent HasRole remplacent les permissions par défaut : on applique ici
        # la règle de lecture seule de l'abonnement (import tardif : billing dépend de core).
        from billing.permissions import SubscriptionWriteAllowed

        return SubscriptionWriteAllowed().has_permission(request, view)
