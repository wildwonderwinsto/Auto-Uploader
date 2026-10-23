"""v13→v14 migration: ShortsScheduler sets no daily upload limit of its own any more; only
YouTube's own 100 uploads a day remains.

Two CHECK constraints are loosened (`BETWEEN 1 AND 10` → `BETWEEN 1 AND 100`) and the global
limit becomes 100. Plans and profiles already approved keep the cap they were approved with. `recurring_plans` is referenced by four tables, so it can't be rebuilt inside the
transaction; this uses SQLite's documented in-place procedure for relaxing constraints (edit the
stored CREATE statement, bump schema_version, check integrity), which existing rows satisfy by
definition.
"""

import sqlite3

from .library import LibraryError

LOOSENED = (("publishing_control", "daily_limit"), ("recurring_plans", "approved_limit"))


def migrate(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA schema_version").fetchone()[0]
    db.execute("PRAGMA writable_schema=ON")
    try:
        for table, column in LOOSENED:
            sql = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            old, new = f"CHECK({column} BETWEEN 1 AND 10)", f"CHECK({column} BETWEEN 1 AND 100)"
            if sql.count(new) == 1 and old not in sql:
                continue  # already loosened
            if sql.count(old) != 1:
                raise LibraryError(
                    f"Unexpected {table} definition in the library; the database was not changed."
                )
            db.execute(
                "UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?",
                (sql.replace(old, new), table),
            )
        db.execute(f"PRAGMA schema_version={version + 1}")
    finally:
        db.execute("PRAGMA writable_schema=OFF")
    db.execute("UPDATE publishing_control SET daily_limit=100")
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise LibraryError("The library failed its integrity check; the database was not changed.")
    db.execute("PRAGMA user_version=14")
