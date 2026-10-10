# Image de cardioanalysis-api — construite, scannée et publiée par la pipeline
# centralisée (pipelines/python-fastapi-pipeline.yml) sur les tags V-x.y.z.
# Configuration 100 % par variables d'environnement (voir .env.example) :
# aucun fichier .env n'est copié dans l'image.

# ── Étape 1 : installation du projet et de ses dépendances dans un venv ───────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Fichiers nécessaires au build du package (hatchling lit README.md et LICENCE)
COPY pyproject.toml README.md LICENCE ./
COPY app ./app

# Installation non éditable : le code et sa version (importlib.metadata) vont dans le venv
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# ── Étape 2 : image d'exécution, sans outils de build ─────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

COPY --from=builder /opt/venv /opt/venv

# Exécution sans root
RUN useradd --create-home --uid 10001 appuser
USER appuser
WORKDIR /home/appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

# Configuration par variables d'environnement (DB_*, SUPABASE_*) : env_file du
# docker-compose, jamais de .env dans l'image
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
