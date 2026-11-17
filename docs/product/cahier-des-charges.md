# Cahier des charges — Komersia

> Version 1.1 — base de référence du dépôt `komersia-platform`.
> Les exigences sont identifiées par un code, priorisées et rédigées de façon testable.
> **Priorités :** P0 = indispensable au MVP · P1 = important, juste après le MVP · P2 = évolution · P3 = long terme.
> **Statut des chiffres :** les valeurs marquées *(hypothèse)* sont à valider avec les commerçants pilotes avant toute décision.
> Les sections 16 à 23 complètent la v1.0 : modèle économique, jalons, règles métier, permissions, conformité et décisions ouvertes.

## 1. Présentation du projet

**Nom du projet :** Komersia
**Domaine principal :** komersia.app
**Type de produit :** Plateforme SaaS multi-tenant de gestion commerciale
**Marché initial :** Commerçants et petites entreprises de la République démocratique du Congo, avec extension progressive en Afrique centrale.
**Langues initiales :** Français et anglais.
**Devises initiales :** Franc congolais (CDF) et dollar américain (USD).

### 1.1 Vision

Komersia permet à tout commerçant de vendre, encaisser, gérer son stock, suivre ses clients et piloter son activité depuis une application simple, fiable et accessible sur web et mobile.

### 1.2 Problème à résoudre

De nombreux commerçants gèrent encore leurs ventes, stocks, dettes clients et revenus avec des cahiers, des notes manuscrites ou des fichiers dispersés. Cela provoque des pertes, des erreurs de caisse, des ruptures de stock, des impayés non suivis et un manque de visibilité sur la rentabilité.

### 1.3 Solution proposée

Komersia fournit un socle commun de gestion, adaptable à tous les secteurs grâce à des modules optionnels : commerce de détail, restauration, pharmacie, quincaillerie, salon de coiffure, grossiste, services et livraison.

### 1.4 Objectifs du produit

- Simplifier la vente et l'encaissement.
- Réduire les pertes liées au stock et aux erreurs de caisse.
- Suivre les dettes clients et améliorer les encaissements.
- Donner au commerçant une visibilité claire sur son activité.
- Fonctionner même en cas de connexion Internet instable.
- Permettre la gestion d'un ou plusieurs points de vente.
- S'adapter aux spécificités de chaque secteur sans reconstruire l'application.

## 2. Cibles et utilisateurs

| Utilisateur | Rôle principal | Besoins |
|---|---|---|
| Propriétaire | Piloter l'activité | Voir le chiffre d'affaires, les bénéfices, les dettes et le stock |
| Gérant | Superviser les opérations | Gérer les ventes, le personnel, les stocks et les rapports |
| Caissier | Vendre et encaisser | Enregistrer une vente rapidement, imprimer un reçu, accepter plusieurs paiements |
| Magasinier | Gérer les marchandises | Réceptionner, inventorier, transférer et alerter sur les ruptures |
| Comptable | Suivre les finances | Exporter les ventes, factures, paiements et dépenses |
| Client | Recevoir un service | Recevoir un reçu, payer, suivre une dette ou une fidélité |

## 3. Périmètre du MVP

Le MVP doit permettre à un commerçant de démarrer et gérer son activité de manière autonome. Il est livré par jalons successifs (voir section 17).

| Priorité | Fonctionnalité | Description |
|---|---|---|
| P0 | Inscription et création de boutique | Créer un compte, une entreprise et une première boutique |
| P0 | Authentification et rôles | Connexion sécurisée, gestion des utilisateurs et permissions |
| P0 | Catalogue | Créer des produits ou services, catégories, prix, unités et codes-barres |
| P0 | Caisse / POS | Enregistrer une vente, appliquer une remise, générer un reçu |
| P0 | Paiements | Encaisser en espèces, Mobile Money et crédit client |
| P0 | Stock | Suivre les entrées, sorties, ajustements et alertes de rupture |
| P0 | Clients | Créer des fiches clients, suivre les dettes et l'historique |
| P0 | Rapports | Voir les ventes du jour, les produits vendus, les dettes et le stock |
| P0 | Mode hors ligne | Enregistrer les ventes sans Internet et synchroniser ensuite |
| P0 | Multi-devises | Gérer les ventes et paiements en CDF et USD |
| P1 | Factures et devis | Créer, imprimer et envoyer des documents |
| P1 | Fournisseurs et achats | Gérer les commandes, réceptions et coûts d'achat |
| P1 | Multi-boutiques | Gérer plusieurs points de vente depuis un même compte |
| P1 | Notifications | Alertes stock, dettes, ventes et synchronisation |
| P2 | Fidélité | Points, remises et programmes clients |
| P2 | Modules sectoriels | Restaurant, pharmacie, salon, grossiste, livraison |

## 4. Exigences fonctionnelles

### 4.1 Gestion des commerçants

| ID | Exigence | Priorité |
|---|---|---|
| KOM-001 | L'utilisateur doit pouvoir créer un compte avec nom, téléphone, e-mail et mot de passe. | P0 |
| KOM-002 | L'utilisateur doit pouvoir créer une entreprise avec nom, secteur, devise principale et adresse. | P0 |
| KOM-003 | L'utilisateur doit pouvoir créer une ou plusieurs boutiques. | P0 |
| KOM-004 | Le système doit permettre de définir les rôles : propriétaire, gérant, caissier, magasinier et comptable. | P0 |
| KOM-005 | Le propriétaire doit pouvoir inviter, désactiver ou supprimer un utilisateur. | P0 |
| KOM-006 | Chaque utilisateur doit accéder uniquement aux données de son entreprise. | P0 |
| KOM-007 | L'utilisateur doit pouvoir vérifier son numéro de téléphone par code OTP (SMS/WhatsApp) et réinitialiser son mot de passe. | P0 |
| KOM-008 | Le caissier doit pouvoir se connecter rapidement sur la caisse par code PIN, après une première authentification de l'appareil. | P0 |
| KOM-009 | Le propriétaire doit pouvoir voir et révoquer les appareils et sessions actifs, et activer une double authentification pour son compte. | P1 |

