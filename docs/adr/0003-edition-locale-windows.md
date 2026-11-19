# ADR 0003 — Édition locale Windows en premier

- **Statut :** Accepté
- **Date :** 2026-11-19
- **Remplace en pratique :** l'ADR 0002 (déploiement en ligne), repoussé à plus tard
- **Références :** `docs/product/cahier-des-charges.md` (sections 8, 9, 16, 20), ADR 0001

## Contexte

Le propriétaire du produit a pris trois décisions :
1. Les paiements Mobile Money sont mis de côté. En boutique, le paiement se fait en espèces, en francs congolais (CDF) et en dollars américains (USD).
2. Dans un premier temps, l'application n'est pas hébergée en ligne : elle est installée en local, avec un fichier d'installation (.exe) qui fait tout tourner sur le PC de la boutique.
3. Réponses aux questions de cadrage : un PC principal avec d'autres appareils connectés par le réseau de la boutique ; abonnement par clé de licence mensuelle saisie à la main ; sauvegarde automatique quotidienne sur un disque ou une clé USB ; Windows 10 et 11 en 64 bits uniquement.

## Décision

L'**édition locale** est la première édition livrée. Le code reste le même (Django, PostgreSQL) ; seul l'emballage et quelques règles changent.

| Sujet | Décision |
|---|---|
| Installation | Un installateur Windows `Komersia-Setup-<version>.exe`, construit par la CI sur un runner Windows. Il installe l'application, une base PostgreSQL embarquée et un serveur web, et les lance au démarrage du PC. |
| Base de données | PostgreSQL embarqué, écoutant sur 127.0.0.1 uniquement. On garde PostgreSQL (et non SQLite) pour conserver les mêmes migrations, l'isolation par Row-Level Security et le journal d'audit en ajout seul : un seul code, deux éditions. |
| Serveur web | Waitress (gunicorn ne fonctionne pas sous Windows), écoute sur le réseau local pour les autres appareils de la boutique. |
| Une entreprise par installation | `KOMERSIA_SINGLE_TENANT` : une fois la boutique configurée, l'inscription d'une autre entreprise est refusée. |
| Autres appareils | PC et téléphones se connectent par un navigateur à l'adresse du PC principal. Pas d'application Android nécessaire dans un premier temps : le serveur est sur le réseau local, donc pas de besoin de mode hors ligne ni de synchronisation. |
| Impression | Reçus HTML imprimés par le navigateur sur l'imprimante installée dans Windows (une imprimante thermique USB installée comme imprimante Windows fonctionne). L'impression directe ESC/POS et Bluetooth viendront plus tard. |
| Abonnement | Clés de licence signées (Ed25519), liées à l'identifiant de l'installation, valables un nombre de mois, vérifiées hors ligne. Mêmes règles qu'avant : essai de 14 jours, grâce de 7 jours, lecture seule. Voir `docs/dev/licences.md`. |
| Paiement en ligne | Désactivé (`BILLING_PROVIDER` vide). Le paiement de l'abonnement se fait hors application, auprès d'un agent Komersia. |
| Sauvegarde | Sauvegarde automatique quotidienne vers un dossier choisi (disque ou clé USB), vérifiée, avec rotation ; restauration par une procédure de maintenance. Voir `docs/dev/sauvegardes-locales.md`. |
| Console d'exploitation `/ops/` | Absente de l'édition locale (aucun serveur central ne voit les installations). Elle reste dans le code pour une future édition en ligne. |

## Conséquences

**Positives**
- Aucun coût d'hébergement ni de dépendance à un agrégateur pour démarrer.
- Le logiciel fonctionne sans Internet, ce qui correspond à la réalité de la cible.
- Le même code servira plus tard à l'édition en ligne.

**Négatives et risques**
- **Support et mises à jour :** chaque installation est autonome ; il faut un processus de mise à jour et d'assistance. Une panne du PC principal arrête la boutique.
- **Données sur un seul PC :** tout repose sur la sauvegarde. Une clé USB débranchée n'est pas sauvegardée ; l'application alerte quand la dernière sauvegarde est ancienne.
- **Licence :** la vérification côté client est une dissuasion, pas une protection absolue. Une clé est liée à un PC : un changement de PC demande une nouvelle clé.
- **Réseau local en HTTP :** les mots de passe circulent sans chiffrement sur le réseau de la boutique. Acceptable pour un réseau privé de confiance ; HTTPS local est à étudier. Le pare-feu n'ouvre le port que pour le profil réseau privé.
- **Pas de vision centrale :** sans serveur Komersia, impossible de suivre le nombre de commerçants actifs ou le revenu récurrent automatiquement. Les indicateurs économiques sont à tenir à la main (registre des clés émises) jusqu'à l'édition en ligne.
- **Test de l'installateur :** il ne peut être vérifié que sur un vrai Windows (CI) et par le propriétaire du produit sur un PC réel.

## Ce qui est repoussé

- Paiements Mobile Money et agrégateur (issue #14).
- Hébergement en ligne et déploiement cloud (issue #15), console `/ops/` en production.
- Application Android et prototype Flutter (issue #13) : un navigateur sur le réseau local suffit au départ.
- Messagerie WhatsApp et SMS.
