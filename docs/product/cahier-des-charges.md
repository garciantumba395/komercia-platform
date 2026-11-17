# Cahier des charges — Komersia

> Version 1.0 — base de référence du dépôt `komersia-platform`.
> Les exigences sont identifiées par un code, priorisées (P0 à P3) et rédigées de façon testable.

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

Le MVP doit permettre à un commerçant de démarrer et gérer son activité de manière autonome.

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

## 7. Sécurité et multi-tenant

La sécurité multi-tenant est une exigence critique du projet.

- Chaque commerçant est un **tenant** isolé.
- Toutes les tables métier doivent contenir `tenant_id`.
- Le `tenant_id` doit provenir du token d'authentification, jamais d'un paramètre contrôlé par le client.
- PostgreSQL Row-Level Security doit être activée pour renforcer l'isolation des données.
- Les rôles et permissions doivent être appliqués côté serveur.
- Les opérations de suppression, annulation de vente, modification de stock et gestion des utilisateurs doivent être auditées.
- Les clés API, secrets de paiement et identifiants Mobile Money doivent être stockés dans un gestionnaire de secrets, jamais dans le code source.

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
- L'application est testée avec au moins 3 à 5 commerçants pilotes.

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

## 15. Indicateurs de succès

- Nombre de commerçants actifs par mois.
- Nombre de ventes enregistrées par jour.
- Taux de ventes réalisées hors ligne puis synchronisées.
- Taux de paiements Mobile Money réussis.
- Taux de dettes clients recouvrées.
- Nombre de ruptures de stock évitées.
- Taux de rétention mensuelle des commerçants.
- Note de satisfaction des utilisateurs pilotes.

## 16. Découpage prévu en issues GitHub

Architecture, base de données multi-tenant, authentification et rôles, catalogue, caisse/POS, stock, paiements Mobile Money, mode hors ligne, rapports, tests de sécurité.