### 4.2 Catalogue produits et services

| ID | Exigence | Priorité |
|---|---|---|
| KOM-010 | L'utilisateur doit pouvoir créer un produit avec nom, SKU, code-barres, catégorie, unité, prix de vente et prix d'achat. | P0 |
| KOM-011 | L'utilisateur doit pouvoir créer un service sans stock, par exemple une coiffure, une réparation ou une consultation. | P0 |
| KOM-012 | Le système doit supporter les variantes : taille, couleur, format ou capacité. | P1 |
| KOM-013 | Le système doit permettre la recherche par nom, code-barres, SKU ou catégorie. | P0 |
| KOM-014 | L'utilisateur doit pouvoir importer et exporter le catalogue en CSV ou Excel. | P1 |
| KOM-015 | Le système doit permettre de définir des prix différents selon le type de client : détail, grossiste ou fidélité. | P2 |

### 4.3 Ventes et caisse

| ID | Exigence | Priorité |
|---|---|---|
| KOM-020 | Le caissier doit pouvoir rechercher un produit par nom, code-barres ou catégorie. | P0 |
| KOM-021 | Le caissier doit pouvoir ajouter plusieurs produits au panier et modifier les quantités. | P0 |
| KOM-022 | Le système doit calculer automatiquement le sous-total, les remises, les taxes éventuelles et le total. | P0 |
| KOM-023 | Le caissier doit pouvoir enregistrer une vente en moins de 10 secondes pour un produit connu. | P0 |
| KOM-024 | Le système doit générer un reçu imprimable ou envoyable par WhatsApp/SMS. | P0 |
| KOM-025 | Le caissier doit pouvoir appliquer une remise globale ou par ligne, selon ses permissions. | P0 |
| KOM-026 | Le système doit permettre un retour, un remboursement ou une annulation avec justification. | P1 |
| KOM-027 | Le système doit conserver l'historique complet des ventes, modifications et annulations. | P0 |
| KOM-028 | Le caissier doit pouvoir ouvrir et clôturer une session de caisse avec fonds initial, comptage final et calcul de l'écart par moyen de paiement et par devise. | P0 |
| KOM-029 | Le caissier doit pouvoir mettre un panier en attente et le reprendre plus tard. | P1 |

### 4.4 Paiements

| ID | Exigence | Priorité |
|---|---|---|
| KOM-030 | Le système doit accepter les paiements en espèces. | P0 |
| KOM-031 | Le système doit accepter les paiements Mobile Money : M-Pesa, Airtel Money, Orange Money et Afrimoney. | P0 |
| KOM-032 | Le système doit permettre le paiement partiel et le suivi du solde restant. | P0 |
| KOM-033 | Le système doit permettre la vente à crédit avec limite et échéancier. | P0 |
| KOM-034 | Le système doit enregistrer chaque paiement avec le montant, la devise, le moyen, la référence et le statut. | P0 |
| KOM-035 | Le système doit rapprocher automatiquement les paiements Mobile Money confirmés par webhook. | P0 |
| KOM-036 | Le système doit permettre de convertir ou afficher les montants en CDF et USD. | P0 |
| KOM-037 | Le taux de change appliqué doit être enregistré sur chaque vente et chaque paiement, et ne plus changer ensuite (voir section 18). | P0 |
| KOM-038 | Le propriétaire ou le gérant doit pouvoir définir et historiser le taux de change CDF/USD de chaque boutique. | P0 |
| KOM-039 | Le système doit gérer un paiement en plusieurs devises ou moyens sur une même vente, avec rendu de monnaie dans la devise choisie. | P0 |

### 4.5 Stock et achats

| ID | Exigence | Priorité |
|---|---|---|
| KOM-040 | Le système doit suivre la quantité disponible par produit et par boutique. | P0 |
| KOM-041 | Le système doit enregistrer les entrées, sorties, ajustements et transferts de stock. | P0 |
| KOM-042 | Le système doit décrémenter automatiquement le stock après une vente d'un produit stockable. | P0 |
| KOM-043 | Le système doit envoyer une alerte lorsqu'un produit atteint son seuil minimum. | P0 |
| KOM-044 | L'utilisateur doit pouvoir réaliser un inventaire et enregistrer les écarts. | P1 |
| KOM-045 | Le système doit permettre la gestion des fournisseurs, commandes et réceptions. | P1 |
| KOM-046 | Le système doit calculer le coût moyen d'achat et la marge estimée. | P1 |

### 4.6 Clients et crédit

| ID | Exigence | Priorité |
|---|---|---|
| KOM-050 | L'utilisateur doit pouvoir créer une fiche client avec nom, téléphone, adresse et notes. | P0 |
| KOM-051 | Le système doit afficher l'historique des achats, paiements et dettes d'un client. | P0 |
| KOM-052 | Le système doit calculer automatiquement le solde de crédit d'un client. | P0 |
| KOM-053 | Le système doit empêcher une vente à crédit si la limite autorisée est dépassée, sauf autorisation spécifique. | P0 |
| KOM-054 | Le système doit permettre l'envoi de rappels de paiement par WhatsApp ou SMS. | P1 |
| KOM-055 | Le système doit permettre un programme de fidélité par points ou remises. | P2 |

