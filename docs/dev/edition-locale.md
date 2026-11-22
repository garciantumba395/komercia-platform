# Édition locale Windows : lanceur, installateur, exploitation

Décision : `docs/adr/0003-edition-locale-windows.md`. Code : `desktop/` (lanceur, construction) et
`backend/komersia/settings_local.py` + `urls_local.py` (réglages Django de l'édition locale).
Sauvegardes : `sauvegardes-locales.md`. Licences : `licences.md`.

## Architecture

```
PC principal (Windows 10/11 64 bits)                       Autres appareils (navigateur)
┌──────────────────────────────────────────────┐
│ Tâche planifiée « Komersia » (au démarrage,   │   http://NOM-DU-PC:8000/app/
│ compte NETWORK SERVICE, sans session ouverte) │ ◄────────────────────────────  PC, téléphones
│  └─ KomersiaServer.exe --run                  │   (HTTP, réseau local PRIVÉ)
│      ├─ waitress  0.0.0.0:8000  (Django)      │
│      ├─ planificateur : billing_tick, backup_tick (toutes les heures)
│      └─ PostgreSQL embarqué 127.0.0.1:54329   │   jamais exposé au réseau
└──────────────────────────────────────────────┘
```

Un seul processus lanceur (`KomersiaServer.exe`, PyInstaller « dossier ») : il prend un verrou d'instance, prépare
secrets et dossiers, démarre PostgreSQL (`pg_ctl`), crée rôles et base, applique migrations et fichiers statiques si
la version a changé, démarre waitress dans un fil, puis le planificateur dans un autre. À l'arrêt (signal, ou fichier
`control\stop.request`), il arrête waitress puis PostgreSQL proprement (`pg_ctl stop -m fast`, arrêt de secours sinon).

### Modules (`desktop/komersia_desktop/`)

| Fichier | Rôle |
|---|---|
| `paths.py` | dossiers installation / données / journaux / `securite` / `control` |
| `config.py` | `secrets.json` (aléatoires, jamais régénérés), `settings.json` (ports, bind), `runtime.json` (ports réels), `state.json` (version appliquée) |
| `winsec.py` | droits des fichiers : `icacls` (Windows), `chmod` (Linux) |
| `postgres.py` | initdb, démarrage/arrêt, rôles, base, privilèges (équivalent de `scripts/db-init.sql`), copie de sécurité |
| `setup.py` | premier lancement et mises à jour (migrations avec `komersia_owner` dans un processus séparé) |
| `server.py` / `scheduler.py` | waitress ; planificateur à horloge injectable |
| `main.py` | `KomersiaServer` : `--run` (défaut) `--stop` `--status` `--init` `--open` `--restore F` `--version` `--selftest` |
| `lock.py`, `ports.py`, `logs.py`, `network.py`, `platform_win.py` | verrou noyau, choix de port avec repli, journaux avec rotation, adresses réseau, appels Windows isolés |

## Dossiers

| Dossier | Contenu | Mise à jour | Désinstallation |
|---|---|---|---|
| `C:\Program Files\Komersia` | `KomersiaServer.exe`, `_internal\`, `pgsql\` (binaires PostgreSQL), `scripts\` | remplacé | supprimé |
| `C:\ProgramData\Komersia` (ou `KOMERSIA_DATA_DIR`) | tout le reste | **jamais touché** | **conservé** (question explicite) |

Données : `config\` (`secrets.json` lisible par le seul compte du service et SYSTEM ; `settings.json`, `runtime.json`,
`state.json`), `pgdata\` (cluster PostgreSQL, réservé au service), `logs\` (`komersia.log` avec rotation 5 × 5 Mo,
`postgres.log`), `static\`, `securite\` (copies de sécurité internes : avant mise à jour et avant restauration, les
3 dernières avant mise à jour sont gardées), `control\` (seul dossier modifiable par les utilisateurs standard :
demande d'arrêt, demande de restauration, verrou), `ADRESSE.txt`.
Les sauvegardes quotidiennes de l'application `backups` vont dans un dossier choisi par le propriétaire dans
l'interface, **hors** de `ProgramData\Komersia` et de l'installation (l'API le refuse) : disque ou clé USB.

## Ports et réseau

| Port | Défaut | Réglage | Repli |
|---|---|---|---|
| Web | 8000 | `settings.json` (`web_port`), `KOMERSIA_PORT`, page de l'installateur (`/PORT=`) | jusqu'à +9 si occupé (la règle de pare-feu couvre `port..port+9`) |
| PostgreSQL (127.0.0.1) | 54329 | `pg_port`, `KOMERSIA_PG_PORT` | jusqu'à +49 |

`KOMERSIA_BIND=127.0.0.1` (ou `"bind": "127.0.0.1"` dans `settings.json`, ou case décochée dans l'installateur) :
écoute limitée à ce PC, sans règle de pare-feu (poste unique). Après changement : « Arrêter Komersia » puis
« Démarrer Komersia ».

### Comment les autres appareils se connectent

Ils doivent être sur le **même réseau** (Wi-Fi ou câble de la boutique) et le PC principal allumé. Dans le navigateur :
`http://NOM-DU-PC:8000/app/` ou `http://192.168.x.y:8000/app/`. Ces adresses sont écrites dans
`C:\ProgramData\Komersia\ADRESSE.txt` (raccourci « Adresse pour les autres appareils ») et affichées par
`KomersiaServer --status`. Conseil : réserver une adresse IP fixe au PC dans le routeur (sinon l'adresse en chiffres
peut changer ; le nom du PC reste valable).

