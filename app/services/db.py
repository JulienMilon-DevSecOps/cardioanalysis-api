"""Database access — the only module allowed to import cardiolab.

Builds an :class:`HRVRepository` configured from the app settings. Routers
call this factory and handle the connection lifecycle (``with``) and any
``psycopg2`` errors themselves — this module returns a native cardiolab
object, it never touches FastAPI's request/response cycle.
"""

import psycopg2
from cardiolab.database.repository import HRVRepository

from app.config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER


def get_repository() -> HRVRepository:
    """Build an ``HRVRepository`` configured from the app settings."""
    return HRVRepository(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
    )


def get_connection():
    """Open a plain ``psycopg2`` connection from the app settings.

    For the API's own SQL (migrations, API tables); the caller closes it.
    """
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT,
        connect_timeout=10,
    )
