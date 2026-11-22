# Image du backend Komersia (Django + gunicorn).
# Construction : docker build -t komersia-backend ./backend
FROM python:3.13-slim

# uv : gestionnaire de dépendances (binaire copié depuis l'image officielle, version figée).
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Utilisateur non-root dédié.
RUN groupadd --system --gid 10001 komersia \
    && useradd --system --uid 10001 --gid komersia --home-dir /app --shell /usr/sbin/nologin komersia

WORKDIR /app

# Couche des dépendances (mise en cache tant que pyproject.toml / uv.lock ne changent pas).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Code de l'application.
COPY --chown=komersia:komersia . .

# Fichiers statiques (servis par WhiteNoise). Aucun accès base de données nécessaire ;
# la clé ci-dessous est un simple jeton de construction, jamais utilisé à l'exécution.
RUN DJANGO_SECRET_KEY=build-only-not-a-secret DJANGO_DEBUG=0 python manage.py collectstatic --noinput \
    && chown -R komersia:komersia /app

USER komersia

EXPOSE 8000

# Valeurs par défaut ; surchargées par docker-compose / l'orchestrateur.
ENV DJANGO_DEBUG=0 \
    GUNICORN_WORKERS=3

CMD ["sh", "-c", "exec gunicorn komersia.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS} --access-logfile - --error-logfile -"]
