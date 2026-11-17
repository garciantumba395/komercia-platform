"""Aucune ressource externe : les connexions sont instables, tout doit être servi par Komersia."""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent
FILES = [*WEB.glob("templates/**/*.html"), *WEB.glob("static/**/*.*")]
URL = re.compile(r"(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.IGNORECASE)
# Chaînes inertes tolérées : espaces de noms XML/SVG (jamais chargés).
ALLOWED = ("www.w3.org",)


def test_files_are_scanned():
    assert len(FILES) >= 6


def test_no_external_url_in_templates_and_static():
    offenders = []
    for path in FILES:
        text = path.read_text(encoding="utf-8")
        for match in URL.finditer(text):
            # ignore les commentaires JS « // texte » : exige un point dans un nom d'hôte réel
            if any(a in match.group(0) for a in ALLOWED):
                continue
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(WEB)}:{line}: {match.group(0)}")
    assert not offenders, "URL externe trouvée :\n" + "\n".join(offenders)
