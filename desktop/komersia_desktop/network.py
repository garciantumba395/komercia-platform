"""Adresse du PC sur le réseau local : à communiquer aux autres appareils de la boutique."""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path


def local_hostname() -> str:
    return socket.gethostname()


def local_ip_addresses(*, hostname: str | None = None) -> list[str]:
    """Adresses IPv4 privées/locales du PC (hors loopback), la route par défaut en premier."""
    found: list[str] = []

    def add(ip: str) -> None:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return
        if addr.version == 4 and not addr.is_loopback and not addr.is_link_local and ip not in found:
            found.append(ip)

    try:  # aucune donnée n'est envoyée : on lit seulement l'interface choisie par le système
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(hostname or local_hostname(), None, socket.AF_INET):
            add(str(info[4][0]))
    except OSError:
        pass
    return found


def access_urls(port: int, bind: str, *, hostname: str | None = None,
                ips: list[str] | None = None) -> list[str]:
    """URL d'accès à `/app/`. Si l'écoute est limitée à 127.0.0.1, seul ce PC y accède."""
    if bind in ("127.0.0.1", "localhost", "::1"):
        return [f"http://localhost:{port}/app/"]
    ips = local_ip_addresses(hostname=hostname) if ips is None else ips
    urls = [f"http://{hostname or local_hostname()}:{port}/app/"]
    urls += [f"http://{ip}:{port}/app/" for ip in ips]
    return urls


def render_address_text(port: int, bind: str, *, hostname: str | None = None,
                        ips: list[str] | None = None) -> str:
    urls = access_urls(port, bind, hostname=hostname, ips=ips)
    if bind in ("127.0.0.1", "localhost", "::1"):
        return (
            "KOMERSIA - adresse d'accès\n\n"
            "Komersia est réglé pour ce PC uniquement (aucun autre appareil ne peut s'y connecter).\n"
            f"Ouvrez dans le navigateur de ce PC : {urls[0]}\n"
        )
    lines = [
        "KOMERSIA - adresse d'accès\n",
        "Sur ce PC, ouvrez : " + f"http://localhost:{port}/app/",
        "",
        "Sur un autre PC ou un téléphone connecté au MÊME réseau (Wi-Fi ou câble de la boutique),",
        "ouvrez le navigateur et tapez l'une de ces adresses :",
        "",
        *[f"    {u}" for u in urls],
        "",
        "Si le nom ne fonctionne pas, utilisez l'adresse avec des chiffres (IP).",
        "Le PC principal doit être allumé et Komersia démarré.",
        "",
    ]
    return "\n".join(lines)


def write_address_file(path: Path, port: int, bind: str, *, hostname: str | None = None,
                       ips: list[str] | None = None) -> str:
    text = render_address_text(port, bind, hostname=hostname, ips=ips)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")  # BOM : Bloc-notes affiche bien les accents
    return text