### 4.7 Rapports et pilotage

| ID | Exigence | Priorité |
|---|---|---|
| KOM-060 | Le propriétaire doit voir les ventes du jour, de la semaine et du mois. | P0 |
| KOM-061 | Le système doit afficher le chiffre d'affaires, le nombre de ventes, la marge estimée et les produits les plus vendus. | P0 |
| KOM-062 | Le système doit afficher les dettes clients, les encaissements et les produits en rupture. | P0 |
| KOM-063 | Le propriétaire doit pouvoir comparer les performances des boutiques. | P1 |
| KOM-064 | Le système doit permettre l'export des rapports en PDF, CSV ou Excel. | P1 |
| KOM-065 | Le système doit afficher les données en temps réel ou après synchronisation hors ligne. | P0 |

### 4.8 Abonnement et facturation de la plateforme

Voir le modèle économique en section 16. L'abonnement est la seule source de revenus de Komersia.

| ID | Exigence | Priorité |
|---|---|---|
| KOM-070 | Le système doit gérer des plans d'abonnement configurables, avec des limites par plan (boutiques, utilisateurs, modules, volume de messages). | P1 |
| KOM-071 | Un nouveau commerçant doit bénéficier d'un **essai gratuit de 14 jours**, sans moyen de paiement enregistré, avec accès aux fonctions du plan Pro *(hypothèse)*. Il n'existe pas de plan gratuit permanent. Le système doit rappeler la fin de l'essai (à 7 jours, 3 jours, 1 jour et le dernier jour) et proposer la souscription depuis ces rappels. | P1 |
| KOM-072 | **Première souscription :** le commerçant doit pouvoir choisir un plan, payer par Mobile Money, et voir son abonnement activé dès la confirmation du paiement, avec un reçu. | P1 |
| KOM-073 | **Paiement mensuel :** le système doit calculer l'échéance, envoyer des rappels avant la date (7 jours, 3 jours et le jour même), permettre au commerçant de payer en quelques actions par Mobile Money, et prolonger l'abonnement d'un mois à la confirmation. | P1 |
| KOM-074 | En cas d'impayé, le système doit appliquer une période de grâce, puis passer le compte en lecture seule. Les ventes déjà enregistrées (y compris hors ligne) doivent toujours être synchronisées, et l'export des données doit rester possible. | P1 |
| KOM-079 | À la fin de l'essai sans souscription, le compte passe en lecture seule avec export possible. Les données sont conservées pendant 90 jours *(hypothèse)* pour permettre la souscription, puis traitées selon la politique de conservation (section 20). | P1 |
| KOM-075 | Le commerçant doit pouvoir passer à un plan supérieur (effet immédiat, au prorata) ou inférieur (effet à la fin de la période payée). | P1 |
| KOM-076 | Le commerçant doit pouvoir résilier son abonnement : il reste actif jusqu'à la fin de la période payée, puis le compte passe en lecture seule et ses données restent exportables. | P1 |
| KOM-077 | Le commerçant doit pouvoir consulter l'historique de ses paiements d'abonnement et télécharger ses reçus ou factures. | P1 |
| KOM-078 | L'état de l'abonnement (essai, actif, en retard, lecture seule, résilié), la date d'échéance et les limites du plan doivent être visibles en permanence pour le propriétaire. | P1 |

### 4.9 Back-office de la plateforme

| ID | Exigence | Priorité |
|---|---|---|
| KOM-080 | L'équipe Komersia doit disposer d'une console listant les commerçants, leur plan, leur statut et leur activité. | P1 |
| KOM-081 | L'équipe Komersia doit pouvoir suspendre et réactiver un commerçant, avec journalisation. | P1 |
| KOM-082 | Le support doit pouvoir consulter le compte d'un commerçant uniquement avec son consentement, et chaque accès doit être audité. | P1 |
| KOM-083 | La console doit afficher les indicateurs de la plateforme : commerçants actifs, MRR, churn, ventes enregistrées, taux de paiements réussis. | P1 |
| KOM-084 | Le système doit permettre d'activer des modules et des fonctions par commerçant (feature flags). | P2 |

## 5. Modules sectoriels

Les modules ne remplacent pas le socle commun : ils l'étendent.

| Module | Fonctionnalités ajoutées | Priorité |
|---|---|---|
| Restaurant | Tables, commandes cuisine, serveurs, recettes, pourboires | P2 |
| Pharmacie | Lots, dates d'expiration, prescriptions, traçabilité | P2 |
| Salon / beauté | Rendez-vous, prestations, employés, commissions | P2 |
| Quincaillerie | Unités multiples, prix détail/gros, devis, livraison | P2 |
| Grossiste | Tarifs clients, tournées, représentants, factures proforma | P2 |
| Livraison | Zones, livreurs, statuts de livraison, frais de transport | P2 |
| École / services | Inscriptions, abonnements, échéanciers, documents | P3 |

## 6. Exigences non fonctionnelles

