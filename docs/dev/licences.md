# Clés de licence mensuelles (édition locale)

L'application est d'abord installée **en local** sur le PC de la boutique, sans serveur Komersia en ligne. Le
paiement Mobile Money en ligne est mis de côté (`BILLING_PROVIDER` vide) ; l'abonnement reste la seule source de
revenus et se gère par une **clé de licence** que le commerçant saisit dans l'application. Les règles du module
`billing` ne changent pas : essai de 14 jours, grâce de 7 jours, lecture seule ensuite, plans essentiel / pro /
business et leurs limites.

## Processus complet

1. **Le commerçant lit son code** : page *Abonnement* (`/app/abonnement/`), carte « Activer ou renouveler votre
   abonnement ». Elle affiche le code court `XXXX-XXXX` et l'identifiant complet (UUID) ; le bouton « Copier »
   les place dans le presse-papiers pour les envoyer (WhatsApp, SMS).
2. **Il paie l'agent hors application** (espèces, agent, virement) et lui transmet le code **et l'UUID**.
3. **L'agent émet la clé** avec l'outil du vendeur (jamais livré aux commerçants) :

   ```bash
   python tools/license/keygen.py issue \
       --installation "ABCD-EFGH 3f2c1a9e-....-....-....-............" \
       --plan pro --months 1 --key-file /chemin/hors/depot/komersia-license-private.pem
   ```

   `--installation` accepte l'UUID seul, ou « code court + UUID » : dans ce cas l'outil vérifie que le code
   correspond à l'UUID (détecte un UUID mal recopié). Le plan est l'un de `essentiel`, `pro`, `business` ; les
   mois vont de 1 à 12.
4. **Le commerçant saisit la clé** dans la même carte (espaces, tirets et minuscules acceptés) et clique sur
   « Activer ». L'abonnement passe à *actif* ; un reçu (clé de licence, montant non affiché) est disponible dans
   l'historique.

Équivalent API : `GET /api/v1/billing/installation/` (propriétaire, gérant, comptable),
`POST /api/v1/billing/license/activate/` `{"key": "..."}` (propriétaire seul ; exemptée de la lecture seule).

## Format de la clé

Charge utile binaire canonique `{version, installation_id, plan, months, issued_at, nonce}` signée en **Ed25519**
(`DOMAIN = "KOMERSIA-LICENSE-V1"` préfixé avant signature), suivie d'une somme de contrôle (3 octets de SHA-256),
le tout en base32 sans `0 O 1 I`, en groupes de 5 caractères (environ 170 caractères). La somme de contrôle est
vérifiée **avant** la signature : une faute de frappe donne « Clé mal saisie : vérifiez les caractères. ». Le
détail est dans `backend/billing/license_codec.py` (partagé par l'application et l'outil, sans Django).

