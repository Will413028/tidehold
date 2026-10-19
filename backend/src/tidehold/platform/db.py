from collections.abc import AsyncIterator, Sequence
from typing import LiteralString

from fastapi import Request
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row

type Conn = AsyncConnection[DictRow]


async def connection(request: Request) -> AsyncIterator[Conn]:
    """One connection and one transaction per request; committed when the handler returns."""
    database_url: str = request.app.state.settings.database_url
    async with await AsyncConnection.connect(database_url, row_factory=dict_row) as conn:
        yield conn


async def insert_returning(conn: Conn, query: LiteralString, params: Sequence[object]) -> DictRow:
    row = await (await conn.execute(query, params)).fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING returned no row")
    return row