| ID | Catégorie | Exigence | Priorité |
|---|---|---|---|
| KOM-NF-001 | Performance | Une vente doit s'enregistrer en moins de 2 secondes lorsque la connexion est disponible. | P0 |
| KOM-NF-002 | Performance | La recherche d'un produit doit retourner un résultat en moins de 1 seconde pour 10 000 produits. | P0 |
| KOM-NF-003 | Disponibilité | L'application doit viser une disponibilité mensuelle d'au moins 99,5 %. | P0 |
| KOM-NF-004 | Hors ligne | Les ventes, mouvements de stock et fiches clients doivent pouvoir être créés sans connexion. | P0 |
| KOM-NF-005 | Synchronisation | Les données créées hors ligne doivent se synchroniser automatiquement dès le retour du réseau. | P0 |
| KOM-NF-006 | Sécurité | Toutes les communications doivent utiliser HTTPS/TLS. | P0 |
| KOM-NF-007 | Sécurité | Les mots de passe doivent être stockés avec un algorithme de hachage robuste. | P0 |
| KOM-NF-008 | Sécurité | Aucun utilisateur ne doit accéder aux données d'un autre commerçant. | P0 |
| KOM-NF-009 | Auditabilité | Toute opération sensible doit être journalisée avec l'utilisateur, la date et l'action. | P0 |
| KOM-NF-010 | Compatibilité | L'application doit fonctionner sur Android moderne, navigateurs Chrome et Firefox récents. | P0 |
| KOM-NF-011 | Sauvegarde | Les données doivent être sauvegardées quotidiennement avec une politique de restauration testée. | P0 |
| KOM-NF-012 | Scalabilité | L'architecture doit supporter la croissance vers plusieurs milliers de commerçants sans refonte majeure. | P1 |
| KOM-NF-013 | Appareils | L'application Android doit fonctionner sur Android 8 ou supérieur avec 2 Go de RAM, et rester fluide sur un téléphone d'entrée de gamme. | P0 |
| KOM-NF-014 | Réseau | L'application doit rester utilisable sur une connexion 2G/3G ou à forte latence, avec une synchronisation compressée et reprenable après coupure. | P0 |
| KOM-NF-015 | Hors ligne | Le mode hors ligne doit tenir au moins 7 jours *(hypothèse)* sur une base locale chiffrée. | P0 |
| KOM-NF-016 | Intégrité financière | Aucune vente ne doit être perdue ni dupliquée à la synchronisation (idempotence testée), et les montants doivent être stockés en entiers, jamais en nombres flottants. | P0 |
| KOM-NF-017 | Reprise | La perte de données maximale (RPO) doit être de 15 minutes et le délai de reprise (RTO) de 4 heures *(hypothèse)*, avec un test de restauration chaque trimestre. | P0 |
| KOM-NF-018 | Sécurité | Les données doivent être chiffrées au repos. Les API doivent être protégées contre l'abus (limitation de débit, détection de force brute). Un test d'intrusion doit précéder le lancement. | P0 |
| KOM-NF-019 | Ergonomie | Une vente doit se faire en 3 écrans ou moins. Un nouveau commerçant doit pouvoir faire sa première vente en moins de 15 minutes après l'inscription. | P0 |
| KOM-NF-020 | Internationalisation | Les textes doivent être externalisés pour permettre l'ajout de langues (lingala et swahili envisagés), et les formats de date, de nombre et de devise doivent être configurables. | P1 |
| KOM-NF-021 | Observabilité | Les erreurs, la latence, les échecs de synchronisation et les échecs de paiement doivent être mesurés, avec des alertes pour l'équipe. | P1 |
| KOM-NF-022 | Support | Un canal de support par WhatsApp doit exister, avec un délai de première réponse de 4 heures ouvrées *(hypothèse)*. | P1 |

## 7. Sécurité et multi-tenant

La sécurité multi-tenant est une exigence critique du projet.

- Chaque commerçant est un **tenant** isolé.
- Toutes les tables métier doivent contenir `tenant_id`.
- Le `tenant_id` doit provenir du token d'authentification, jamais d'un paramètre contrôlé par le client.
- PostgreSQL Row-Level Security doit être activée pour renforcer l'isolation des données.
- Les rôles et permissions doivent être appliqués côté serveur.
- Les opérations de suppression, annulation de vente, modification de stock et gestion des utilisateurs doivent être auditées.
- Les clés API, secrets de paiement et identifiants Mobile Money doivent être stockés dans un gestionnaire de secrets, jamais dans le code source.
- Les webhooks entrants (Mobile Money) doivent être authentifiés par signature et traités de façon idempotente.
- Les tests automatisés doivent inclure des scénarios où un utilisateur du tenant A tente de lire ou modifier des données du tenant B (API, requêtes directes, exports, fichiers).
- Les accès du support aux données d'un commerçant doivent être soumis à son consentement et journalisés.

## 8. Architecture technique

### 8.1 Approche

Le projet démarre en **monolithe modulaire** : un seul déploiement, mais des modules clairement séparés. Cette approche facilite le développement initial tout en permettant une évolution vers des services indépendants si nécessaire.

### 8.2 Composants

| Composant | Rôle |
|---|---|
| Application web | Tableau de bord, administration, rapports et gestion |
| Application Android | Caisse, stock, ventes terrain et mode hors ligne |
| API backend | Logique métier, authentification, règles et intégrations |
| Base de données | PostgreSQL avec isolation multi-tenant |
| File de tâches | Notifications, rapports, synchronisation et intégrations |
| Stockage fichiers | Reçus, logos, produits et documents |
| Intégrations | Mobile Money, WhatsApp/SMS, impression et comptabilité |

### 8.3 Principes techniques

- API documentée et versionnée.
- Validation stricte des données côté serveur.
- Tests unitaires, d'intégration et de sécurité.
- Environnements séparés : développement, test et production.
- Déploiement automatisé via GitHub Actions.
- Journalisation centralisée des erreurs et événements.
- Sauvegardes automatiques et plan de restauration.

## 9. Intégrations prioritaires

