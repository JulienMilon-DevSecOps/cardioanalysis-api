"""Application configuration.

Loads environment variables from .env and exposes them as settings.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path("../.env"))
