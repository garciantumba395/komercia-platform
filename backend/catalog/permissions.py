"""Rôles du catalogue (matrice des permissions : cahier des charges, section 19)."""

from core.permissions import ACCOUNTANT, CASHIER, MANAGER, OWNER, STOREKEEPER

ALL_ROLES = [OWNER, MANAGER, CASHIER, STOREKEEPER, ACCOUNTANT]
WRITE_ROLES = [OWNER, MANAGER]
# « Voir les prix d'achat et les marges » : jamais le caissier.
COST_VISIBLE_ROLES = [OWNER, MANAGER, STOREKEEPER, ACCOUNTANT]
# « Exporter les données »
EXPORT_ROLES = [OWNER, MANAGER, ACCOUNTANT]


def can_see_cost(request) -> bool:
    membership = getattr(request, "membership", None)
    return membership is not None and membership.role in COST_VISIBLE_ROLES
