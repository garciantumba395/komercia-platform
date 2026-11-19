"""Paramètres Django de Komersia.

Base de données : deux rôles PostgreSQL (voir docs/adr/0001-choix-de-la-stack.md).
- `komersia_owner` : propriétaire des tables, utilisé uniquement pour les migrations.
- `komersia_app`   : rôle d'exécution, NON propriétaire, soumis à la Row-Level Security.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    # Socle
    "core",
    "accounts",
    # Modules métier
    "catalog",
    "stock",
    "customers",
    "sales",
    "reports",
    "currency",
    "audit",
    "web",
    "sync",
    "billing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.TenantContextMiddleware",
]

ROOT_URLCONF = "komersia.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "komersia.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "komersia"),
        "USER": os.environ.get("DB_USER", "komersia_app"),
        "PASSWORD": os.environ.get("DB_PASSWORD", "app"),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        # Chaque requête HTTP s'exécute dans une transaction : indispensable pour que
        # `SET LOCAL app.tenant_id` (RLS) reste valable pendant toute la requête.
        "ATOMIC_REQUESTS": True,
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
# Hachage robuste (KOM-NF-007) : Argon2 n'est pas requis, PBKDF2 (défaut Django) suffit au MVP.

LANGUAGE_CODE = "fr"
LANGUAGES = [("fr", "Français"), ("en", "English")]
TIME_ZONE = "Africa/Kinshasa"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["core.tenancy.TenantTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "billing.permissions.SubscriptionWriteAllowed",  # lecture seule si abonnement terminé
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": os.environ.get("THROTTLE_ANON", "60/min")},
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Komersia API",
    "DESCRIPTION": "API de la plateforme Komersia",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# Abonnement. Le fournisseur « sandbox » permet de confirmer ses propres paiements sans authentification :
# il n'est actif par défaut qu'en développement (DEBUG). En production, définir un fournisseur réel.
BILLING_PROVIDER = os.environ.get("BILLING_PROVIDER", "sandbox" if DEBUG else "")
BILLING_WEBHOOK_SECRET = os.environ.get("BILLING_WEBHOOK_SECRET")