| Intégration | Objectif | Priorité |
|---|---|---|
| M-Pesa | Encaissement Mobile Money | P0 |
| Airtel Money | Encaissement Mobile Money | P0 |
| Orange Money | Encaissement Mobile Money | P0 |
| Afrimoney | Encaissement Mobile Money | P1 |
| WhatsApp Business | Envoi de reçus, relances et alertes | P1 |
| SMS | Notifications essentielles | P1 |
| Imprimante thermique | Reçus de caisse | P0 |
| Export comptable | Factures, ventes et paiements | P2 |

## 10. Parcours utilisateurs clés

### 10.1 Vente en caisse

1. Le caissier ouvre la caisse.
2. Il recherche un produit par nom ou scanne le code-barres.
3. Il ajoute les produits au panier.
4. Il applique éventuellement une remise.
5. Il choisit le mode de paiement.
6. Le système enregistre la vente, met à jour le stock et génère le reçu.
7. Le commerçant consulte la vente dans ses rapports.

### 10.2 Vente à crédit

1. Le caissier sélectionne un client.
2. Le système affiche son solde et sa limite de crédit.
3. Le caissier enregistre la vente à crédit.
4. Le système crée une dette et un échéancier.
5. Le client paie partiellement ou totalement.
6. Le système met à jour le solde et l'historique.

### 10.3 Réception de stock

1. Le magasinier crée ou sélectionne une commande fournisseur.
2. Il enregistre les produits reçus, les quantités et les prix d'achat.
3. Le système met à jour le stock et le coût d'achat.
4. Le système génère l'historique de réception.
5. Le propriétaire consulte la marge et les produits disponibles.

## 11. Critères d'acceptation du MVP

Le MVP est considéré comme livré lorsque les conditions suivantes sont remplies :

- Un commerçant peut créer un compte et une boutique.
- Un caissier peut vendre un produit et générer un reçu.
- Le stock est automatiquement mis à jour après une vente.
- Un paiement en espèces ou Mobile Money peut être enregistré.
- Une vente à crédit crée une dette visible dans la fiche client.
- Un rapport quotidien affiche les ventes, produits, dettes et stock.
- Un utilisateur ne peut pas accéder aux données d'un autre commerçant.
- Les ventes créées hors ligne se synchronisent correctement.
- Les tests automatisés couvrent les fonctions critiques et l'isolation multi-tenant.
- Une session de caisse peut être ouverte et clôturée, avec calcul de l'écart par devise.
- Un test de synchronisation avec 1 000 ventes créées hors ligne sur plusieurs appareils ne produit ni doublon ni perte.
- Un test d'isolation confirme qu'aucune requête d'un tenant ne retourne de donnée d'un autre tenant.
- Un nouveau commerçant fait sa première vente en moins de 15 minutes, mesuré lors de tests utilisateurs.
- Une restauration de sauvegarde a été réalisée avec succès dans l'environnement de test.
- L'application est testée avec au moins 3 à 5 commerçants pilotes pendant au moins 4 semaines.

## 12. Livrables attendus

- Application web responsive.
- Application Android de caisse et gestion.
- API backend sécurisée.
- Base de données PostgreSQL multi-tenant.
- Documentation produit et technique.
- Guide utilisateur pour commerçants.
- Environnement de démonstration.
- Tests automatisés.
- Plan de déploiement et de sauvegarde.
- Tableau de bord d'administration.

## 13. Hors périmètre du MVP

- Comptabilité complète avec grand livre et états financiers.
- Gestion de paie et ressources humaines avancées.
- Marketplace publique entre commerçants.
- Application iOS native.
- Intelligence artificielle avancée de prévision.
- Intégration fiscale automatisée.
- Gestion de flotte complète.
- E-commerce B2C complet.

## 14. Risques principaux

| Risque | Impact | Mesure de prévention |
|---|---|---|
| Connexion Internet instable | Ventes bloquées | Mode hors ligne et synchronisation |
| Erreur d'isolation multi-tenant | Fuite de données | RLS, tests de sécurité et audits |
| Échec de confirmation Mobile Money | Perte financière | Webhooks, rapprochement et journal des transactions |
| Complexité excessive | Retard du lancement | MVP limité et modules progressifs |
| Faible adoption | Croissance lente | Interface simple, formation et support WhatsApp |
| Dépendance à un opérateur | Risque commercial | Intégration de plusieurs moyens de paiement |
| Conflits de synchronisation (stock, doublons) | Écarts de stock, ventes dupliquées | Identifiants générés côté appareil, idempotence, journal d'opérations, règles de conflit (section 18) |
| Fraude ou erreur en caisse | Perte d'argent | Clôture de caisse, permissions sur remises et annulations, journal d'audit |
| Manipulation de l'heure de l'appareil hors ligne | Données incohérentes | Horodatage appareil et serveur, détection d'écart, plafond de durée hors ligne |
| Exigences réglementaires (paiement, fiscalité, données) | Blocage ou sanction | Agrégateur agréé, validation juridique locale avant lancement (section 20) |
| Prix trop élevé pour la clientèle cible | Faible conversion de l'essai vers l'abonnement | Tarifs testés avec les pilotes, paiement mensuel en petites sommes, essai donnant accès à toute la valeur du produit (section 16) |
| Essai de 14 jours trop court pour un commerçant qui doit saisir catalogue et stock | Abandon avant d'avoir vu la valeur | Import du catalogue (CSV/Excel), assistant de démarrage, accompagnement par WhatsApp pendant l'essai, prolongation possible par le support |
| Paiements mensuels oubliés ou manqués (paiement non automatique) | Churn involontaire, revenu irrégulier | Rappels multiples, paiement en quelques actions, période de grâce, relance par WhatsApp |
| Dépendance d'un seul revenu (l'abonnement) | Revenu fragile si l'adoption est lente | Suivre MRR et conversion dès le pilote, maîtriser les coûts, revoir le modèle si les objectifs ne sont pas atteints |
| Dépendance à un fournisseur de messagerie (WhatsApp/SMS) | Coûts et interruptions | Plafonds par plan, fournisseurs alternatifs, repli par SMS |

