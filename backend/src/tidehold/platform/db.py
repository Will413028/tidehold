from collections.abc import AsyncIterator, Sequence
from typing import LiteralString

from fastapi import Request
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row

type Conn = AsyncConnection[DictRow]

# A stuck statement or lock fails the request instead of holding a connection indefinitely.
OPTIONS = "-c statement_timeout=10000 -c lock_timeout=5000"


async def connect(database_url: str) -> Conn:
    """Autocommit: each statement commits on its own; work that must be atomic uses
    `async with conn.transaction()`, so a transaction never spans an external call."""
    return await AsyncConnection.connect(
        database_url, autocommit=True, row_factory=dict_row, options=OPTIONS
    )


async def connection(request: Request) -> AsyncIterator[Conn]:
    """One connection per request."""
    async with await connect(request.app.state.settings.database_url) as conn:
        yield conn


async def insert_returning(conn: Conn, query: LiteralString, params: Sequence[object]) -> DictRow:
    row = await (await conn.execute(query, params)).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING returned no row")
    return row
