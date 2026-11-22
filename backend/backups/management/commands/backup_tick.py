# ruff: noqa: E501
from django.core.management.base import BaseCommand

from backups import services


class Command(BaseCommand):
    help = ("Crée une sauvegarde si la dernière réussie date de plus de 24 h (réglable) et si le dossier est "
            "disponible. Idempotent ; à planifier toutes les heures (planificateur du lanceur Windows).")

    def handle(self, *args, **options):
        result = services.tick()
        self.stdout.write(", ".join(f"{k}={v}" for k, v in result.items()))
