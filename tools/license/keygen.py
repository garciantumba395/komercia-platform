#!/usr/bin/env python3
"""Outil du VENDEUR : génère la paire de clés de production et émet les clés de licence.

JAMAIS livré aux commerçants (ni dans l'installateur) ; la clé privée n'est jamais dans le dépôt.
N'importe pas Django : seulement `cryptography` et le codec pur `backend/billing/license_codec.py`
(chargé par chemin de fichier, pour que l'application et l'outil partagent exactement le même format).

    python tools/license/keygen.py init --out-dir ~/komersia-licences
    python tools/license/keygen.py issue --installation <uuid> --plan pro --months 1 --key-file <clé privée>
    python tools/license/keygen.py issue --installation "ABCD-EFGH 3f2c...uuid" --plan essentiel --months 3

Variable d'environnement KOMERSIA_LICENSE_PRIVATE_KEY_FILE : clé privée par défaut.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
CODEC_PATH = ROOT / "backend" / "billing" / "license_codec.py"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
SHORT_RE = re.compile(r"\b[A-Z2-9]{4}-[A-Z2-9]{4}\b")


def load_codec():
    spec = importlib.util.spec_from_file_location("komersia_license_codec", CODEC_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # requis par @dataclass
    spec.loader.exec_module(module)
    return module


def parse_installation(text: str, codec) -> uuid.UUID:
    """Accepte « <uuid> » ou « XXXX-XXXX <uuid> » ; si le code court est fourni, il doit correspondre."""
    found = UUID_RE.search(text)
    if not found:
        raise SystemExit("Identifiant d'installation invalide : un UUID complet est obligatoire "
                         "(le code court seul ne suffit pas).")
    installation = uuid.UUID(found.group(0))
    short = SHORT_RE.search(text.upper().replace(found.group(0).upper(), ""))
    if short and short.group(0) != codec.short_code(installation):
        raise SystemExit(f"Le code court {short.group(0)} ne correspond pas à l'UUID (attendu "
                         f"{codec.short_code(installation)}) : identifiant mal recopié ?")
    return installation


def cmd_init(args) -> None:
    out = Path(args.out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    private_path, public_path = out / "komersia-license-private.pem", out / "license_public_key.pem"
    if private_path.exists() and not args.force:
        raise SystemExit(f"{private_path} existe déjà : refus d'écraser (--force pour forcer).")
    key = Ed25519PrivateKey.generate()
    private_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    os.chmod(private_path, 0o600)
    public_path.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    print(f"Clé privée  : {private_path}\nClé publique : {public_path}\n")
    print("CONSIGNES (lisez avant de continuer) :\n"
          " 1. La clé privée est LE secret qui fait vivre l'abonnement : quiconque la détient fabrique des clés.\n"
          " 2. Conservez-la HORS du dépôt, sur une machine de confiance, avec au moins 2 sauvegardes chiffrées\n"
          "    dans des lieux distincts. Si elle est perdue, plus aucune clé ne peut être émise.\n"
          " 3. Ne la copiez JAMAIS sur le PC d'un commerçant ni dans un installateur.\n"
          " 4. Remplacez backend/billing/license_public_key.pem par la clé PUBLIQUE ci-dessus avant la\n"
          "    première livraison, puis reconstruisez l'installateur.\n"
          " 5. Rotation et perte : voir docs/dev/licences.md.")


def cmd_issue(args) -> None:
    codec = load_codec()
    key_file = args.key_file or os.environ.get("KOMERSIA_LICENSE_PRIVATE_KEY_FILE")
    if not key_file:
        raise SystemExit("Indiquez la clé privée : --key-file ou KOMERSIA_LICENSE_PRIVATE_KEY_FILE.")
    private = serialization.load_pem_private_key(Path(key_file).expanduser().read_bytes(), password=None)
    if not 1 <= args.months <= 12:
        raise SystemExit("--months doit être compris entre 1 et 12.")
    installation = parse_installation(args.installation, codec)
    payload = codec.LicensePayload(
        installation_id=installation, plan=args.plan, months=args.months,
        issued_at=args.issued_at if args.issued_at is not None else int(time.time()), nonce=codec.new_nonce())
    print(codec.issue_key(private, payload))
    print(f"# installation {installation} ({codec.short_code(installation)}), plan {args.plan}, "
          f"{args.months} mois, nonce {payload.nonce}", file=sys.stderr)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="génère une paire de clés de production")
    init.add_argument("--out-dir", required=True, help="dossier de sortie (hors dépôt)")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)
    issue = sub.add_parser("issue", help="émet une clé de licence")
    issue.add_argument("--installation", required=True, help="UUID, ou « XXXX-XXXX UUID »")
    issue.add_argument("--plan", required=True, help="code du plan : essentiel, pro, business")
    issue.add_argument("--months", type=int, default=1)
    issue.add_argument("--key-file", help="clé privée PEM")
    issue.add_argument("--issued-at", type=int, help="date d'émission (secondes Unix), tests uniquement")
    issue.set_defaults(func=cmd_issue)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