### Sécurité : le trafic local est en HTTP

Le trafic entre les appareils et le PC principal n'est **pas chiffré** (pas de HTTPS : aucun certificat n'est
valable pour un nom de PC privé). Toute personne branchée sur le même réseau peut lire les échanges, dont le mot de
passe saisi. Conséquences : ne pas ouvrir le Wi-Fi de la boutique au public (utiliser un réseau d'invités séparé),
protéger le Wi-Fi par WPA2/WPA3, ne jamais rediriger le port 8000 du routeur vers Internet. Le pare-feu n'autorise
l'entrée que sur le profil réseau **Privé** (réseau Public/Domaine : refusé). PostgreSQL n'écoute que sur 127.0.0.1,
avec authentification scram et mots de passe aléatoires ; le serveur web se connecte avec `komersia_app` (soumis à la
RLS), les migrations avec `komersia_owner`, les sauvegardes avec `komersia_backup` (lecture seule, BYPASSRLS).
Aucun mot de passe par défaut : tout est généré au premier lancement. `secrets.json` ne doit jamais être supprimé ni
copié hors du PC (il contient le mot de passe de la base) ; il fait partie de ce qu'il faut protéger avec le PC.

## Ce que fait l'installateur (`desktop/build/komersia.iss`)

Assistant en français, droits administrateur, Windows 10+ 64 bits seulement.
1. Pages : port (8000) et « autoriser les autres appareils » (non redemandées lors d'une mise à jour).
2. Mise à jour : arrête l'ancienne version proprement, remplace `_internal`, `pgsql`, `scripts` (jamais les données).
3. Copie les programmes et les binaires PostgreSQL ; crée les raccourcis.
4. Exécute `scripts\komersia-service.ps1 -Action Install` : dossiers et droits (NETWORK SERVICE modifie `ProgramData\Komersia` ;
   `control` modifiable par tous ; `securite` limité), `settings.json` (une seule fois), **tâche planifiée « Komersia »**
   (déclencheur « au démarrage », compte NETWORK SERVICE, redémarrage automatique en cas de crash, pas de limite de durée),
   **règle de pare-feu** entrante, profil Privé, `KomersiaServer.exe`, ports `p..p+9`, puis démarre la tâche.
   Le serveur ne tourne pas en administrateur : PostgreSQL le refuse.
5. Raccourcis (menu Démarrer, + Bureau) : « Komersia » (ouvre le navigateur ; attend le premier démarrage),
   « Arrêter Komersia », « Démarrer Komersia (administrateur) », « Restaurer une sauvegarde », « Adresse pour les autres
   appareils », « Désinstaller ».

Installation silencieuse : `Komersia-Setup-1.0.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=setup.log [/PORT=8000] [/LAN=1]`.

**Premier démarrage** (≈ 1 minute) : génération des secrets, `initdb`, rôles, migrations, fichiers statiques. Les suivants
sont rapides (migrations ignorées si la version est inchangée).

## Mise à jour

Lancer le nouvel installateur par-dessus : il arrête Komersia, remplace les programmes, relance. Au démarrage, si la
version a changé, le lanceur fait d'abord une **copie de sécurité** (`securite\avant-mise-a-jour-<ancienne>-<date>.dump`,
et **refuse de migrer** si elle échoue), puis applique les migrations avec le rôle propriétaire et collecte les fichiers statiques.

## Désinstallation

Panneau de configuration > Applications (ou raccourci). Arrête Komersia, supprime tâche planifiée, règle de pare-feu,
programmes et raccourcis. **Les données sont conservées par défaut** : une question explicite (puis une confirmation)
propose de les supprimer. Silencieux : `unins000.exe /VERYSILENT` conserve ; `/PURGEDATA=1` supprime. Réinstaller
retrouve les données (même `secrets.json`, même base).

## Restauration d'une sauvegarde

Raccourci « Restaurer une sauvegarde » (ou `KomersiaServer --restore fichier.dump [--check-only]`). L'instance en cours
arrête le serveur web, lance `manage.py restore_backup --yes` (copie de sécurité de l'état actuel d'abord, dans
`securite\`), applique les migrations, rétablit les droits du rôle applicatif et relance le serveur web.
Le fichier doit être lisible par le compte du service (disque ou clé USB, pas un dossier personnel privé).
Détails : `sauvegardes-locales.md`.

## Construction

`desktop/build/build.ps1` (voir l'en-tête) : zip PostgreSQL EDB (version et SHA-256 dans `postgres.json`, **la
construction échoue si la somme est vide ou différente**), `uv sync`, PyInstaller, `--selftest` de l'exécutable gelé,
assemblage, Inno Setup. Le workflow `.github/workflows/build-windows.yml` (« Installateur Windows ») le lance sur
`windows-latest`, exécute `ci-integration-test.ps1`, puis publie l'artefact `Komersia-Setup-<version>`.

**Première fois : renseigner la somme SHA-256.** L'URL est
`https://get.enterprisedb.com/postgresql/postgresql-<version>-windows-x64-binaries.zip` ; elle n'a pas pu être
téléchargée depuis l'environnement de développement (réseau bloqué), donc `postgres.json` a `"sha256": ""`.
Lancer le workflow avec « print_pg_hash » coché : il télécharge le zip et affiche sa somme ; la contrôler avec la page
EDB, la copier dans `postgres.json`, valider. Pour changer de version PostgreSQL, modifier `version` et recommencer.

## Dépannage

| Symptôme | Cause probable / action |
|---|---|
| Le raccourci « Komersia » ne s'ouvre pas | Premier démarrage en cours (≈ 1 min) ; ou arrêté : « Démarrer Komersia (administrateur) ». Voir `logs\komersia.log`. |
| Autres appareils : pas de connexion | PC éteint ; autre réseau ; réseau Windows classé **Public** (Paramètres > Réseau > Propriétés > Privé) ; adresse changée (relire `ADRESSE.txt`) ; logiciel de sécurité tiers. |
| « Port occupé » dans les journaux | Repli automatique ; lire le port réel dans `ADRESSE.txt`. Au-delà de +9, changer `web_port` et refaire l'installation (règle de pare-feu). |
| Erreur au démarrage de la base | `logs\postgres.log`. Après une coupure de courant PostgreSQL se récupère seul. Ne jamais supprimer `pgdata`. |
| `secrets.json` illisible | Ne rien supprimer : restaurer le fichier depuis une copie ; sans lui la base ne peut plus être ouverte par l'application. |
| Diagnostic | `KomersiaServer.exe --status`, `--selftest` ; journaux dans `C:\ProgramData\Komersia\logs`. |

## Ce qui est testé, et où

| Élément | Testé ici (Linux, `desktop/tests`) | Testé seulement par la CI Windows |
|---|---|---|
| Secrets, permissions 0600, ports/repli, verrou, rotation des journaux, planificateur, arguments `icacls` | oui | — |
| initdb, pg_ctl, rôles, RLS, migrations propriétaire, waitress, inscription, vente, arrêt propre, redémarrage, mise à jour avec copie de sécurité, sauvegarde + restauration via le lanceur | oui, **cycle complet réel** (PostgreSQL 16 du conteneur, sources ET exécutable PyInstaller Linux) | — |
| `settings_local`, `urls_local` (parité avec `urls.py`) | oui | — |
| `icacls`/ACL réelles, verrou `msvcrt`, `SO_EXCLUSIVEADDRUSE`, PostgreSQL sous NETWORK SERVICE, binaires EDB | non | oui |
| Zip EDB + somme SHA-256, PyInstaller Windows, Inno Setup (`komersia.iss`, Pascal), PowerShell (`komersia-service.ps1`) | non | oui (`build-windows.yml`) |
| Tâche planifiée, pare-feu Privé, écoute 127.0.0.1 de PostgreSQL, désinstallation/réinstallation, mise à jour par-dessus | non | oui (`ci-integration-test.ps1`) |

Lancer les tests Linux (depuis la racine du dépôt) :
`uv run --project backend pytest -c desktop/pytest.ini desktop/tests`. Le test de cycle complet a besoin des binaires
PostgreSQL (`/usr/lib/postgresql/16/bin`, ou `KOMERSIA_TEST_PG_BIN`) et, en root, de l'utilisateur système `postgres`
(initdb refuse de tourner en root ; PostgreSQL est alors lancé avec cet utilisateur via `KOMERSIA_PG_RUN_AS`, variable
réservée aux tests) ; sinon il est ignoré. `KOMERSIA_TEST_FROZEN=<chemin>/KomersiaServer` le fait tourner sur un exécutable
PyInstaller construit sous Linux.
