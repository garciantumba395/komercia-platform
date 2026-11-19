"""Paramètres de l'ÉDITION LOCALE de Komersia (installateur Windows, voir docs/dev/edition-locale.md).

Tout tourne sur le PC principal du commerçant : PostgreSQL embarqué (127.0.0.1 uniquement) et serveur web
waitress. Aucun secret par défaut : les mots de passe et la clé secrète sont générés au premier lancement
par le lanceur (`desktop/komersia_desktop`) et lus dans `<dossier de données>/config/secrets.json`.

Ce module ne dépend que de la bibliothèque standard (il est importé par Django ET par les outils) ; la
résolution du dossier de données doit rester identique à `komersia_desktop/paths.py` (test de parité).
"""

import json
import os
import secrets as _secrets
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F403

KOMERSIA_EDITION = "local"
KOMERSIA_SINGLE_TENANT = True  # une seule entreprise par installation


# --- Dossiers ---
def _default_data_dir() -> Path:
    explicit = os.environ.get("KOMERSIA_DATA_DIR")
    if explicit:
        return Path(explicit)
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "Komersia"
    return Path.home() / ".local" / "share" / "komersia"


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


KOMERSIA_DATA_DIR = _default_data_dir()
KOMERSIA_CONFIG_DIR = KOMERSIA_DATA_DIR / "config"
KOMERSIA_LOG_DIR = KOMERSIA_DATA_DIR / "logs"
KOMERSIA_PG_BIN_DIR = os.environ.get("KOMERSIA_PG_BIN") or str(
    Path(os.environ.get("KOMERSIA_INSTALL_DIR", ".")) / "pgsql" / "bin"
)

_secrets_file = _load_json(KOMERSIA_CONFIG_DIR / "secrets.json")
_settings_file = _load_json(KOMERSIA_CONFIG_DIR / "settings.json")
_runtime_file = _load_json(KOMERSIA_CONFIG_DIR / "runtime.json")


def _secret(name: str) -> str:
    value = _secrets_file.get(name)
    if value:
        return str(value)
    if os.environ.get("KOMERSIA_EPHEMERAL_SECRETS") == "1":  # auto-test du paquet, jamais en production
        return _secrets.token_urlsafe(32)
    raise ImproperlyConfigured(
        f"Secret « {name} » introuvable dans {KOMERSIA_CONFIG_DIR / 'secrets.json'}. "
        "Lancez d'abord le lanceur (KomersiaServer --init) : il génère les secrets au premier lancement."
    )


# --- Sécurité ---
SECRET_KEY = _secret("secret_key")
DEBUG = False
# Accès par le nom ou l'IP du PC sur le réseau local : l'hôte demandé n'est pas connu à l'avance.
ALLOWED_HOSTS = ["*"]

# --- Applications et URL : pas de console d'exploitation /ops/ ni d'API de support en local ---
INSTALLED_APPS = [app for app in INSTALLED_APPS if app != "backoffice"]  # noqa: F405
ROOT_URLCONF = "komersia.urls_local"

# --- Base de données : PostgreSQL embarqué, 127.0.0.1 uniquement ---
# Rôle `app` (défaut) : exécution, non propriétaire, soumis à la RLS. Rôle `owner` : migrations seulement
# (le lanceur exécute les migrations dans un processus à part avec KOMERSIA_DB_ROLE=owner).
_PG_PORT = int(
    os.environ.get("KOMERSIA_DB_PORT")
    or _runtime_file.get("pg_port")
    or _settings_file.get("pg_port")
    or 54329
)
_DB_NAME = _settings_file.get("db_name") or "komersia"
_DB_ROLE = os.environ.get("KOMERSIA_DB_ROLE", "app")
if _DB_ROLE not in ("app", "owner"):
    raise ImproperlyConfigured("KOMERSIA_DB_ROLE doit valoir « app » ou « owner ».")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _DB_NAME,
        "USER": f"komersia_{_DB_ROLE}",
        "PASSWORD": _secret(f"{_DB_ROLE}_password"),
        "HOST": "127.0.0.1",
        "PORT": str(_PG_PORT),
        # Indispensable pour `SET LOCAL app.tenant_id` (RLS), comme dans settings.py.
        "ATOMIC_REQUESTS": True,
    }
}
# L'alias « ops » (rôle komersia_ops, BYPASSRLS) n'existe pas en local : supprimé ci-dessus.

# Sauvegardes (application `backups`) : le lanceur fournit les variables d'environnement DB_BACKUP_USER/PASSWORD
# (rôle komersia_backup, BYPASSRLS) et DB_RESTORE_USER/PASSWORD (komersia_owner). Aucun dossier de sauvegarde
# par défaut : il doit être choisi hors du dossier des données et de l'installation (disque ou clé USB).

# --- Fichiers statiques (servis par WhiteNoise) ---
STATIC_ROOT = KOMERSIA_DATA_DIR / "static"

# --- Paiement en ligne : désactivé (abonnement par clé de licence saisie à la main) ---
BILLING_PROVIDER = ""
