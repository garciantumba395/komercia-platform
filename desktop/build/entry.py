"""Point d'entrée de l'exécutable gelé (KomersiaServer.exe)."""

import sys

from komersia_desktop.main import main

if __name__ == "__main__":
    sys.exit(main())
