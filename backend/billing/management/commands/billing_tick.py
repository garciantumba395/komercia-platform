from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from billing import services


class Command(BaseCommand):
    help = ("Applique les transitions d'état des abonnements et génère les rappels. Idempotent ; "
            "à planifier chaque heure ou chaque jour.")

    def add_arguments(self, parser):
        parser.add_argument("--now", help="Date ISO 8601 simulée (tests, rattrapage).")

    def handle(self, *args, **options):
        now = parse_datetime(options["now"]) if options.get("now") else None
        stats = services.tick(now)
        self.stdout.write(", ".join(f"{k}={v}" for k, v in stats.items()))
