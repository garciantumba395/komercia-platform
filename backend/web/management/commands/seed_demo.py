"""Données de démonstration : `python manage.py seed_demo`.

Idempotente : peut être relancée sans créer de doublon. Crée un commerçant de démonstration, une
boutique, un taux de change, des catégories, une quarantaine de produits avec stock, un caissier,
des clients et, si le module des ventes est disponible, quelques ventes d'exemple.
"""

import importlib.util
import random
import uuid
from decimal import Decimal

from django.core.management.base import BaseCommand
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Membership, Store, Tenant, User
from accounts.services import register_merchant
from catalog.models import Category, Product
from core.permissions import CASHIER
from core.tenancy import tenant_context
from currency.models import ExchangeRate
from customers.models import Customer
from stock.models import StockMovement
from stock.services import apply_movement

OWNER_PHONE = "+243990000000"
OWNER_PASSWORD = "Demo-1234!"
CASHIER_PHONE = "+243990000001"
CASHIER_PASSWORD = "Caissier-1234!"
RATE = Decimal("2800")
SALES_NAMESPACE = uuid.UUID("6f1c3a9e-5b1d-4c53-9a39-0d6c2f6f7a11")
DEMO_SALES = 15

ALIM, BOIS, HYG, MEN = "Alimentation", "Boissons", "Hygiène", "Ménage"

# (nom, catégorie, unité, prix, devise, coût d'achat, seuil minimum, stock initial)
# Prix et coûts en unité majeure : francs entiers, ou dollars avec centimes.
PRODUCTS = [
    ("Riz 1 kg", ALIM, "kg", "3500", "CDF", "2600", "20", "60"),
    ("Sac de riz 25 kg", ALIM, "piece", "24.00", "USD", "20.00", "3", "12"),
    ("Huile végétale 1 L", ALIM, "litre", "8000", "CDF", "6200", "12", "5"),
    ("Huile de palme 1 L", ALIM, "litre", "6000", "CDF", "4500", "10", "30"),
    ("Sucre 1 kg", ALIM, "kg", "4500", "CDF", "3600", "15", "8"),
    ("Farine de maïs 5 kg", ALIM, "piece", "14000", "CDF", "11000", "8", "25"),
    ("Farine de blé 1 kg", ALIM, "kg", "3000", "CDF", "2300", "10", "40"),
    ("Sel fin 500 g", ALIM, "piece", "1000", "CDF", "700", "20", "50"),
    ("Haricots secs 1 kg", ALIM, "kg", "5000", "CDF", "3800", "10", "35"),
    ("Spaghetti 500 g", ALIM, "piece", "2500", "CDF", "1800", "15", "45"),
    ("Pâte de tomate 70 g", ALIM, "piece", "800", "CDF", "550", "30", "90"),
    ("Sardines en boîte", ALIM, "piece", "3000", "CDF", "2200", "20", "60"),
    ("Lait en poudre 400 g", ALIM, "piece", "6.50", "USD", "5.00", "6", "18"),
    ("Lait concentré sucré 400 g", ALIM, "piece", "3500", "CDF", "2600", "6", "0"),
    ("Cubes bouillon (boîte de 24)", ALIM, "box", "4000", "CDF", "3000", "8", "20"),
    ("Café soluble 100 g", ALIM, "piece", "7000", "CDF", "5400", "6", "3"),
    ("Thé en sachets (boîte de 25)", ALIM, "box", "2500", "CDF", "1800", "10", "24"),
    ("Biscuits (paquet)", ALIM, "piece", "1500", "CDF", "1000", "20", "48"),
    ("Poisson salé 1 kg", ALIM, "kg", "12000", "CDF", "9500", "5", "14"),
    ("Cola 50 cl", BOIS, "piece", "1500", "CDF", "1100", "48", "120"),
    ("Soda orange 50 cl", BOIS, "piece", "1500", "CDF", "1100", "48", "96"),
    ("Soda citron 50 cl", BOIS, "piece", "1500", "CDF", "1100", "48", "72"),
    ("Eau minérale 1,5 L", BOIS, "piece", "2000", "CDF", "1400", "24", "60"),
    ("Eau minérale 50 cl", BOIS, "piece", "1000", "CDF", "700", "48", "100"),
    ("Bière blonde 72 cl", BOIS, "piece", "4000", "CDF", "3200", "24", "10"),
    ("Bière pression 72 cl", BOIS, "piece", "4000", "CDF", "3200", "24", "48"),
    ("Bière forte 65 cl", BOIS, "piece", "3500", "CDF", "2800", "24", "36"),
    ("Jus d’orange 1 L", BOIS, "litre", "4500", "CDF", "3400", "12", "20"),
    ("Boisson énergisante 33 cl", BOIS, "piece", "2500", "CDF", "1800", "12", "30"),
    ("Savon de toilette", HYG, "piece", "1500", "CDF", "1000", "20", "60"),
    ("Dentifrice 75 ml", HYG, "piece", "3500", "CDF", "2600", "10", "24"),
    ("Brosse à dents", HYG, "piece", "1500", "CDF", "900", "12", "30"),
    ("Papier toilette (4 rouleaux)", HYG, "piece", "3500", "CDF", "2600", "10", "40"),
    ("Serviettes hygiéniques (paquet)", HYG, "piece", "3000", "CDF", "2200", "10", "2"),
    ("Lotion corporelle 250 ml", HYG, "piece", "6000", "CDF", "4500", "6", "15"),
    ("Vaseline 100 ml", HYG, "piece", "2500", "CDF", "1700", "8", "20"),
    ("Lessive en poudre 1 kg", MEN, "piece", "6500", "CDF", "5000", "10", "25"),
    ("Eau de Javel 1 L", MEN, "litre", "2500", "CDF", "1800", "10", "30"),
    ("Savon de lessive (barre)", MEN, "piece", "1500", "CDF", "1000", "20", "70"),
    ("Allumettes (boîte de 10)", MEN, "box", "1000", "CDF", "600", "20", "50"),
    ("Bougies (paquet de 6)", MEN, "piece", "3000", "CDF", "2000", "10", "25"),
    ("Éponge de cuisine", MEN, "piece", "800", "CDF", "500", "15", "40"),
    ("Sacs poubelle (rouleau)", MEN, "piece", "3500", "CDF", "2600", "8", "18"),
    ("Bidon d’huile de palme 20 L", ALIM, "piece", "28.00", "USD", "23.00", "2", "6"),
]

