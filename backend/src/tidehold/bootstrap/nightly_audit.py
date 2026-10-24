"""The nightly reconciliation (plan step 10a), run right before the nightly rebuild:

    DATABASE_URL=... RECONCILE_LOG_DIR=... PAYPAL_ENV=sandbox PAYPAL_CLIENT_ID=... \\
        PAYPAL_CLIENT_SECRET=... python -m tidehold.bootstrap.nightly_audit

Appends one JSON line per command and payment check, then a summary line, to
`RECONCILE_LOG_DIR/reconcile-<UTC date>.jsonl`, and prints the summary. Exits 1 when any row
is a mismatch, open or unread. The PayPal client sits on a read-only transport: nothing can be
sent to PayPal.
"""

import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2 as httpx

from tidehold.money import audit
from tidehold.money.paypal import PayPalClient, ReadOnlyTransport
from tidehold.platform.db import connect
from tidehold.platform.settings import (
    database_url_from_env,
    paypal_from_env,
    reconcile_log_dir_from_env,
)


def write_log(directory: Path, rows: list[audit.Row], at: datetime) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "summary": True,
        "at": at.isoformat(),
        "rows": len(rows),
        "verdicts": dict(Counter(row.verdict for row in rows)),
        "failed": audit.failed(rows),
    }
    path = directory / f"reconcile-{at.date().isoformat()}.jsonl"
    with path.open("a", encoding="utf-8") as log:
        for row in rows:
            log.write(json.dumps(row.as_json()) + "\n")
        log.write(json.dumps(summary) + "\n")
    return summary


async def _main() -> int:
    credentials = paypal_from_env()
    client = PayPalClient(
        credentials.client_id,
        credentials.client_secret,
        transport=ReadOnlyTransport(httpx.AsyncHTTPTransport()),
    )
    try:
        async with await connect(database_url_from_env()) as conn:
            rows = await audit.run(conn, client)
    finally:
        await client.aclose()
    summary = write_log(reconcile_log_dir_from_env(), rows, datetime.now(UTC))
    print(json.dumps(summary))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
