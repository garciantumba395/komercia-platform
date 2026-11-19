"""Crée un agent de la plateforme : `python manage.py create_platform_admin --phone +243... --name "Nom"`.

Le mot de passe n'est JAMAIS passé en argument : variable d'environnement PLATFORM_ADMIN_PASSWORD, ou saisie
interactive (masquée, deux fois). L'agent est `is_staff=True` et n'appartient à AUCUNE entreprise (pas de
Membership) : l'API commerçant le refuse. `--owner` en fait un propriétaire de la plateforme (super-utilisateur :
gestion des fonctions par entreprise). À exécuter avec le rôle propriétaire (DB_USER=komersia_owner).
"""

import getpass
import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Crée un agent de la plateforme (accès à la console /ops/), sans entreprise."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--owner", action="store_true", help="Propriétaire de la plateforme (super-utilisateur).")

    def handle(self, *args, **options):
        phone, name = options["phone"].strip(), options["name"].strip()
        if not phone or not name:
            raise CommandError("Le téléphone et le nom sont obligatoires.")
        if User.objects.filter(phone=phone).exists():
            raise CommandError("Un utilisateur avec ce numéro existe déjà.")
        password = os.environ.get("PLATFORM_ADMIN_PASSWORD")
        if not password:
            password = getpass.getpass("Mot de passe : ")
            if password != getpass.getpass("Confirmer le mot de passe : "):
                raise CommandError("Les mots de passe ne correspondent pas.")
        try:
            validate_password(password, user=User(phone=phone, full_name=name))
        except ValidationError as exc:
            raise CommandError("Mot de passe refusé : " + " ".join(exc.messages)) from exc
        user = User.objects.create_user(
            phone=phone, password=password, full_name=name, email=options["email"].strip(),
            is_staff=True, is_superuser=options["owner"],
        )
        self.stdout.write(self.style.SUCCESS(f"Agent créé : {user.full_name} ({user.phone}), sans entreprise."))