Vérifications à l'activation (dans cet ordre) : somme de contrôle, signature, installation (une clé d'un autre PC est
refusée et le message rappelle le code du PC), plan existant, nonce jamais utilisé (table `LicenseKeyUse`, sinon
HTTP 409 « Cette clé a déjà été utilisée. »), clé émise il y a moins de 90 jours (réglage
`KOMERSIA_LICENSE_MAX_KEY_AGE_DAYS`, pour ne pas garder de clés en réserve). Effet : plan de la clé, période
prolongée de `months` mois calendaires à partir de `max(maintenant, fin de période encore valide)` (payer en avance
ne fait perdre aucun jour), grâce et lecture seule effacées. Une clé d'un plan supérieur s'applique tout de suite,
sans prorata (l'agent la facture au bon montant) ; une clé d'un plan inférieur est refusée si l'usage actuel
(boutiques, utilisateurs) dépasse les limites de ce plan, et n'est alors pas consommée. Un paiement de fournisseur
`license` (montant 0, référence = nonce) est créé pour l'historique et le reçu, et l'audit enregistre
`subscription.license_activated`.

## Conservation de la clé privée

- La **clé privée de production** est générée par Komersia **hors dépôt**, avant la première livraison :
  `python tools/license/keygen.py init --out-dir <dossier hors dépôt>` (fichier en droits 600).
- Elle fait vivre tout le revenu : quiconque la détient fabrique des clés. Conservez-la sur une machine de
  confiance, avec au moins deux sauvegardes chiffrées dans des lieux distincts ; **jamais** sur le PC d'un
  commerçant, dans un installateur, dans le dépôt, dans un message ou un ticket.
- `backend/billing/license_public_key.pem` contient seulement une **paire de développement** : la clé privée
  correspondante (`tools/license/dev-private-key.pem`) est ignorée par git et ne sert qu'aux essais. **Avant la
  première livraison** : générer la paire de production, remplacer `license_public_key.pem` par la clé publique
  produite, puis reconstruire l'installateur. Les clés de développement ne doivent plus fonctionner en production.
- Le fichier public peut être surchargé par `KOMERSIA_LICENSE_PUBLIC_KEY_FILE` (réglage Django ou variable
  d'environnement), utile pour les tests.

## Rotation et perte

- **Rotation** : le format porte une version, pas d'identifiant de clé. Pour changer de paire, générer une nouvelle
  paire, livrer une mise à jour de l'application avec la nouvelle clé publique, puis émettre les clés suivantes
  avec la nouvelle clé privée. Les clés déjà émises avec l'ancienne paire cessent d'être acceptées dès la mise à
  jour (elles durent au plus 90 jours de validité d'activation) : planifier la rotation en début de mois et
  réémettre les clés en attente.
- **Perte de la clé privée** : plus aucune clé ne peut être émise ; il faut une nouvelle paire et une mise à jour
  de tous les PC. D'où les sauvegardes.
- **Fuite de la clé privée** : même procédure que la rotation, en urgence.

## Limites

- **Une clé est liée à un PC** (identifiant d'installation créé au premier besoin, stocké dans la table
  `billing_licensestate`, sans RLS, jamais modifiable via l'API). Un changement de PC, une réinstallation complète
  ou une restauration sur une autre machine donne un **nouvel identifiant**.
- **Procédure de transfert (manuelle, non automatisée)** : le commerçant donne à l'agent le nouveau code ; l'agent
  vérifie l'identité du commerçant, constate la date d'échéance sur l'ancien PC (page Abonnement) ou dans ses
  registres, puis émet une clé pour le nouveau PC avec le même plan et les mois restants (arrondis au mois
  supérieur au plus). L'ancien PC n'est pas désactivé : c'est un engagement commercial, pas technique. Restaurer
  une sauvegarde de base sur le MÊME PC conserve l'identifiant seulement si la table `billing_licensestate` est
  restaurée avec.
- **Horloge** : le temps d'abonnement ne recule jamais (`effective_now = max(horloge, dernière date vue)`, mémorisée
  au plus une fois par minute, à chaque requête authentifiée et à chaque `billing_tick`). Remettre l'horloge 40
  jours en arrière ne prolonge ni l'essai ni l'abonnement. Un saut en avant de plus de 400 jours d'un coup n'est pas
  enregistré (journal d'avertissement `billing.clock`) pour qu'une horloge folle, une fois corrigée, ne bloque pas
  le commerçant. Un PC qui reste éteint plus de 400 jours puis rallumé reste donc évalué à l'heure système sans
  que la date soit mémorisée jusqu'à ce que l'écart redevienne raisonnable : prévoir une intervention de
  l'agent dans ce cas rare.
- **La sécurité côté client est une dissuasion, pas une protection absolue** : l'application et la base tournent sur
  le PC du commerçant ; un utilisateur déterminé peut modifier son installation (code, base, horloge du système et
  base ensemble). La signature empêche surtout de fabriquer ou de partager des clés ; le lien à l'installation et
  le nonce empêchent de réutiliser une clé. La réponse à la fraude est contractuelle et commerciale.