## 15. Indicateurs de succès

- Nombre de commerçants actifs par mois.
- Nombre de ventes enregistrées par jour.
- Taux de ventes réalisées hors ligne puis synchronisées.
- Taux de paiements Mobile Money réussis.
- Taux de dettes clients recouvrées.
- Nombre de ruptures de stock évitées.
- Taux de rétention mensuelle des commerçants.
- Note de satisfaction des utilisateurs pilotes.
- Revenu mensuel récurrent (MRR) et revenu moyen par commerçant (ARPU).
- Taux de conversion de l'essai gratuit vers un abonnement payant.
- Part des essais où le commerçant a enregistré au moins 10 ventes avant la fin de l'essai (indicateur d'activation).
- Taux de désabonnement (churn) mensuel.
- Taux de renouvellement mensuel à l'échéance et taux de paiements d'abonnement réussis.
- Coût d'acquisition par commerçant rapporté à la valeur vie client (LTV).
- Part des commerçants actifs 7 jours après l'inscription (activation).

Les cibles chiffrées seront fixées après la phase pilote.

## 16. Modèle économique

### 16.1 Principes

- **L'abonnement est la seule source de revenus de Komersia.** Il n'y a ni commission sur les ventes ou les paiements des commerçants, ni programme de partage de revenus avec des partenaires.
- Le parcours est simple : **achat d'un plan, première souscription, puis paiement mensuel**.
- Prix affichés en USD, payables en CDF ou en USD par Mobile Money.
- **Pas de plan gratuit permanent** : un essai gratuit de 14 jours permet au commerçant de comprendre le logiciel ; s'il est convaincu, il souscrit à un plan payant.
- Le commerçant ne perd jamais ses données : l'export reste toujours possible, et les ventes déjà enregistrées sont toujours synchronisées, même si l'abonnement est en retard ou expiré (KOM-074, KOM-079).
- Les paiements Mobile Money que les commerçants reçoivent de leurs clients ne génèrent aucun revenu pour Komersia. Les frais éventuels de l'opérateur ou de l'agrégateur sont à la charge de celui qui les supporte selon les conditions de l'opérateur, et ne sont pas prélevés par Komersia.

### 16.2 Plans d'abonnement

| Plan | Cible | Contenu indicatif | Prix mensuel *(hypothèse)* |
|---|---|---|---|
| Essentiel | Petit commerce de détail | 1 boutique, 3 utilisateurs, catalogue illimité, caisse hors ligne, clients et crédit, Mobile Money, rapports de base, reçus WhatsApp/SMS limités | 5 USD |
| Pro | Commerce établi | Jusqu'à 3 boutiques, 10 utilisateurs, achats et fournisseurs, factures et devis, exports, rapports avancés | 15 USD |
| Business | Multi-boutiques, grossiste | Plus de boutiques et d'utilisateurs, modules sectoriels, accès API, support prioritaire | 40 USD, plus un supplément par boutique |

Le contenu exact des plans, les limites et les prix sont à valider par des entretiens avec les commerçants pilotes (consentement à payer, capacité de paiement). La facturation est mensuelle uniquement ; un paiement annuel pourra être étudié plus tard.

### 16.3 Cycle de vie de l'abonnement

1. **Essai gratuit de 14 jours.** À l'inscription, le commerçant accède au logiciel sans moyen de paiement, avec les fonctions du plan Pro *(hypothèse)*, pour comprendre la valeur du produit. Des rappels l'informent de la fin de l'essai (KOM-071).
2. **Achat et première souscription.** S'il est convaincu, il choisit un plan, paie par Mobile Money et l'abonnement s'active dès la confirmation du paiement, sans attendre la fin de l'essai. Il reçoit un reçu (KOM-072). La date d'échéance est fixée à un mois après l'activation. S'il n'a pas souscrit à la fin de l'essai, le compte passe en lecture seule (KOM-079).
3. **Paiement mensuel.** Le système rappelle l'échéance à 7 jours, 3 jours et le jour même. Le commerçant paie par Mobile Money, ce qui prolonge l'abonnement d'un mois à compter de l'échéance (KOM-073). Le Mobile Money se prête mal au prélèvement automatique : le paiement reste déclenché par le commerçant.
4. **Retard de paiement.** Période de grâce de 7 jours *(hypothèse)* avec toutes les fonctions et des rappels, puis passage en lecture seule. L'export et la synchronisation des ventes déjà faites restent possibles (KOM-074).
5. **Changement de plan.** Passage au plan supérieur immédiat, au prorata ; passage au plan inférieur à la fin de la période payée (KOM-075).
6. **Résiliation.** L'abonnement reste actif jusqu'à la fin de la période payée, puis le compte passe en lecture seule (KOM-076).

| État | Description | Accès |
|---|---|---|
| Essai | 14 jours gratuits après l'inscription | Fonctions du plan Pro *(hypothèse)* |
| Actif | Abonnement payé et à jour | Toutes les fonctions du plan souscrit |
| En retard | Échéance dépassée, période de grâce en cours | Toutes les fonctions, avec rappels |
| Lecture seule | Essai terminé sans souscription, grâce terminée sans paiement, ou résiliation effective | Consultation, rapports et export ; plus de nouvelle vente ; synchronisation des ventes déjà faites |

