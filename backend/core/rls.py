"""Opération de migration qui active la Row-Level Security sur une table métier."""

from django.db import migrations


class EnableTenantRLS(migrations.RunSQL):
    """À ajouter dans la migration qui crée une table avec une colonne `tenant_id`."""

    def __init__(self, table: str):
        policy = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
        forward = [
            f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;',
            f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;',
            f'CREATE POLICY tenant_isolation ON "{table}" USING ({policy}) WITH CHECK ({policy});',
        ]
        backward = [
            f'DROP POLICY IF EXISTS tenant_isolation ON "{table}";',
            f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY;',
            f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;',
        ]
        super().__init__(sql=forward, reverse_sql=backward)
