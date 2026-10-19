"""Apply `migrations/*.sql` in name order, each once, recorded in `schema_migrations`."""

import os
import sys
from importlib.resources import files

import psycopg


def migrate(database_url: str) -> list[str]:
    applied: list[str] = []
    scripts = sorted(
        (
            f
            for f in files("tidehold.platform").joinpath("migrations").iterdir()
            if f.name.endswith(".sql")
        ),
        key=lambda f: f.name,
    )
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "create table if not exists schema_migrations"
            " (name text primary key, applied_at timestamptz not null default now())"
        )
        # Serialises concurrent runs; released when the transaction ends.
        conn.execute("select pg_advisory_xact_lock(hashtext('tidehold.migrate'))")
        done = {row[0] for row in conn.execute("select name from schema_migrations")}
        for script in scripts:
            if script.name in done:
                continue
            conn.execute(script.read_text())
            conn.execute("insert into schema_migrations (name) values (%s)", (script.name,))
            applied.append(script.name)
    return applied


if __name__ == "__main__":
    for name in migrate(os.environ["DATABASE_URL"]):
        print(f"applied {name}", file=sys.stderr)
