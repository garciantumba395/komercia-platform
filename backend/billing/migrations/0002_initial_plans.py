from django.db import migrations

# Hypothèses à valider avec les commerçants pilotes (cahier des charges, section 16.2).
# Faciles à changer : éditer les plans dans l'admin Django.
PLANS = [
    dict(code="essentiel", name="Essentiel", price_usd_minor=500, extra_store_price_usd_minor=0,
         max_stores=1, max_users=3, sort_order=1),
    dict(code="pro", name="Pro", price_usd_minor=1500, extra_store_price_usd_minor=0,
         max_stores=3, max_users=10, sort_order=2),
    dict(code="business", name="Business", price_usd_minor=4000, extra_store_price_usd_minor=1000,
         max_stores=10, max_users=50, sort_order=3),
]


def create_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    for data in PLANS:
        Plan.objects.get_or_create(code=data["code"], defaults=data)


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(create_plans, migrations.RunPython.noop)]
