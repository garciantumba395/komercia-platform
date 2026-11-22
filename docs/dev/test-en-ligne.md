# Test en ligne gratuit (Render)

Image tout-en-un `Dockerfile.test` : application + PostgreSQL dans un seul conteneur, données de démonstration
recréées à chaque démarrage. Testée en CI (job `test-image` : santé, connexion démo, interface `/app/`).

**Réservé aux tests** : données éphémères, paiements d'abonnement simulés (fournisseur `sandbox`), aucune vraie
donnée de client. Le service gratuit s'endort après ~15 min d'inactivité (réveil 30–60 s).

## Déploiement
1. render.com → se connecter avec GitHub, autoriser le dépôt `komercia-platform`.
2. New → Blueprint → choisir le dépôt et la branche (`render.yaml` à la racine) → Apply.
3. Attendre la fin du build (5–10 min), puis ouvrir `https://komercia-test.onrender.com/app/` (l'URL exacte est affichée par Render).

## Comptes de démonstration
- Propriétaire : +243990000000 / Demo-1234!
- Caissier : +243990000001 / Caissier-1234!
