# Synchronisation hors ligne : protocole serveur

Public : développeur de l'application Android (Flutter). Références : cahier des charges section 18.5,
KOM-NF-004, KOM-NF-005, KOM-NF-014, KOM-NF-016 ; ADR 0001 (journal d'opérations sur mesure).
Code : `backend/sync/`. Schéma OpenAPI : `/api/docs/` (opérations `sync_pull`, `sync_push`, `sync_status`).

## 1. Vue d'ensemble

```
        PULL (serveur -> appareil)                        PUSH (appareil -> serveur)
  catalogue, clients+soldes, stock, taux          journal d'opérations rejoué dans l'ordre
  delta par curseur opaque, gzip                  idempotent par op_id, un résultat par opération
```

- Toutes les routes sont sous `/api/v1/sync/`, avec `Authorization: Token <clé>` comme le reste de l'API.
  Le tenant vient du token, jamais d'un paramètre.
- Rôles autorisés : propriétaire, gérant, caissier. Magasinier et comptable reçoivent 403.
- Les identifiants (clients, `client_id` des ventes, `op_id`, `device_id`) sont des UUID générés sur l'appareil.
- Les montants sont des entiers dans la plus petite unité (CDF : franc ; USD : centime), avec leur devise.
- Les nombres décimaux de quantité sont des chaînes (`"2.500"`) ; ne jamais les lire en `double`.

### Séquence type

1. Au premier lancement : `GET status/`, puis `GET pull/?store=<boutique>` en boucle tant que `has_more` est vrai.
   Mémoriser le dernier `cursor`.
2. Hors ligne : chaque action métier ajoute une opération dans le journal local (SQLite) avec un `op_id`
   neuf et `device_time`.
3. En ligne : `POST push/` par lots de 200 au plus, dans l'ordre du journal ; marquer chaque opération
   `applied` / `duplicate` / `rejected` selon la réponse ; ne supprimer du journal que celles qui ont un
   résultat.
4. Puis `pull` depuis le dernier curseur (les soldes clients et le stock reflètent maintenant vos ventes).
5. Si la dernière synchronisation date de plus de `max_offline_days` jours (`status`), l'appli exige une
   synchronisation avant de continuer.

Si la réponse d'un `push` est perdue (coupure), **renvoyez le même lot tel quel** : les opérations déjà
appliquées reviennent en `duplicate`, rien n'est refait.

## 2. `GET /api/v1/sync/status/`

```json
{"server_time": "2026-11-17T10:00:00Z", "min_app_version": "0.1.0", "max_offline_days": 7}
```

`min_app_version` : en dessous, l'appli doit se mettre à jour. `max_offline_days` : durée maximale hors
ligne (KOM-NF-015).

## 3. `GET /api/v1/sync/pull/`

Paramètres : `since` (curseur reçu au dernier appel, omis la première fois), `store` (UUID de la boutique
de l'appareil : limite `stock_levels` à cette boutique et lie le curseur à elle), `limit` (1 à 1000, 500 par
défaut ; s'applique à **chaque** flux).

```json
{
  "cursor": "eyJzIjp7InByb2R1Y3RzIjpbIjIwMjYtMTEtMTdUMDk6NTk6MDEuMTIzNDU2KzAwOjAwIiwiLi4uIl19LCJzdG9yZSI6bnVsbCwidiI6MX0",
  "has_more": false,
  "server_time": "2026-11-17T10:00:00.250000Z",
  "changes": {
    "stores":       [{"id": "...", "name": "Boutique", "address": "", "is_active": true, "updated_at": "..."}],
    "categories":   [{"id": "...", "name": "Boissons", "parent": null, "updated_at": "..."}],
    "products":     [{"id": "...", "name": "Savon", "sku": "SAV-1", "barcode": "", "category": "...",
                      "kind": "product", "unit": "piece", "price_minor": 1500, "currency": "CDF",
                      "cost_minor": 1000, "track_stock": true, "min_stock": "0.000",
                      "is_active": true, "updated_at": "..."}],
    "customers":    [{"id": "...", "name": "Marie", "phone": "+243...", "address": "", "notes": "",
                      "credit_limit_minor": 100000, "credit_limit_currency": "CDF", "is_active": true,
                      "balance": {"CDF": 1500}, "updated_at": "..."}],
    "stock_levels": [{"id": "...", "product": "...", "store": "...", "quantity": "7.000", "updated_at": "..."}],
    "exchange_rate": {"id": "...", "store": null, "rate_cdf_per_usd": "2850.5000", "effective_at": "..."}
  }
}
```

### Curseur

- **Opaque** : ne pas l'interpréter ni le fabriquer. Le conserver tel quel et le renvoyer dans `since`.
- **Monotone** : chaque flux avance selon l'ordre (`updated_at`, `id`). Plusieurs lignes ayant le même
  `updated_at` sont servies sans oubli ni doublon, y compris quand une page s'arrête au milieu du groupe.
- **Pagination** : tant que `has_more` est vrai, rappeler immédiatement avec le `cursor` reçu. Quand
  `has_more` est faux, le delta est épuisé ; conserver ce curseur pour la synchronisation suivante.
- Les **doublons éventuels** (une ligne modifiée entre deux pages revient) se traitent par *upsert* sur `id` :
  le pull est idempotent.
- `invalid_cursor` (400) : repartir sans `since` (synchronisation complète). Un curseur émis pour une
  boutique (`store`) n'est valable que pour cette boutique.
- Délai de sécurité : les lignes modifiées depuis moins de `SYNC_PULL_LAG_SECONDS` (2 s par défaut) ne sont
  servies qu'au passage suivant, pour ne jamais rater une transaction validée en retard. Rien n'est perdu.

### Règles de contenu

- **Produits désactivés inclus** : `is_active: false` signifie « retirer du catalogue de vente local »
  (ne pas supprimer : l'historique des ventes y fait référence). Même chose pour les clients inactifs.
- **Coût d'achat** (`cost_minor`) : absent pour un caissier. Il n'est envoyé qu'au propriétaire et au gérant.
- **Clients** : `balance` = dette ouverte par devise (`{"CDF": 1500, "USD": 200}`). Un client revient dans
  le delta aussi quand son solde change (vente à crédit, paiement, annulation) sans que sa fiche change.
- **Stock** : niveaux de la boutique demandée. Peut être négatif (stock négatif toléré, voir 4.3).
- **Taux de change** : toujours présent (taux courant de la boutique, sinon du tenant) ; `null` si aucun
  taux n'est défini : dans ce cas, l'appli ne doit pas proposer de paiement en devise différente.
- Réponse **gzip** si la requête porte `Accept-Encoding: gzip` (réseaux 2G/3G).

## 4. `POST /api/v1/sync/push/`

```json
{
  "device_id": "5d0c6a3e-3a0f-4e51-9d0a-0c1f7c2d9b11",
  "operations": [
    {"op_id": "…uuid…", "type": "sale.create", "device_time": "2026-11-16T14:03:22+02:00", "payload": {}}
  ]
}
```

- 200 opérations au plus par lot : au-delà, **400** `{"code": "batch_too_large"}` et rien n'est appliqué.
- Enveloppe invalide (UUID, date, liste vide...) : **400** avant tout traitement.
- Les opérations sont rejouées **dans l'ordre**. Chaque opération s'exécute dans sa **propre transaction** :
  un rejet n'annule pas les autres, et une opération appliquée le reste même si la réponse est perdue.
- `device_time` est conservé dans le journal (et dans l'audit) mais n'est **jamais** l'heure de la vente :
  `created_at` est l'heure du serveur.

### 4.1 Réponse

HTTP 200 avec un résultat par opération, dans le même ordre :

```json
{
  "results": [
    {"op_id": "…", "status": "applied", "entity_id": "…", "data": {"number": "B01-000042"},
     "warnings": [{"code": "negative_stock", "message": "…", "details": {"product": "…", "quantity": "-2.000"}}]},
    {"op_id": "…", "status": "duplicate", "entity_id": "…", "data": {"number": "B01-000042"}},
    {"op_id": "…", "status": "rejected", "error": {"code": "product_inactive", "message": "…"}}
  ],
  "server_time": "2026-11-17T10:00:00Z"
}
```

| `status` | Sens | Action de l'appareil |
|---|---|---|
| `applied` | Appliquée maintenant. | Marquer comme synchronisée ; lire `entity_id`, `data`, `warnings`. |
| `duplicate` | Déjà appliquée (même `op_id`, ou même entité déjà présente). Rien n'a été refait ; `entity_id` identique. | Marquer comme synchronisée. |
| `rejected` | Refusée ; `error.code` dit pourquoi. Les opérations suivantes ne sont pas affectées. | Voir la table des codes. |

`entity_id` est l'identifiant **serveur** de l'entité (id de la vente, du client, de la session de caisse, du
mouvement de stock). Pour une vente, `data` contient `number` (numéro de reçu serveur), `client_id`,
`total_minor`, `currency`.

### 4.2 Idempotence

- Clé : `(tenant, op_id)`. Une opération déjà **appliquée** renvoie `duplicate` avec le même `entity_id`
  et les mêmes `data`/`warnings` que la première fois, sans rien refaire, y compris si deux requêtes
  identiques arrivent **en même temps** (verrou PostgreSQL par `op_id`).
- Une opération **rejetée** n'est pas définitive : le même `op_id` est réévalué à chaque envoi (par exemple
  une vente rejetée parce que le produit était désactivé s'applique si le produit est réactivé). Chaque
  tentative est journalisée avec son contenu. Pour abandonner une opération rejetée, ne la renvoyez pas.
- Deux tenants peuvent utiliser le même `op_id` sans aucun lien. Un `op_id` d'un autre tenant ne révèle
  rien (RLS) : on obtient simplement une application indépendante.
- Une vente est aussi idempotente par `client_id` : si le même `client_id` arrive sous un autre `op_id`,
  résultat `duplicate` (même vente) ; avec un contenu différent : `rejected` / `conflict`.
- Journal serveur : table `SyncOperation` (op_id, device_id, type, status, entity_id, payload, result,
  device_time, received_at), **en ajout seul**.

### 4.3 Types d'opérations et formats

Les `payload` réutilisent les formats des API existantes.

#### `sale.create`

Payload = corps de `POST /api/v1/sales/` (`client_id`, `store`, `currency`, `customer`, `items[]`,
`discount_minor`, `payments[]`, `credit_remaining`, `change_currency`, `override_credit_limit`).
`device_time` est repris de l'opération s'il manque. Rôles : propriétaire, gérant, caissier.

```json
{"op_id": "…", "type": "sale.create", "device_time": "2026-11-16T14:03:22+02:00",
 "payload": {"client_id": "…", "store": "…", "customer": null,
             "items": [{"product": "…", "quantity": "2"}],
             "payments": [{"method": "cash", "amount_minor": 3000, "currency": "CDF"}]}}
```

Règles : le stock insuffisant ne refuse **jamais** la vente (stock négatif, avertissement `negative_stock`
par produit concerné). Un produit désactivé ou inconnu est rejeté. Un caissier doit avoir une session de
caisse ouverte dans la boutique (sinon `dependency_missing`) : ouvrez-la avec `cash_session.open` avant.
Le prix et la remise suivent les règles de rôle habituelles. La vente reçoit son numéro de reçu côté serveur
(`data.number`) : l'appareil peut imprimer un numéro provisoire local et le remplacer par celui-ci.

#### `sale.cancel`

`{"sale_id": "<uuid serveur>" | "client_id": "<uuid appareil>", "reason": "..."}`. Utilisez `client_id` si la
vente a été créée dans le même lot (l'`entity_id` serveur n'est pas encore connu). Propriétaire et gérant
uniquement. Contre-passation : stock restitué, dette annulée (refusée en `conflict` si le client a déjà payé
une partie de cette dette), vente conservée avec le statut `cancelled`.

#### `customer.create`

`{"id": "<uuid appareil>", "name": "...", "phone": "", "address": "", "notes": "", "is_active": true}`.
Le client reçoit l'`id` choisi par l'appareil. Un `id` déjà existant donne `duplicate` (la fiche existante
n'est pas modifiée). Les champs `credit_limit_*` sont **ignorés** (voir 5) : un client créé hors ligne n'a pas
de crédit tant qu'un gérant ne lui accorde pas une limite.

#### `customer.update`

`{"id": "...", "name": "...", "phone": "...", "address": "...", "notes": "...", "is_active": true}` : seuls les
champs présents sont modifiés. Règle de conflit : voir 5.

#### `stock.movement`

Format de `POST /api/v1/stock/movements/` : `{"product", "store", "kind": "in"|"out"|"adjustment",
"quantity", "unit_cost_minor"?, "reason", "reference"?}`. Propriétaire et gérant. Le mouvement n'est jamais
refusé pour stock insuffisant (avertissement `negative_stock`). Écrit dans l'audit avec `device_time`.

#### `cash_session.open`

`{"store": "<uuid>", "opening_float": {"CDF": 5000, "USD": 0}}`. `entity_id` = id de la session. Une session
déjà ouverte pour ce caissier dans cette boutique : `rejected` / `conflict` (avec `details.session_id`) ;
les ventes suivantes utiliseront cette session.

#### `cash_session.close`

`{"counted": {"CDF": 8000, "USD": 0}, <référence>}` où la référence est l'une de :
`"session_id"` (uuid serveur), `"open_op_id"` (l'`op_id` de l'ouverture, pour une session ouverte et
fermée hors ligne dans le même lot), ou `"store"` (session ouverte de ce caissier dans la boutique).
Le caissier ne clôture que ses propres sessions. `data` contient `expected` et `difference` par devise.

### 4.4 Codes d'erreur (`error.code`)

| Code | Sens | Que faire |
|---|---|---|
| `validation_error` | Contenu invalide (`error.details` : erreurs par champ). | Corriger l'appli ; l'opération est inutilisable telle quelle. |
| `unknown_type` | Type d'opération inconnu de ce serveur. | Mettre l'appli à jour. |
| `forbidden` | Le rôle n'autorise pas cette opération (annulation, mouvement de stock, prix modifié...). | Informer l'utilisateur. |
| `dependency_missing` | Une opération dont celle-ci dépend n'a pas été appliquée : client, vente ou session de caisse introuvable. | Corriger la cause (souvent un rejet plus haut dans le lot) puis renvoyer. |
| `product_not_found`, `product_inactive`, `store_not_found` | Référence inconnue ou désactivée. | Signaler au commerçant (vente à traiter manuellement) ; elle reste dans le journal serveur. |
| `conflict` | Déjà fait ou incompatible (vente déjà annulée, session déjà ouverte, `client_id` réutilisé avec un autre contenu, `id` client déjà pris). | Voir `message`. |
| `stale_update` | Fiche client modifiée plus récemment ailleurs (voir 5). | Faire un `pull` ; informer l'utilisateur si utile. |
| `business_rule` | Règle métier (paiement insuffisant, crédit refusé, remise trop forte...). | Voir `message`. |
| `not_found` | Entité introuvable. | |
| `temporary_error`, `internal_error` | Panne passagère ou inattendue. | Renvoyer plus tard. |

### 4.5 Avertissements (`warnings[]`)

Chaque avertissement : `{"code", "message", "details"?}`. Ils accompagnent `applied` (et `rejected` pour
`clock_skew`) et ne bloquent rien.

- `negative_stock` : le stock d'un produit de la boutique est devenu négatif ; `details` = produit, boutique,
  quantité. À montrer au gérant (réapprovisionnement ou erreur d'inventaire).
- `clock_skew` : `device_time` diffère de plus de 10 minutes de l'heure du serveur à la réception
  (`details.skew_minutes`). Normal pour une opération ancienne rejouée après des jours hors ligne ; suspect si
  l'appareil vient d'être resynchronisé. L'heure du serveur fait foi pour la vente dans tous les cas.
- `sensitive_field_ignored` : des champs sensibles du payload n'ont pas été appliqués (`details.fields`).

## 5. Règles de conflit (cahier des charges 18.5)

| Donnée | Règle |
|---|---|
| Ventes et paiements | **Jamais écrasés** ni supprimés. Une annulation crée une contre-passation. |
| Stock | Les mouvements s'additionnent ; le stock peut devenir négatif et est signalé. |
| Fiche client (`customer.update`) | **La dernière modification gagne.** On compare l'heure appareil de l'opération (ramenée à l'heure de réception si elle est dans le futur) à la dernière modification de la fiche : plus ancienne, l'opération est `rejected` / `stale_update`. Plusieurs mises à jour du même appareil dans l'ordre s'appliquent toutes. |
| `credit_limit_minor`, `credit_limit_currency` | **Champs sensibles : jamais modifiés par synchronisation**, ni à la création, ni à la mise à jour, quel que soit le rôle. Un avertissement `sensitive_field_ignored` est renvoyé si la valeur envoyée diffère. Le gérant les modifie en ligne (interface web / API clients). |
| `customer.create` avec un `id` existant | `duplicate` ; la fiche existante est inchangée. |
| Produits | Données de référence en lecture seule via `pull` (aucune écriture catalogue par `push`). |

## 6. Mise en œuvre côté serveur

- `PushView` n'est **pas** enveloppée dans la transaction de requête (`ATOMIC_REQUESTS` désactivé pour
  cette vue) : chaque opération a sa transaction, le contexte de tenant (RLS) est réactivé dans chacune.
- Les ventes passent par `sales.services.create_sale`, les annulations par `cancel_sale`, les sessions par
  `open_session` / `close_session`, le stock par `stock.services.apply_movement` : aucune règle métier n'est
  dupliquée.
- La compression gzip du `pull` est faite dans la vue (le middleware `GZipMiddleware` n'est pas activé dans
  les réglages). Si le middleware est activé plus tard, il ignorera ces réponses déjà compressées.
- Réglage optionnel : `SYNC_PULL_LAG_SECONDS` (défaut 2).

## 7. Limites connues

- Pas de lien automatique « nouveau client créé hors ligne avec crédit » : la limite est accordée en ligne.
- Le coût du `pull` sur les clients calcule la dernière écriture de crédit par client : adapté jusqu'à
  quelques milliers de clients ; au-delà, prévoir un index sur `CreditEntry(customer, created_at)` ou une
  colonne dédiée.
- Les écritures du `push` sont séquentielles dans la requête : prévoir des lots de 50 à 200 opérations ;
  compter environ 20 à 40 ms par vente.
