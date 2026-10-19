"""Read a recorder directory into the evidence store (plan step 7).

    DATABASE_URL=... python -m tidehold.bootstrap.ingest_warnings /var/lib/tidehold-recorder

Re-running is harmless: each warning is stored once with its earliest fetch time.
"""

import asyncio
import json
import sys
from pathlib import Path

from tidehold.platform.db import connect
from tidehold.platform.migrate import migrate
from tidehold.platform.settings import database_url_from_env
from tidehold.warnings import store


async def _main(paths: list[Path]) -> dict[str, int]:
    url = database_url_from_env()
    migrate(url)
    async with await connect(url) as conn, conn.transaction():
        return await store.ingest(conn, store.envelopes(paths))


if __name__ == "__main__":
    match sys.argv[1:]:
        case [directory]:
            paths = sorted(Path(directory).glob("*/*/*.json.gz"))
            print(json.dumps(asyncio.run(_main(paths))))
        case _:
            raise SystemExit(__doc__)
