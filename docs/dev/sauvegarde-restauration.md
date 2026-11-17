# Sauvegarde et restauration PostgreSQL

Exigences : KOM-NF-011 (sauvegarde quotidienne, restauration testée) et KOM-NF-017 (RPO 15 minutes, RTO 4 heures
*(hypothèse)*, test de restauration chaque trimestre). Scripts : `scripts/backup.sh` et `scripts/restore.sh`.

## Objectifs

| Indicateur | Cible | Ce que couvrent les scripts |
|---|---|---|
| RPO (perte de données maximale) | 15 minutes | **Non atteint par `backup.sh` seul** : une sauvegarde logique quotidienne donne un RPO de 24 h. Le RPO de 15 min exige l'archivage continu des journaux WAL (`archive_command` ou pgBackRest/WAL-G) avec restauration à un instant donné (PITR). À mettre en place avec l'hébergement de production (section 22 du cahier des charges, encore ouverte). |
| RTO (délai de reprise) | 4 heures | La restauration logique d'une base de taille MVP prend quelques minutes ; à mesurer à chaque test trimestriel et à comparer à la cible. |
| Fréquence de sauvegarde | quotidienne (KOM-NF-011) | `backup.sh` planifié par cron ou par l'orchestrateur. |
| Test de restauration | trimestriel (KOM-NF-017) | Procédure ci-dessous. |

## Rôle de sauvegarde (point important)

Les tables métier utilisent `FORCE ROW LEVEL SECURITY` : la politique d'isolation s'applique **même au propriétaire**
`komersia_owner`. Avec ce rôle, `pg_dump` échoue (« query would be affected by row-level security policy »).
La sauvegarde utilise donc un rôle dédié `komersia_backup` : lecture seule (`pg_read_all_data`) avec l'attribut
`BYPASSRLS`. Il est créé par `scripts/db-init.sql` (Docker : automatiquement ; hors Docker : relancer le script en
super-utilisateur, il est idempotent). Un super-utilisateur peut aussi être utilisé.

## Sauvegarder

```bash
BACKUP_DB_PASSWORD=<mot_de_passe> BACKUP_DIR=/var/backups/komersia BACKUP_RETENTION_DAYS=14 ./scripts/backup.sh
```

Produit `komersia_<base>_<AAAAMMJJTHHMMSSZ>.dump` (format custom compressé, horodatage UTC, permissions 600),
vérifié par `pg_restore --list`, puis supprime les sauvegardes du même préfixe plus anciennes que
`BACKUP_RETENTION_DAYS` (0 = aucune rotation). Un échec ne laisse aucun fichier partiel.

| Variable | Défaut | Rôle |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME` | `localhost`, `5432`, `komersia` | Base à sauvegarder (`DB_HOST` vide = socket Unix) |
| `BACKUP_DB_USER`, `BACKUP_DB_PASSWORD` | `komersia_backup`, (vide) | Rôle de sauvegarde |
| `BACKUP_DIR` | `./backups` | Destination |
| `BACKUP_RETENTION_DAYS` | `14` | Rotation |
| `BACKUP_PREFIX` | `komersia` | Préfixe des fichiers |

Planification quotidienne (exemple cron, 02:00) :

```cron
0 2 * * * cd /opt/komersia && set -a && . ./.env && set +a && ./scripts/backup.sh >> /var/log/komersia-backup.log 2>&1
```

Sous Docker Compose : `docker compose exec -T db pg_dump ...` n'est pas nécessaire ; lancer le script depuis l'hôte avec
`DB_PORT=5433` (port publié) ou `docker compose run`/cron dans un conteneur disposant de `pg_dump` 16.

**Les sauvegardes doivent être copiées hors du serveur** (autre site ou stockage objet, chiffré au repos : KOM-NF-018).
Ce dépôt ne fournit pas encore cette copie : à ajouter avec le choix d'hébergement.

## Restaurer

```bash
RESTORE_DB_PASSWORD=<mot_de_passe_proprietaire> ./scripts/restore.sh backups/komersia_komersia_20261117T020000Z.dump komersia_restore
```

Le script crée la base cible (propriétaire `komersia_owner`, qui doit avoir `CREATEDB`), restaure avec
`pg_restore --exit-on-error`, puis rétablit les droits du rôle applicatif `komersia_app`. Les politiques RLS font partie
de la sauvegarde. Garde-fous : refus si la base cible existe (sauf `RESTORE_DROP_EXISTING=1`), refus de cibler la base
`komersia` (sauf `RESTORE_ALLOW_PRIMARY=1`), suppression automatique d'une base cible incomplète en cas d'échec.

### Reprise après sinistre (remplacer la base de production)

1. Arrêter `web` (`docker compose stop web`) pour éviter les écritures.
2. Restaurer vers une base temporaire (`komersia_restore`) et vérifier (ci-dessous).
3. Si nécessaire, renommer : `ALTER DATABASE komersia RENAME TO komersia_avant_incident;` puis
   `ALTER DATABASE komersia_restore RENAME TO komersia;` (en super-utilisateur, sans connexion ouverte).
4. Relancer `web`, vérifier `/api/docs/` et une connexion applicative.

## Test de restauration trimestriel (KOM-NF-017)

À faire chaque trimestre (janvier, avril, juillet, octobre), par une personne désignée, et à consigner (date, durée,
résultat) dans le registre d'exploitation :

1. Prendre la **dernière sauvegarde de production** (pas une sauvegarde fraîche de développement).
2. Chronométrer : `time ./scripts/restore.sh <fichier> komersia_restore_test` sur un serveur distinct de la production.
3. Comparer le nombre de lignes des tables clés entre la source et la copie, par exemple :
   ```sql
   SELECT 'accounts_tenant', count(*) FROM accounts_tenant UNION ALL
   SELECT 'django_migrations', count(*) FROM django_migrations UNION ALL
   SELECT 'auth_permission', count(*) FROM auth_permission;   -- en tant que komersia_backup (BYPASSRLS)
   ```
   (compléter avec les tables de ventes et de stock, dès qu'elles contiennent des données.)
4. Vérifier que les politiques RLS sont présentes : `SELECT count(*) FROM pg_policies;` identique à la source, et que le
   rôle applicatif sans contexte de tenant ne voit aucune ligne.
5. Démarrer l'application sur la copie (`DB_NAME=komersia_restore_test`) et exécuter un scénario de connexion et de vente.
6. Comparer la durée mesurée au RTO de 4 heures ; noter l'écart avec le RPO réel (âge de la sauvegarde utilisée).
7. Supprimer la base de test : `DROP DATABASE komersia_restore_test;`.

Tout écart (lignes manquantes, erreur de restauration, durée > RTO) ouvre un incident à traiter avant le test suivant.

## Vérification effectuée (développement)

Le 2026-11-17 sur PostgreSQL 16.15 local : sauvegarde de `komersia` avec `komersia_backup` (45 Ko), restauration vers
`komersia_restore_test`, comptes identiques (`auth_permission` 44, `django_content_type` 11, `django_migrations` 23,
`accounts_tenant` 0, 1 politique RLS, 1 table `FORCE RLS`), droits du rôle applicatif fonctionnels, base de test supprimée.
La base de développement ne contenait presque pas de données métier : ce test valide la mécanique, pas les volumes ni
la durée réelle de restauration.