# (nom, téléphone, limite de crédit en unité majeure, devise de la limite)
CUSTOMERS = [
    ("Mama Jeanne Mbuyi", "+243812345601", "50000", "CDF"),
    ("Papa Joseph Kasongo", "+243998765402", "100000", "CDF"),
    ("Restaurant Chez Tantine", "+243971112203", "200.00", "USD"),
    ("Patrick Lumbu", "+243850004404", "0", "CDF"),
    ("Paroisse Notre-Dame de Fatima", "+243820005505", "150000", "CDF"),
    ("Boutique Kinshasa Express", "+243990006606", "500.00", "USD"),
]


def minor(text: str, currency: str) -> int:
    value = Decimal(text)
    return int(value * (100 if currency == "USD" else 1))


class Command(BaseCommand):
    help = "Crée (ou complète) les données de démonstration : commerçant, boutique, produits, stock, clients."

    def handle(self, *args, **options):
        owner, tenant, store = self.ensure_merchant()
        with tenant_context(tenant.id):
            self.ensure_rate(owner, store)
            products = self.ensure_products()
            created_stock = self.ensure_stock(store, products, owner)
            self.ensure_customers()
        self.ensure_cashier(tenant)
        sales_message = self.ensure_sales(tenant, store, products)
        self.stdout.write(self.style.SUCCESS("Données de démonstration prêtes."))
        self.stdout.write(f"  Entreprise : {tenant.name} · boutique : {store.name}")
        self.stdout.write(f"  Produits : {len(products)} ({created_stock} entrée(s) de stock créée(s))")
        self.stdout.write(f"  Propriétaire : {OWNER_PHONE} / {OWNER_PASSWORD}")
        self.stdout.write(f"  Caissier     : {CASHIER_PHONE} / {CASHIER_PASSWORD}")
        self.stdout.write(f"  Ventes d'exemple : {sales_message}")

    # ----- commerçant, boutique, taux -----

    def ensure_merchant(self):
        owner = User.objects.filter(phone=OWNER_PHONE).first()
        if owner is None:
            owner, tenant, store = register_merchant(
                full_name="Marie Kabongo", phone=OWNER_PHONE, password=OWNER_PASSWORD,
                business_name="Boutique Démo", sector="retail", base_currency="CDF",
                address="Avenue du Commerce, Gombe, Kinshasa", store_name="Boutique Démo",
            )
            self.stdout.write("Commerçant de démonstration créé.")
            return owner, tenant, store
        membership = owner.memberships.select_related("tenant").first()
        tenant = membership.tenant
        with tenant_context(tenant.id):
            store = Store.objects.order_by("created_at").first()
            if store is None:
                store = Store.objects.create(name="Boutique Démo")
        return owner, tenant, store

    def ensure_rate(self, owner, store):
        if not ExchangeRate.objects.filter(store__isnull=True).exists():
            ExchangeRate.objects.create(
                rate_cdf_per_usd=RATE, set_by=owner, note="Taux de démonstration"
            )

    # ----- catalogue et stock -----

    def ensure_products(self):
        categories = {}
        for name in (ALIM, BOIS, HYG, MEN):
            category = Category.objects.filter(name__iexact=name).first()
            categories[name] = category or Category.objects.create(name=name)
        products = []
        for index, (name, cat, unit, price, currency, cost, min_stock, _qty) in enumerate(PRODUCTS, start=1):
            sku = f"DEMO-{index:03d}"
            product = Product.objects.filter(sku=sku).first()
            if product is None:
                product = Product.objects.create(
                    name=name, sku=sku, barcode=f"600100{index:07d}", category=categories[cat], unit=unit,
                    price_minor=minor(price, currency), currency=currency, cost_minor=minor(cost, currency),
                    track_stock=True, min_stock=Decimal(min_stock),
                )
            products.append(product)
        return products

    def ensure_stock(self, store, products, owner):
        created = 0
        for product, row in zip(products, PRODUCTS, strict=True):
            if StockMovement.objects.filter(product=product, reference="DEMO-INIT").exists():
                continue
            qty = Decimal(row[7])
            # Un produit à zéro : une entrée puis une sortie, pour qu'il apparaisse en rupture.
            entry = qty if qty > 0 else Decimal("1")
            apply_movement(product, store, entry, StockMovement.IN, reason="Stock initial de démonstration",
                           reference="DEMO-INIT", unit_cost_minor=product.cost_minor, user=owner)
            if qty == 0:
                apply_movement(product, store, Decimal("-1"), StockMovement.OUT,
                               reason="Rupture de démonstration", reference="DEMO-RUPTURE", user=owner)
            created += 1
        return created

    def ensure_customers(self):
        for name, phone, limit, currency in CUSTOMERS:
            if not Customer.objects.filter(name=name).exists():
                Customer.objects.create(name=name, phone=phone, credit_limit_minor=minor(limit, currency),
                                        credit_limit_currency=currency)

    def ensure_cashier(self, tenant):
        user = User.objects.filter(phone=CASHIER_PHONE).first()
        if user is None:
            user = User.objects.create_user(phone=CASHIER_PHONE, password=CASHIER_PASSWORD,
                                            full_name="Patience Mukendi")
        if not Membership.objects.filter(user=user, tenant=tenant).exists():
            Membership.objects.create(user=user, tenant=tenant, role=CASHIER)
        return user

    # ----- ventes d'exemple (uniquement si le module des ventes est disponible) -----

    def ensure_sales(self, tenant: Tenant, store, products):
        if importlib.util.find_spec("sales.services") is None:
            return "module des ventes absent, ignorées"
        try:
            return self._create_sales(store, products)
        except Exception as exc:  # la démo ne doit jamais échouer à cause des ventes
            self.stderr.write(f"Ventes d'exemple ignorées : {exc}")
            return f"ignorées ({exc})"

    def _create_sales(self, store, products):
        cashier = User.objects.get(phone=CASHIER_PHONE)
        token, _ = Token.objects.get_or_create(user=cashier)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

        existing = client.get("/api/v1/sales/", {"store": str(store.id)})
        if existing.status_code == 200:
            data = existing.json()
            count = data.get("count", len(data.get("results", data))) if isinstance(data, dict) else len(data)
            if count >= DEMO_SALES:
                return f"déjà présentes ({count})"
        current = client.get("/api/v1/sales/cash-sessions/current/", {"store": str(store.id)})
        if current.status_code == 404:
            opened = client.post(
                "/api/v1/sales/cash-sessions/open/",
                {"store": str(store.id), "opening_float": {"CDF": 50000, "USD": 2000}}, format="json",
            )
            if opened.status_code not in (200, 201):
                return f"ouverture de caisse refusée ({opened.status_code})"

        with tenant_context(store.tenant_id):
            customers = {c.name: c for c in Customer.objects.all()}
        rng = random.Random(2800)
        done, failed = 0, 0
        for i in range(DEMO_SALES):
            lines = rng.sample(products, rng.randint(1, 4))
            items = [
                {"product": str(p.id), "quantity": str(rng.randint(1, 3)), "discount_minor": 0} for p in lines
            ]
            total_cdf = sum(
                (p.price_minor if p.currency == "CDF" else p.price_minor * 28) * int(it["quantity"])
                for p, it in zip(lines, items, strict=True)
            )
            body = {
                "client_id": str(uuid.uuid5(SALES_NAMESPACE, f"seed-demo-{i}")), "store": str(store.id),
                "currency": "CDF", "customer": None, "items": items, "discount_minor": 0,
                "payments": [], "credit_remaining": False, "change_currency": "CDF",
            }
            kind = i % 5
            if kind == 4:  # vente à crédit à un client avec limite en francs
                body["customer"] = str(customers["Papa Joseph Kasongo"].id)
                body["payments"] = [{"method": "cash", "amount_minor": total_cdf // 4, "currency": "CDF"}]
                body["credit_remaining"] = True
            elif kind == 3:  # paiement mixte francs + dollars
                usd = max(total_cdf // 2 // 2800, 1)
                body["payments"] = [
                    {"method": "cash", "amount_minor": usd * 100, "currency": "USD"},
                    {"method": "cash", "amount_minor": max(total_cdf - usd * 2800, 0), "currency": "CDF"},
                ]
            else:  # espèces, avec un billet rond
                rounded = -(-total_cdf // 1000) * 1000
                body["payments"] = [{"method": "cash", "amount_minor": rounded, "currency": "CDF"}]
            response = client.post("/api/v1/sales/", body, format="json")
            if response.status_code in (200, 201):
                done += 1
            else:
                failed += 1
                detail = response.content[:200]
                self.stderr.write(f"  vente {i + 1} refusée ({response.status_code}) : {detail!r}")
        return f"{done} créée(s) ou déjà présente(s), {failed} refusée(s)"
