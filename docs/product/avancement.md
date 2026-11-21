# Avancement du projet Komersia

*Mis à jour le 19 novembre 2026. Référence : `cahier-des-charges.md` (jalons section 17).*

## Vue d'ensemble

| Jalon | Contenu | État |
|---|---|---|
| M0 — Fondations | Architecture, multi-tenant, authentification, audit | Fait (hors hébergement) |
| M1 — Caisse hors ligne | Catalogue, caisse, devises, stock, crédit, rapports, synchronisation | Fait côté serveur et web ; application Android non commencée |
| M2 — Paiements et monétisation | Abonnement, back-office, Mobile Money, WhatsApp/SMS | Abonnement et back-office faits ; Mobile Money et messagerie à faire |
| M3 — Croissance | Achats, factures, multi-boutiques | Non commencé |
| M4 — Modules | Modules sectoriels, fidélité | Non commencé |

## Issues M0 et M1

| Issue | Sujet | État |
|---|---|---|
| #2 | Architecture et socle technique | Fait, sauf déploiement en test (hébergeur non choisi) |
| #3 | Base de données multi-tenant (RLS) | Fait, tests d'isolation en CI |
| #4 | Authentification, rôles, permissions | Fait (double authentification du propriétaire : à faire) |
| #5 | Journal d'audit | Fait, branché sur les opérations sensibles |
| #6 | Catalogue | Fait (variantes : à faire) |
| #7 | Caisse, reçus, session de caisse | Fait côté serveur et web ; impression Bluetooth non testée |
| #8 | Multi-devises et taux de change | Fait |
| #9 | Stock | Fait (transferts et inventaire : M3) |
| #10 | Clients et crédit | Fait (rappels WhatsApp/SMS : M2) |
| #11 | Hors ligne et synchronisation | Serveur fait (pull, push, idempotence) ; application Android à faire |
| #12 | Rapports de base | Fait (export PDF et Excel natif : à faire) |
| #13 | Prototype Flutter | Non commencé (demande un téléphone et une imprimante) |

## M2 : ce qui est fait

- **Abonnement** : essai de 14 jours créé à l'inscription, première souscription, paiement mensuel, période de grâce de 7 jours, lecture seule, changement de plan avec aperçu du montant, résiliation, rappels, reçus. Fournisseur de paiement de test uniquement.
- **Page d'abonnement** dans l'interface web et bandeau d'état.
- **Back-office `/ops/`** : liste des entreprises, suspension et réactivation, accès support avec consentement du propriétaire, indicateurs (MRR, conversion, désabonnement), drapeaux de fonctions.

## Ce qui reste, par priorité

Issues ouvertes : #14 (Mobile Money par agrégateur, reporté), #15 (hébergement en ligne, reporté), #16 (remboursement d'une vente à crédit), #13 (prototype Flutter), #17 (édition locale : installateur, licences, sauvegardes).

Tickets ajoutés le 21 novembre 2026 (cahier des charges d'architecture v1.0), qui complètent les issues M1 existantes sans les dupliquer : #18 (stock sérialisé par IMEI / numéro de série), #19 (Mobile Money manuel : référence et rapprochement à la clôture), #20 (multi-terminaux : identifiant d'appareil et reçus préfixés par caisse).

| Sujet | Bloqué par |
|---|---|
| Agrégateur Mobile Money réel | Choix de l'agrégateur |
| Hébergement et déploiement en test | Choix de l'hébergeur, budget |
| Application Android (prototype Flutter, puis développement) | Téléphone d'entrée de gamme et imprimante thermique pour les tests |
| Remboursement d'une vente à crédit partiellement payée | Choix du mode de remboursement |
| Durcissement avant production (cache partagé, double authentification de la console, secrets) | Hébergement |
| Messagerie WhatsApp et SMS | Choix du fournisseur |
| Facturation du supplément par boutique du plan Business | Décision sur les prix |
| Validation des prix et du contenu des plans avec les commerçants pilotes | Phase pilote |

## Points de vigilance connus

- Les prix des plans, la durée de grâce et le plafond de remise du caissier sont des hypothèses à valider.
- La console d'exploitation s'appuie sur un rôle de base de données qui peut lire les ventes de toutes les entreprises ; le consentement du propriétaire est appliqué par l'application. Voir `docs/dev/console-exploitation.md`.
- L'image Docker n'a pas pu être construite dans l'environnement de développement (démon indisponible) : à vérifier en CI ou sur une machine avec Docker.
- La conformité (paiement, fiscalité, données personnelles) reste à faire valider par un juriste local avant le lancement commercial (cahier des charges, section 20).
