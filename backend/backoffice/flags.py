"""Fonctions activables par entreprise (KOM-084, version simple).

Usage depuis un autre module : `from backoffice.flags import is_enabled` puis
`if is_enabled(request.tenant.id, "advanced_reports"): ...`. La lecture passe par la connexion APPLICATIVE
(`default`) : la table n'est pas sous RLS (voir `models.py`). Une clé sans ligne vaut `default`.
Les écritures se font uniquement depuis la console (`services.set_flag`).
"""

from .models import FeatureFlag

# Fonctions connues de la console (clé -> libellé). Ajouter ici une clé pour la rendre activable.
KNOWN_FLAGS = {
    "advanced_reports": "Rapports avancés",
    "loyalty": "Programme de fidélité",
    "multi_store_transfers": "Transferts de stock entre boutiques",
}


def is_enabled(tenant_id, key: str, default: bool = False) -> bool:
    value = FeatureFlag.objects.filter(tenant_id=tenant_id, key=key).values_list("enabled", flat=True).first()
    return default if value is None else bool(value)
