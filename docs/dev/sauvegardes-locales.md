# Sauvegardes locales (édition installée sur le PC principal)

Application Django `backups` (`backend/backups/`). Équivalent applicatif, multiplateforme (Windows 10/11), de
`scripts/backup.sh` et `scripts/restore.sh` (voir `sauvegarde-restauration.md` pour la version serveur/Linux).
Disponible seulement avec `KOMERSIA_EDITION=local` : sinon l'API répond 404 et la page affiche un message.

## Fonctionnement

- Une sauvegarde est un `pg_dump` au format personnalisé compressé, nommé `komersia-AAAAMMJJ-HHMMSS.dump`
  (heure de Kinshasa), écrit dans un fichier temporaire du dossier de destination puis renommé de façon atomique.
  Aucun écrasement (suffixe `-1`, `-2` si besoin). Un fichier `.dump.sha256` voisin porte la somme SHA-256.
- Contrôle immédiat : `pg_restore --list` doit lire l'archive et y trouver des données ; sinon la sauvegarde est
  supprimée et l'échec enregistré avec un message clair. Un échec ne laisse jamais de fichier partiel.
- Rotation : on garde les N dernières (30 par défaut, réglable de 1 à 365) ; la sauvegarde valide qu'on vient de créer
  n'est jamais supprimée. Les sauvegardes « avant restauration » (`komersia-avant-restauration-*.dump`) ne sont jamais
  supprimées automatiquement.
- Une seule opération à la fois (verrou consultatif PostgreSQL, valable entre l'interface web et le planificateur).
- Journal : table `BackupRecord` ; réglages : `BackupSettings` (singleton). Les deux sont SANS RLS (installation
  entière, pas un commerçant), voir l'en-tête de `models.py`. Audit : `backup.run`, `backup.settings_change`.

## Planification

`manage.py backup_tick`, à appeler **toutes les heures** par le planificateur du lanceur. Idempotent. Il crée une
sauvegarde si aucune n'existe ou si la dernière réussie date de plus de `interval_hours` (24 par défaut), sans passer
avant l'heure préférée (2 h par défaut) sauf retard de plus de 12 h. Dossier introuvable (clé USB débranchée) : l'état
« dossier introuvable » est enregistré (une seule ligne, avec compteur de tentatives) et la sauvegarde est refaite au
passage suivant dès que le dossier revient. Le planificateur n'écrit pas d'entrée d'audit (pas de tenant).

## Rôle de sauvegarde : pourquoi

Les tables métier sont sous Row-Level Security **forcée**. Avec un rôle ordinaire (`komersia_app`, `komersia_owner`),
`pg_dump` échoue ou ne verrait aucune ligne. Il faut un rôle `BYPASSRLS` ou super-utilisateur : `komersia_backup`,
créé par `scripts/db-init.sql` (lecture seule + `BYPASSRLS`). L'application refuse tôt un rôle inadapté avec un message
explicite.

| Variable | Rôle |
|---|---|
| `KOMERSIA_PG_BIN` | dossier de `pg_dump`/`pg_restore` (sinon PATH) |
| `DB_BACKUP_USER`, `DB_BACKUP_PASSWORD` | rôle de sauvegarde (sinon base par défaut : ne convient que si super-utilisateur) |
| `DB_RESTORE_USER`, `DB_RESTORE_PASSWORD` | rôle de restauration : propriétaire des tables ou super-utilisateur (sinon base par défaut) |
| `KOMERSIA_BACKUP_DIR` | dossier proposé à la première utilisation |
| `KOMERSIA_DATA_DIR` | dossier des données, interdit comme destination |

## Dossier recommandé

Un disque ou une clé USB **différents** de celui où Komersia est installé, par exemple `D:\Sauvegardes` ou
`E:\Komersia`. L'API refuse : chemins relatifs, tout chemin contenant `..`, dossiers inexistants ou non inscriptibles,
et tout dossier dans l'installation ou le dossier des données (liens symboliques résolus). Propriétaire seul pour
modifier ; propriétaire et gérant peuvent lancer une sauvegarde. Ces routes restent utilisables en lecture seule
d'abonnement (`/api/v1/backups/` est exempté dans `billing/permissions.py`).

## Restauration pas à pas

La restauration est une opération de **maintenance** : le serveur web ne la lance jamais.

1. Fermer Komersia ; brancher le disque ou la clé contenant les sauvegardes.
2. Menu Démarrer > Komersia > « Restaurer une sauvegarde » (le lanceur appelle la commande ci-dessous), ou :
   `python manage.py restore_backup <fichier.dump> --yes` (`--check-only` vérifie sans restaurer).
3. La commande : vérifie la somme SHA-256 (fichier `.sha256` ou journal), la lisibilité (`pg_restore --list`) et la
   version de schéma ; refuse un fichier plus récent que l'application ; fait une **sauvegarde de sécurité** de l'état
   actuel (dossier configuré, sinon dossier du fichier) et s'arrête si elle échoue ; ferme les autres connexions ;
   restaure avec `pg_restore --clean --if-exists --no-owner --single-transaction` (tout ou rien) ; rétablit les droits
   du rôle applicatif ; conserve les réglages de sauvegarde actuels.
4. Fichier plus ancien que l'application : lancer `manage.py migrate` (le lanceur le fait au démarrage).
5. Rouvrir Komersia et contrôler les dernières ventes. Pour annuler, restaurer la sauvegarde de sécurité.

## Test mensuel conseillé

Une fois par mois : prendre la dernière sauvegarde, la restaurer sur un autre PC (ou une base de test) avec
`--check-only` puis une vraie restauration, et comparer quelques chiffres (ventes du jour, nombre de produits).
Une sauvegarde jamais restaurée n'est pas une garantie.

## Limites

- Une clé USB débranchée n'est pas sauvegardée : l'interface alerte (bandeau après 2 jours) mais rien ne protège
  les données tant que le dossier est absent. Laisser la clé branchée, ou alterner deux clés.
- Une sauvegarde sur le même disque ne protège pas d'une panne du disque ou de la perte du PC.
- RPO de 24 h au mieux (sauvegarde logique quotidienne) ; les sauvegardes ne sont pas chiffrées.
- Aucune copie hors du PC n'est faite automatiquement.
- Non testé sur Windows réel ni avec une clé USB réelle (FAT/exFAT : l'application n'utilise ni lien physique ni
  verrou de fichier).