### 16.4 Indicateurs économiques

MRR, ARPU, conversion de l'essai vers un abonnement payant, durée moyenne avant souscription, taux de renouvellement à l'échéance, taux de paiements d'abonnement réussis, churn, coût d'acquisition et LTV/CAC (voir section 15).

## 17. Jalons et feuille de route

| Jalon | Objectif | Contenu principal |
|---|---|---|
| M0 — Fondations | Poser le socle technique | Architecture, CI/CD, environnements, base multi-tenant avec RLS, authentification, rôles, audit |
| M1 — Caisse hors ligne | Premier produit utilisable par les pilotes | Catalogue, caisse en espèces, multi-devises, stock, clients et crédit, clôture de caisse, rapports de base, synchronisation hors ligne, reçus imprimables |
| M2 — Paiements et monétisation | MVP commercialisable | Mobile Money via un agrégateur, reçus WhatsApp/SMS, abonnement et essai, back-office, tests de sécurité complets, notifications |
| M3 — Croissance | Étendre l'offre | Achats et fournisseurs, factures et devis, multi-boutiques, inventaire |
| M4 — Modules | Spécialiser par secteur | Modules sectoriels, fidélité, export comptable |

Le MVP commercialisable correspond à M0 + M1 + M2. La phase pilote démarre dès la fin de M1 afin de valider l'ergonomie et le hors ligne avant d'investir dans les paiements.

## 18. Règles métier détaillées

### 18.1 Montants et devises
- Tous les montants sont stockés en entiers, dans la plus petite unité de la devise, avec le code de la devise.
- Chaque boutique a une devise de référence et un taux CDF/USD défini manuellement par le commerçant, historisé (KOM-038). Une source externe pourra être proposée plus tard.
- Chaque vente et chaque paiement enregistrent la devise, le montant, le taux appliqué et la date du taux. Ces valeurs ne sont plus modifiées ensuite.
- Un paiement peut mélanger plusieurs devises et moyens ; le rendu de monnaie se fait dans la devise choisie par le caissier (KOM-039).
- Règles d'arrondi : CDF à l'unité, USD au centime. L'écart d'arrondi est enregistré.

### 18.2 Ventes
- Une vente validée est immuable. Une annulation ou un retour crée une écriture inverse, liée à la vente d'origine, avec justification et auteur. Aucune suppression.
- La numérotation des reçus est séquentielle par appareil et par boutique, avec un préfixe d'appareil pour éviter les collisions hors ligne.
- Les taxes (TVA) sont configurables par boutique, avec prix saisis hors taxe ou toutes taxes comprises.

### 18.3 Crédit client
- Chaque client a une limite de crédit. Un dépassement exige l'autorisation d'un rôle habilité (KOM-053).
- Une dette reste dans la devise de la vente d'origine ; le commerçant choisit s'il la réévalue.
- Par défaut, un paiement s'impute à la dette la plus ancienne.

### 18.4 Stock
- Le coût d'achat est calculé en coût moyen pondéré.
- La vente d'un produit sans stock suffisant est autorisée ou refusée selon un réglage par boutique. Une vente déjà faite hors ligne n'est jamais refusée à la synchronisation : le stock peut devenir négatif et une alerte est émise.
- Un transfert entre boutiques se fait en deux étapes : expédié, puis reçu.

