# Intégration du module `billing`

Le module est autonome : rien n'est branché ailleurs tant que les étapes ci-dessous ne sont pas faites
(chef de projet). Les routes `/api/v1/billing/` et la migration (plans initiaux, RLS) sont déjà actives.

## 1. Réglages (`komersia/settings.py`)

```python
# Fournisseur de paiement : "sandbox" (test) pour l'instant. En production, un fournisseur réel.
BILLING_PROVIDER = os.environ.get("BILLING_PROVIDER", "sandbox")
# Secret HMAC-SHA256 des webhooks (en-tête X-Komersia-Signature = hex(HMAC(secret, corps brut))).
# Absent => tout webhook est refusé (403).
BILLING_WEBHOOK_SECRET = os.environ.get("BILLING_WEBHOOK_SECRET")
```

Production : ne JAMAIS laisser `BILLING_PROVIDER=sandbox` (l'endpoint `sandbox/confirm/` permet de se
confirmer ses propres paiements, sans authentification).

Application de la règle de lecture seule, après l'authentification par défaut :

```python
REST_FRAMEWORK = {
    ...
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "billing.permissions.SubscriptionWriteAllowed",
    ],
}
```

Attention : les vues qui déclarent `permission_classes = [HasRole]` REMPLACENT la valeur par défaut. Il faut
donc ajouter `SubscriptionWriteAllowed` à ces vues (`permission_classes = [HasRole, SubscriptionWriteAllowed]`),
ou l'intégrer dans `core.permissions.HasRole` (appel à `SubscriptionWriteAllowed().has_permission(...)`).
Sont toujours exemptés : méthodes GET/HEAD/OPTIONS, chemins `/api/v1/auth/`, `/api/v1/billing/`,
`/api/v1/sync/` et tout chemin contenant `/export` (ex. `/api/v1/reports/export/`). Refus : HTTP 402
`{"detail": "Votre essai ou votre abonnement est terminé. ...", "code": "subscription_inactive"}`.

## 2. Limites des plans (`billing.services.check_limit`)

Appeler AVANT la création, dans le contexte du tenant ; lève `PlanLimitReached` (403, code
`plan_limit_reached`, message en français) :

- `accounts.views.StoreViewSet` : dans `perform_create` (ou `create`) → `check_limit(request.tenant, "stores")`.
- `accounts.views.MemberViewSet.create` : avant `User.objects.create_user` → `check_limit(request.tenant, "users")`.
- Création d'une boutique ou d'un utilisateur dans `sync` / `settings` (le cas échéant) : même appel.

Pendant l'essai, les limites sont celles du plan « pro » (`services.TRIAL_PLAN_CODE`). Le comptage = boutiques
actives et memberships actifs du tenant.

## 3. Synchronisation hors ligne (KOM-074)

`/api/v1/sync/` est exempté de `SubscriptionWriteAllowed` : les ventes déjà faites doivent pouvoir être
rejouées. Dans `sync.services._sale_create` (et plus généralement toute opération créant une vente),
refuser les opérations faites APRÈS le passage en lecture seule :

```python
from billing import services as billing

# au début de _sale_create, après avoir fixé data["device_time"] :
if not billing.is_write_allowed_at(request_tenant_id, data["device_time"]):
    raise OpRejected("subscription_inactive",
                     "Abonnement terminé : cette vente a été faite après la fin de l'abonnement.")
```

`is_write_allowed_at(tenant_ou_abonnement, quand, now=None)` est vrai si l'abonnement n'est pas en lecture
seule, ou si `quand < read_only_since`. Les autres opérations de sync (clients, stock) sont à traiter de
la même façon si on veut les bloquer. Limite : une fois l'abonnement réactivé (`read_only_since` effacé),
tout est de nouveau accepté.

## 4. Tâche planifiée

`python manage.py billing_tick` (idempotent) : applique les transitions d'état, génère les rappels
(`BillingNotice`) et expire les paiements `pending` de plus de 24 h. À planifier chaque heure
(cron / tâche du conteneur). `--now <ISO 8601>` pour rattraper ou simuler.
`python manage.py billing_backfill` : une fois au déploiement, crée un essai de 14 jours pour les tenants
existants sans abonnement (idempotent). Les nouveaux tenants reçoivent leur essai par signal.

## 5. Notes d'exploitation

- Webhook : `POST /api/v1/billing/webhooks/<provider>/`, `<provider>` doit être égal à `BILLING_PROVIDER`
  (sinon 404). Signature absente/invalide ou secret non configuré : 403. Rejeu : 200 sans nouvelle prolongation.
- Tables : `Plan` et `PaymentReference` sans RLS (rôle applicatif : droits par `ALTER DEFAULT PRIVILEGES`
  de `scripts/db-init.sql`) ; `Subscription`, `SubscriptionPayment`, `BillingNotice` avec RLS.
- Admin Django : `Plan` éditable ; les autres modèles étant sous RLS, l'admin n'y voit que les lignes du
  tenant actif de la session (en pratique aucune) : pour le support, passer par un script avec `tenant_context`.
- Prix des plans : éditer `Plan` dans l'admin (valeurs initiales = hypothèses de la section 16.2).
  Le supplément par boutique (`extra_store_price_usd_minor`) est affiché mais pas encore facturé.
