import psycopg

from .config import get_database_url


def connect(timeout: int = 5):
    """Open a connection. Use as `with connect() as conn:` (commits on success)."""
    return psycopg.connect(get_database_url(), connect_timeout=timeout)