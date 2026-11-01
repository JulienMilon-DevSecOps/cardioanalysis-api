# Image de cardioanalysis-api — construite, scannée et publiée par la pipeline
# centralisée (pipelines/python-fastapi-pipeline.yml) sur les tags V-x.y.z.
# Configuration 100 % par variables d'environnement (voir .env.example) :
# aucun fichier .env n'est copié dans l'image.

# ── Étape 1 : installation du projet et de ses dépendances dans un venv ───────
# Versions exactes de uv.lock (les mêmes que celles testées en CI), sans le groupe dev.
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Venv créé par uv hors du code, avec le Python de l'image (jamais téléchargé)
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    # Précompile les .pyc à l'installation (uv ne le fait pas par défaut, contrairement
    # à pip) : sans eux, avec PYTHONDONTWRITEBYTECODE, numpy/scipy/matplotlib seraient
    # recompilés à chaque démarrage (~18 s mesurées au lieu de quelques secondes)
    UV_COMPILE_BYTECODE=1

# Même version que UV_VERSION dans .gitlab-ci.yml
RUN pip install uv==0.13.6

WORKDIR /build

# Dépendances d'abord : couche mise en cache tant que uv.lock ne change pas
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

# Puis le projet, installé non éditable : le code et sa version (importlib.metadata)
# vont dans le venv (hatchling lit README.md et LICENCE)
COPY README.md LICENCE ./
COPY app ./app
RUN uv sync --frozen --no-dev --no-editable --no-cache

# ── Étape 2 : image d'exécution, sans outils de build ─────────────────────────
FROM python:3.13-slim

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
