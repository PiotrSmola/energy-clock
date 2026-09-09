from typing import Any

from django.db.backends.base.base import BaseDatabaseWrapper

SQLITE_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
)


def apply_sqlite_pragmas(
    sender: type[BaseDatabaseWrapper], connection: BaseDatabaseWrapper, **kwargs: Any
) -> None:
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        for pragma in SQLITE_PRAGMAS:
            cursor.execute(pragma)
