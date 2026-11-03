"""Application configuration.

Loads environment variables from .env and exposes them as settings.
"""

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from dotenv import load_dotenv

# Fichier d'env à charger — toujours résolu depuis la racine du projet (pas le
# répertoire courant du process, qui dépend d'où `uvicorn` est lancé).
# Permet de faire tourner l'app en local tout en pointant sur un autre backend
# Supabase (ex. celui du NAS) : `ENV_FILE=.env.nas uvicorn app.main:app --reload`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_env_path = Path(os.getenv("ENV_FILE", ".env"))
if not _env_path.is_absolute():
    _env_path = _PROJECT_ROOT / _env_path

load_dotenv(_env_path)

try:
    APP_VERSION: str = version("cardioanalysis-api")
except PackageNotFoundError:
    APP_VERSION = "0.1.0"

# Supabase Auth — vérification des JWT (voir app/auth/dependency.py).
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_JWT_SECRET: str = os.getenv("SUPABASE_JWT_SECRET", "")

# PostgreSQL — connexion applicative (voir app/services/db.py).
DB_HOST: str = os.getenv("DB_HOST", "")
DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
DB_NAME: str = os.getenv("DB_NAME", "")
DB_USER: str = os.getenv("DB_USER", "")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
