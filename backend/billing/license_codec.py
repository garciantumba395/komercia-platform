"""Format des clés de licence Komersia : codage, signature et lecture. AUCUNE dépendance à Django.

Ce module est partagé par l'application (`billing.licensing`) et par l'outil du vendeur
(`tools/license/keygen.py`, chargé par chemin de fichier) : un seul format, pas de dérive possible.

Format binaire canonique de la charge utile (grand-boutiste) :

    version (1 octet) | installation_id (16 octets) | longueur du plan (1 octet) | plan (ASCII)
    | mois (1 octet) | émise le (4 octets, secondes Unix) | nonce (10 octets)

Signature Ed25519 (64 octets) calculée sur `DOMAIN + charge utile`. La clé affichée est :

    base32(charge utile | signature | somme de contrôle) en groupes de 5 caractères séparés par « - »

où la somme de contrôle = 3 premiers octets de SHA-256(charge utile | signature). Elle sert à détecter
les fautes de frappe AVANT toute vérification cryptographique. L'alphabet exclut 0, O, 1 et I.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import struct
import unicodedata
import uuid
from dataclasses import dataclass

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 32 caractères, sans 0 O 1 I
VERSION = 1
DOMAIN = b"KOMERSIA-LICENSE-V1\x00"
SIGNATURE_LEN = 64
CHECKSUM_LEN = 3
NONCE_LEN = 10
MAX_PLAN_LEN = 30
MIN_MONTHS, MAX_MONTHS = 1, 12
GROUP = 5

MSG_MALFORMED = "Clé mal saisie : vérifiez les caractères."
_DASHES = "-‐‑‒–—−"


class LicenseError(Exception):
    """Erreur de clé de licence : `message` (français, affichable) et `code` (stable)."""

    def __init__(self, message: str, code: str = "license_invalid"):
        super().__init__(message)
        self.message, self.code = message, code


@dataclass(frozen=True)
class LicensePayload:
    installation_id: uuid.UUID
    plan: str
    months: int
    issued_at: int  # secondes Unix (UTC)
    nonce: str  # 20 caractères hexadécimaux minuscules
    version: int = VERSION


def new_nonce() -> str:
    return secrets.token_hex(NONCE_LEN)


# --------------------------------------------------------------------------- charge utile


def pack_payload(p: LicensePayload) -> bytes:
    if not re.fullmatch(rf"[a-z0-9_-]{{1,{MAX_PLAN_LEN}}}", p.plan or ""):
        raise ValueError("Code de plan invalide.")
    if not MIN_MONTHS <= p.months <= MAX_MONTHS:
        raise ValueError(f"Le nombre de mois doit être entre {MIN_MONTHS} et {MAX_MONTHS}.")
    if not 0 <= p.issued_at < 2**32:
        raise ValueError("Date d'émission invalide.")
    nonce = bytes.fromhex(p.nonce)
    if len(nonce) != NONCE_LEN:
        raise ValueError("Nonce invalide.")
    plan = p.plan.encode("ascii")
    return (struct.pack(">B16sB", p.version, p.installation_id.bytes, len(plan)) + plan
            + struct.pack(">BI", p.months, p.issued_at) + nonce)


def unpack_payload(data: bytes) -> LicensePayload:
    try:
        version, inst, plan_len = struct.unpack_from(">B16sB", data, 0)
        if version != VERSION:
            raise LicenseError("Version de clé non reconnue : mettez Komersia à jour ou demandez une "
                               "nouvelle clé.", "license_version")
        pos = 18
        plan = data[pos:pos + plan_len]
        pos += plan_len
        months, issued_at = struct.unpack_from(">BI", data, pos)
        pos += 5
        nonce = data[pos:pos + NONCE_LEN]
        if len(plan) != plan_len or len(nonce) != NONCE_LEN or pos + NONCE_LEN != len(data):
            raise ValueError
        return LicensePayload(uuid.UUID(bytes=inst), plan.decode("ascii"), months, issued_at, nonce.hex())
    except (struct.error, ValueError, UnicodeDecodeError):
        raise LicenseError(MSG_MALFORMED, "license_malformed") from None


# --------------------------------------------------------------------------- base32 lisible


def _encode_bytes(blob: bytes) -> str:
    bits = len(blob) * 8
    pad = (-bits) % 5
    n = int.from_bytes(blob, "big") << pad
    count = (bits + pad) // 5
    return "".join(ALPHABET[(n >> (5 * (count - 1 - i))) & 31] for i in range(count))


def _decode_chars(text: str) -> bytes:
    count = len(text)
    nbytes = (count * 5) // 8
    pad = count * 5 - nbytes * 8
    if nbytes == 0 or (-nbytes * 8) % 5 != pad or pad >= 5:
        raise LicenseError(MSG_MALFORMED, "license_malformed")
    n = 0
    for ch in text:
        n = (n << 5) | ALPHABET.index(ch)
    if n & ((1 << pad) - 1):  # bits de bourrage non nuls
        raise LicenseError(MSG_MALFORMED, "license_malformed")
    return (n >> pad).to_bytes(nbytes, "big")


def normalize(text: str) -> str:
    """Majuscules, sans espaces ni tirets. Lève LicenseError si un caractère est hors alphabet."""
    cleaned = unicodedata.normalize("NFKC", text or "").upper()
    cleaned = "".join(c for c in cleaned if not c.isspace() and c not in _DASHES)
    if not cleaned or any(c not in ALPHABET for c in cleaned):
        raise LicenseError(MSG_MALFORMED, "license_malformed")
    return cleaned


def group(chars: str) -> str:
    return "-".join(chars[i:i + GROUP] for i in range(0, len(chars), GROUP))


def _checksum(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()[:CHECKSUM_LEN]


# --------------------------------------------------------------------------- clés


def issue_key(private_key, payload: LicensePayload) -> str:
    """Signe la charge utile avec une clé privée Ed25519 (`cryptography`) et retourne la clé affichable."""
    body = pack_payload(payload)
    data = body + private_key.sign(DOMAIN + body)
    return group(_encode_bytes(data + _checksum(data)))


def decode_key(text: str) -> tuple[bytes, bytes]:
    """(charge utile, signature) d'une clé saisie. Contrôle d'abord la somme de contrôle (faute de
    frappe), PAS la signature. Lève LicenseError « Clé mal saisie » sinon."""
    blob = _decode_chars(normalize(text))
    if len(blob) < 1 + 16 + 1 + 1 + 1 + 4 + NONCE_LEN + SIGNATURE_LEN + CHECKSUM_LEN:
        raise LicenseError(MSG_MALFORMED, "license_malformed")
    data, check = blob[:-CHECKSUM_LEN], blob[-CHECKSUM_LEN:]
    if not secrets.compare_digest(_checksum(data), check):
        raise LicenseError(MSG_MALFORMED, "license_malformed")
    return data[:-SIGNATURE_LEN], data[-SIGNATURE_LEN:]


def verify_signature(public_key, body: bytes, signature: bytes) -> None:
    from cryptography.exceptions import InvalidSignature

    try:
        public_key.verify(signature, DOMAIN + body)
    except InvalidSignature:
        raise LicenseError("Clé invalide : elle n'a pas été émise par Komersia.",
                           "license_bad_signature") from None


# --------------------------------------------------------------------------- code court


def short_code(installation_id: uuid.UUID) -> str:
    """Code lisible « XXXX-XXXX » dérivé de l'identifiant (40 bits), pour le dicter au téléphone."""
    digest = hashlib.sha256(b"komersia-installation-code\x00" + installation_id.bytes).digest()
    n = int.from_bytes(digest[:5], "big")
    chars = "".join(ALPHABET[(n >> (5 * (7 - i))) & 31] for i in range(8))
    return f"{chars[:4]}-{chars[4:]}"
