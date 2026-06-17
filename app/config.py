"""Application configuration.

Loads environment variables from .env and exposes them as settings.
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path("../.env"))

try:
    APP_VERSION: str = version("cardioanalysis-api")
except PackageNotFoundError:
    APP_VERSION = "0.1.0"
