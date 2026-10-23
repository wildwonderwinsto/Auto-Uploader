"""Additive v14→v15 migration: backup Buffer and Cloudinary accounts.

The saved Buffer app client (`account_config`) and Cloudinary account (`media_hosting`) stay the
main ones. Backups are listed in their own tables; each post remembers the Cloudinary account its
copy lives in and the Buffer sign-in it was sent with, so switching accounts never strands a copy
or a status check. Existing posts belong to the main accounts. No existing row changes meaning.
"""

import sqlite3

PLATFORMS = ("tiktok", "instagram", "facebook")
STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS media_hosts (
            credential_key TEXT PRIMARY KEY,
            cloud_name TEXT NOT NULL,
            api_key TEXT NOT NULL,
            position INTEGER NOT NULL,
            full_until TEXT,
            added_at TEXT NOT NULL
        )""",
    """CREATE TABLE IF NOT EXISTS buffer_accounts (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            client_id TEXT NOT NULL,
            redirect_uri TEXT NOT NULL,
            retry_after TEXT,
            position INTEGER NOT NULL,
            added_at TEXT NOT NULL
        )""",
    """-- Which Buffer account (app client) issued each stored sign-in; absent means the main one.
        CREATE TABLE IF NOT EXISTS buffer_sign_ins (
            credential_key TEXT PRIMARY KEY,
            account_id TEXT NOT NULL
        )""",
)


def columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def migrate(db: sqlite3.Connection) -> None:
    # Individual statements: executescript() would commit Library.initialize's transaction.
    for statement in STATEMENTS:
        db.execute(statement)
    if "full_until" not in columns(db, "media_hosting"):
        db.execute("ALTER TABLE media_hosting ADD COLUMN full_until TEXT")
    main = db.execute("SELECT credential_key FROM media_hosting WHERE id=1").fetchone()
    for platform in PLATFORMS:
        table = f"{platform}_posts"
        present = columns(db, table)
        for column in ("host_key", "buffer_key", "buffer_org"):
            if column not in present:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        if main:
            db.execute(
                f"UPDATE {table} SET host_key=? WHERE host_key='' AND hosted_id!=''", (main[0],)
            )
    db.execute("PRAGMA user_version=15")
