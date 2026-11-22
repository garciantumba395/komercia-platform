# ruff: noqa: E501
from django.core.management.base import BaseCommand, CommandError

from backups import services


class Command(BaseCommand):
    help = ("OPÉRATION DE MAINTENANCE : restaure une sauvegarde dans la base de l'installation, après une "
            "sauvegarde de sécurité de l'état actuel. Remplace TOUTES les données actuelles. "
            "Arrêter l'application avant de la lancer.")

    def add_arguments(self, parser):
        parser.add_argument("fichier", help="Chemin du fichier .dump à restaurer.")
        parser.add_argument("--yes", action="store_true", help="Confirme le remplacement des données actuelles.")
        parser.add_argument("--check-only", action="store_true", help="Vérifie le fichier sans rien restaurer.")
        parser.add_argument("--safety-dir", help="Dossier de la sauvegarde de sécurité (défaut : dossier configuré).")

    def handle(self, *args, **options):
        path = options["fichier"]
        try:
            if options["check_only"]:
                info = services.verify_backup_file(path)
                self.stdout.write(f"Fichier valide (SHA-256 {info['sha256'][:12]}…).")
                return
            if not options["yes"]:
                raise CommandError("Cette opération remplace toutes les données actuelles. "
                                   "Relancez avec --yes pour confirmer.")
            result = services.restore_backup(path, safety_folder=options.get("safety_dir"))
        except services.BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("Restauration terminée."))
        self.stdout.write(f"Sauvegarde de sécurité de l'état précédent : {result.safety_backup}")
        if result.sessions_left:
            self.stdout.write(self.style.WARNING(
                f"{result.sessions_left} connexion(s) n'ont pas pu être fermées : redémarrez l'application."))
        if result.pending_migrations:
            self.stdout.write(self.style.WARNING(
                "Cette sauvegarde est plus ancienne que l'application : lancez « manage.py migrate » "
                "(le lanceur le fait au démarrage)."))