### 18.5 Hors ligne et synchronisation
- Les identifiants (UUID) sont générés sur l'appareil, ce qui rend la synchronisation idempotente.
- L'appareil tient un journal d'opérations rejoué vers le serveur dans l'ordre. Une opération déjà reçue est ignorée.
- Les ventes et paiements ne sont jamais écrasés. Pour les fiches (client, produit), la dernière modification gagne, sauf sur les champs sensibles (prix d'achat, limite de crédit) qui sont signalés pour validation.
- L'horodatage de l'appareil et celui du serveur sont tous deux conservés ; un écart important est signalé.
- Au-delà de la durée maximale hors ligne (KOM-NF-015), l'application demande une synchronisation avant de continuer.

### 18.6 Paiements Mobile Money
- Statuts : initié, en attente, confirmé, échoué, expiré, remboursé.
- Un webhook est vérifié par signature et traité de façon idempotente. En l'absence de webhook, le système interroge l'agrégateur après un délai.
- Un rapprochement quotidien compare les paiements enregistrés au relevé de l'agrégateur, et signale les écarts.
- Une vente payée par Mobile Money n'est validée qu'à la confirmation du paiement, sauf règle contraire du commerçant.

## 19. Matrice des permissions

✔ = autorisé · — = interdit · seuil = autorisé jusqu'à un seuil fixé par le propriétaire.

| Action | Propriétaire | Gérant | Caissier | Magasinier | Comptable |
|---|---|---|---|---|---|
| Enregistrer une vente | ✔ | ✔ | ✔ | — | — |
| Appliquer une remise | ✔ | ✔ | seuil | — | — |
| Annuler ou rembourser une vente | ✔ | ✔ | — | — | — |
| Modifier un prix de vente | ✔ | ✔ | — | — | — |
| Voir les prix d'achat et les marges | ✔ | ✔ | — | ✔ | ✔ |
| Ajuster le stock | ✔ | ✔ | — | ✔ | — |
| Faire un inventaire et réceptionner | ✔ | ✔ | — | ✔ | — |
| Gérer les clients | ✔ | ✔ | ✔ | — | — |
| Modifier une limite de crédit | ✔ | ✔ | — | — | — |
| Clôturer une caisse | ✔ | ✔ | ✔ (sa session) | — | — |
| Voir les rapports complets | ✔ | ✔ | — | — | ✔ |
| Exporter les données | ✔ | ✔ | — | — | ✔ |
| Gérer les utilisateurs | ✔ | — | — | — | — |
| Gérer l'abonnement et la facturation | ✔ | — | — | — | — |

## 20. Conformité et cadre réglementaire

Les points suivants sont à valider avec un conseil juridique local avant le lancement commercial.

| Domaine | Point d'attention |
|---|---|
| Paiements | Le traitement des fonds relève d'acteurs agréés par la Banque centrale du Congo. Komersia doit passer par un agrégateur ou un opérateur agréé, y compris pour encaisser les abonnements, et ne pas détenir les fonds des commerçants. |
| Fiscalité | Reçus, factures et TVA doivent respecter les règles de l'administration fiscale congolaise (taux de TVA en vigueur à vérifier). L'intégration fiscale automatisée est hors périmètre du MVP. |
| Données personnelles | Respecter le cadre congolais applicable (notamment le Code du numérique de 2023, à vérifier) : information et consentement, droit d'accès et de suppression, durée de conservation, localisation de l'hébergement. |
| Messagerie | Obtenir le consentement des clients avant les messages WhatsApp ou SMS, et permettre de s'y opposer. |
| Contrats | Rédiger les conditions générales d'utilisation, la politique de confidentialité et les conditions de vente de l'abonnement (renouvellement, retard, résiliation, remboursement). |
| Conservation | Définir la durée de conservation des ventes, paiements et journaux d'audit. |

## 21. Glossaire

| Terme | Définition |
|---|---|
| Tenant | Un commerçant (entreprise) isolé des autres dans la plateforme |
| Boutique | Point de vente rattaché à un tenant |
| Session de caisse | Période entre l'ouverture et la clôture d'une caisse par un caissier |
| Agrégateur | Prestataire agréé qui centralise les paiements Mobile Money de plusieurs opérateurs |
| RLS | Row-Level Security : règle de la base de données qui limite l'accès ligne par ligne |
| Idempotence | Propriété d'une opération qui, rejouée, ne produit pas d'effet en double |
| MRR | Revenu mensuel récurrent |

## 22. Hypothèses et décisions ouvertes

| Sujet | Options | Recommandation | Statut |
|---|---|---|---|
| Application Android | Native (Kotlin) ou multiplateforme (Flutter, React Native) | Multiplateforme si l'équipe est réduite ; native si une compétence Android solide existe. Valider par un prototype de la caisse hors ligne. | À décider |
| Stack backend | TypeScript, Python ou autre, sur PostgreSQL | Choisir selon la compétence de l'équipe ; l'architecture visée est un monolithe modulaire. | À décider |
| Synchronisation hors ligne | Solution du marché ou développement sur mesure | Mener une étude technique courte sur un cas simple (ventes et stock) avant de choisir. | À décider |
| Mobile Money | Agrégateur unique ou intégrations directes | Commencer par un agrégateur agréé couvrant la RDC ; intégrations directes plus tard si le volume le justifie. | À décider |
| Hébergement | Région cloud proche de la RDC ou en Europe | Retenir la région offrant la meilleure latence tout en respectant la section 20. | À décider |
| Imprimante thermique | Bluetooth, USB ou impression via application tierce | Privilégier Bluetooth avec le protocole ESC/POS, testé sur les modèles courants du marché. | À décider |
| Taux de change | Manuel ou source externe | Manuel et historisé pour le MVP. | Proposé |
| Prix et plans | Voir section 16.2 | Valider par entretiens avec les pilotes. | À valider |
| Modèle économique | Abonnement uniquement | Abonnement mensuel, sans commission ni partage de revenus. | Décidé |

## 23. Découpage prévu en issues GitHub

| Épic | Jalon | Exigences principales |
|---|---|---|
| Architecture et socle technique | M0 | Section 8, KOM-NF-003, KOM-NF-021 |
| Base de données multi-tenant | M0 | Section 7, KOM-006, KOM-NF-008 |
| Authentification, rôles et permissions | M0 | KOM-001 à KOM-009, matrice section 19 |
| Journal d'audit | M0 | KOM-027, KOM-NF-009 |
| Catalogue produits et services | M1 | KOM-010, KOM-011, KOM-013 |
| Caisse, reçus et session de caisse | M1 | KOM-020 à KOM-025, KOM-028 |
| Multi-devises et taux de change | M1 | KOM-036 à KOM-039, section 18.1 |
| Stock | M1 | KOM-040 à KOM-043, section 18.4 |
| Clients et crédit | M1 | KOM-050 à KOM-053, section 18.3 |
| Hors ligne et synchronisation | M1 | KOM-NF-004, KOM-NF-005, KOM-NF-015, KOM-NF-016, section 18.5 |
| Rapports de base | M1 | KOM-060 à KOM-062, KOM-065 |
| Paiements Mobile Money | M2 | KOM-031, KOM-034, KOM-035, section 18.6 |
| Messagerie WhatsApp et SMS | M2 | KOM-024, KOM-054 |
| Abonnement : achat, première souscription et paiement mensuel | M2 | KOM-070 à KOM-079 |
| Back-office plateforme | M2 | KOM-080 à KOM-083 |
| Tests de sécurité et d'isolation | M2 | KOM-NF-006 à KOM-NF-009, KOM-NF-018 |
| Achats et fournisseurs, inventaire | M3 | KOM-044 à KOM-046 |
| Factures, devis, retours | M3 | KOM-026, KOM-064 |
| Multi-boutiques | M3 | KOM-003, KOM-063 |
| Modules sectoriels et fidélité | M4 | Section 5, KOM-055 |
