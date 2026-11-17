# ADR 0001 — Choix de la stack technique

- **Statut :** Accepté
- **Date :** 2026-11-17
- **Références :** `docs/product/cahier-des-charges.md` (sections 7, 8, 18, 22)

## Contexte

Komersia est un SaaS multi-tenant de gestion commerciale pour la RDC. Les contraintes qui pèsent le plus sur la technique sont :
- l'isolation stricte des données entre commerçants (KOM-NF-008) ;
- le fonctionnement hors ligne pendant au moins 7 jours, sans perte ni doublon à la synchronisation (KOM-NF-015, KOM-NF-016) ;
- des téléphones Android d'entrée de gamme (Android 8, 2 Go de RAM) et des connexions 2G/3G (KOM-NF-013, KOM-NF-014) ;
- l'impression de reçus sur imprimante thermique Bluetooth ;
- une petite équipe, qui doit livrer vite un monolithe modulaire.

## Décision

| Couche | Choix |
|---|---|
| Backend | Python, **Django 5** et **Django REST Framework**, en monolithe modulaire (une application Django par module métier) |
| Documentation d'API | drf-spectacular (OpenAPI), API versionnée |
| Base de données | **PostgreSQL** avec Row-Level Security |
| Tâches asynchrones | Celery et Redis |
| Application Android | **Flutter** (Dart) avec base locale SQLite chiffrée |
| Synchronisation | Journal d'opérations sur mesure (endpoints `push` et `pull`), conforme à la section 18.5 du cahier des charges |
| Tableau de bord web | Pages Django avec HTMX pour le MVP |
| Qualité | pytest-django, ruff, mypy, Docker, GitHub Actions avec PostgreSQL |

## Justification

- Django fournit l'authentification, les migrations, l'ORM, l'internationalisation et une console d'administration qui couvre l'essentiel du back-office (KOM-080 à KOM-083). L'effort de l'équipe se porte sur la complexité métier plutôt que sur l'assemblage d'outils.
- FastAPI a été écarté : son avantage de débit n'est pas notre goulot d'étranglement, et il faudrait assembler soi-même l'authentification, les migrations et l'administration.
- PostgreSQL avec Row-Level Security ajoute une protection au niveau de la base : même si le code oublie un filtre, la base refuse les lignes d'un autre tenant.
- Flutter donne une seule base de code Android avec de bonnes performances sur matériel modeste.
- Une application web seule (PWA) est écartée : le hors ligne sur plusieurs jours et l'impression Bluetooth y sont trop fragiles.
- La synchronisation sur mesure est retenue par défaut parce que les règles de conflit (ventes jamais écrasées, idempotence, stock négatif toléré) sont spécifiques. Une solution du marché reste évaluée dans l'étude technique de l'épic « Hors ligne et synchronisation ».

## Règles d'implémentation

1. Le `tenant_id` est posé en début de transaction (`SET LOCAL`) par un middleware, à partir du token d'authentification, jamais d'un paramètre client.
2. L'application se connecte avec un rôle de base de données qui **n'est pas propriétaire** des tables, sinon la Row-Level Security est contournée. Un test automatisé le vérifie.
3. Les montants sont stockés en entiers dans la plus petite unité de la devise. Les conversions utilisent `Decimal` avec un arrondi explicite. Aucun nombre flottant.
4. Les webhooks Mobile Money sont vérifiés par signature et traités de façon idempotente.
5. Les tests d'isolation entre tenants bloquent le merge en cas d'échec.
6. La recherche de produits s'appuie sur des index PostgreSQL adaptés (trigrammes ou recherche plein texte) pour tenir moins d'une seconde sur 10 000 produits.

## Conséquences

- **Positives :** démarrage rapide, back-office quasi gratuit, isolation renforcée, un seul langage côté serveur.
- **Négatives :** deux langages dans l'équipe (Python et Dart), et un risque technique sur l'impression Bluetooth et le hors ligne longue durée.

## Garde-fou : prototype Flutter

Avant d'engager le développement de l'application Android, un prototype de une à deux semaines valide les trois points les plus risqués : vente hors ligne, impression Bluetooth sur une imprimante courante du marché, synchronisation.
**Critère de repli :** si le prototype échoue, l'application passe en Kotlin natif. Le backend n'est pas modifié.

## Questions encore ouvertes

- Hébergement et région cloud (voir section 22 du cahier des charges).
- Agrégateur Mobile Money.
- Modèle d'imprimante thermique de référence.
