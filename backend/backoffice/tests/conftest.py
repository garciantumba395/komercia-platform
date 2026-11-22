"""Fixtures des tests du back-office.

`ops_role` (session) : après la création de la base de test, garantit l'existence du rôle `komersia_ops`
et lui accorde ses droits sur la base de test. Sans ce rôle BYPASSRLS, la console ne verrait rien.
Création du rôle : via la connexion propriétaire si elle le permet ; sinon via un super-utilisateur
(variables OPS_TEST_SUPERUSER / OPS_TEST_SUPERUSER_PASSWORD) ; sinon échec explicite (exécuter
`scripts/db-init.sql`, ce que fait la CI).
"""

# ruff: noqa: E501

import importlib
import os

import psycopg
import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from psycopg import sql

from backoffice import ops_role


def _create_role_as_superuser(role, password):
    user = os.environ.get("OPS_TEST_SUPERUSER")
    if not user:
        return False
    db = settings.DATABASES["default"]
    with psycopg.connect(
        host=db["HOST"], port=db["PORT"], dbname="postgres", user=user,
        password=os.environ.get("OPS_TEST_SUPERUSER_PASSWORD", ""), autocommit=True,
    ) as conn:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} BYPASSRLS").format(sql.Identifier(role), sql.Literal(password))
        )
    return True


@pytest.fixture(scope="session", autouse=True)
def ops_role_ready(django_db_setup, django_db_blocker):
    role = ops_role.ops_role_name()
    password = settings.DATABASES["ops"]["PASSWORD"]
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
        if cursor.fetchone() is None:
            try:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} BYPASSRLS").format(
                        sql.Identifier(role), sql.Literal(password)
                    ).as_string(cursor.connection)
                )
            except Exception:
                if not _create_role_as_superuser(role, password):
                    pytest.fail(
                        f"Le rôle PostgreSQL « {role} » n'existe pas et ne peut pas être créé par le rôle propriétaire "
                        "(BYPASSRLS exige un super-utilisateur). Exécutez scripts/db-init.sql en super-utilisateur "
                        "ou définissez OPS_TEST_SUPERUSER / OPS_TEST_SUPERUSER_PASSWORD."
                    )
        assert ops_role.apply_grants(cursor, role), "rôle ops introuvable après création"


@pytest.fixture(autouse=True)
def _console_settings(settings):
    settings.ROOT_URLCONF = "backoffice.tests.urls"
    cache.clear()  # compteurs de limitation des connexions
    yield
    cache.clear()


def _seed_plans():
    """Plans initiaux (migration billing.0002) : un nettoyage de tables (tests transactionnels) les supprime."""
    from billing.models import Plan

    for data in importlib.import_module("billing.migrations.0002_initial_plans").PLANS:
        Plan.objects.get_or_create(code=data["code"], defaults=data)


@pytest.fixture(autouse=True)
def seeded_plans(db):
    _seed_plans()


@pytest.fixture(scope="session", autouse=True)
def restore_plans_at_end(ops_role_ready, django_db_blocker):
    """Les tests transactionnels vident les tables : on remet les plans pour les exécutions suivantes (--reuse-db)."""
    yield
    with django_db_blocker.unblock():
        _seed_plans()
