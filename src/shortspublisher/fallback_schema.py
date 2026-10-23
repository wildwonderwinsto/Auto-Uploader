"""Additive v15→v16 migration: each backup Buffer account keeps its own standing sign-in (a Keychain
key) and the Buffer organizations it belongs to, so posting can fall back to it automatically and
the same Buffer account is never added twice. No existing row changes meaning."""

import sqlite3

COLUMNS = (
    ("credential_key", "TEXT NOT NULL DEFAULT ''"),
    ("organizations", "TEXT NOT NULL DEFAULT '[]'"),
)


def migrate(db: sqlite3.Connection) -> None:
    present = {row[1] for row in db.execute("PRAGMA table_info(buffer_accounts)")}
    for name, kind in COLUMNS:
        if name not in present:
            db.execute(f"ALTER TABLE buffer_accounts ADD COLUMN {name} {kind}")
    db.execute("PRAGMA user_version=16")
