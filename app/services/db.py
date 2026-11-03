"""Database access — the only module allowed to import cardiolab.

Builds an :class:`HRVRepository` configured from the app settings. Routers
call this factory and handle the connection lifecycle (``with``) and any
``psycopg2`` errors themselves — this module returns a native cardiolab
object, it never touches FastAPI's request/response cycle.
"""

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
