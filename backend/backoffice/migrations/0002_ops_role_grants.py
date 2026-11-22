"""Droits du rôle `komersia_ops` sur les tables existantes (ignorés si le rôle n'existe pas encore).

Voir `backoffice/ops_role.py`. Si le rôle est créé APRÈS cette migration, rejouer `scripts/db-init.sql`
(il accorde les mêmes droits aux tables existantes).
"""

from django.db import migrations

from backoffice.ops_role import apply_grants


def grant(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        apply_grants(cursor)


class Migration(migrations.Migration):
    dependencies = [
        ("backoffice", "0001_initial"),
        ("accounts", "0001_initial"),
        ("audit", "0001_initial"),
        ("billing", "0002_initial_plans"),
        ("sales", "0002_sale_status_date_index"),
        ("catalog", "0001_initial"),
        ("customers", "0001_initial"),
    ]
    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
