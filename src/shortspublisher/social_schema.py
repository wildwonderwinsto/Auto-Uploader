"""Additive v12→v13 migration: Instagram and Facebook via Buffer, one table set each, shaped like
TikTok's. No existing table changes; creates no connection, hosting or consent."""

import sqlite3

PLATFORMS = ("instagram", "facebook")
TEMPLATES = (
    """CREATE TABLE IF NOT EXISTS {p}_drafts (
            video_id INTEGER PRIMARY KEY,
            caption TEXT NOT NULL DEFAULT '',
            ai_generated INTEGER,
            updated_at TEXT NOT NULL
        )""",
    """CREATE TABLE IF NOT EXISTS {p}_posts (
            id TEXT PRIMARY KEY,
            source_sha256 TEXT NOT NULL,
            video_id INTEGER,
            source_path TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            origin TEXT NOT NULL CHECK (origin IN ('manual', 'automation')),
            profile_id TEXT,
            caption TEXT NOT NULL,
            ai_generated INTEGER NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('shareNow', 'customScheduled')),
            due_at TEXT,
            state TEXT NOT NULL,
            hosted_id TEXT NOT NULL DEFAULT '',
            hosted_url TEXT NOT NULL DEFAULT '',
            hosted_deleted_at TEXT,
            buffer_post_id TEXT NOT NULL DEFAULT '',
            buffer_status TEXT NOT NULL DEFAULT '',
            scheduling_type TEXT NOT NULL DEFAULT '',
            external_link TEXT NOT NULL DEFAULT '',
            sent_at TEXT,
            error TEXT NOT NULL DEFAULT '',
            submitted_at TEXT,
            next_check_at TEXT,
            checks INTEGER NOT NULL DEFAULT 0,
            released INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    """-- Same bytes, same account or Page: one live record until the user explicitly releases it.
        CREATE UNIQUE INDEX IF NOT EXISTS {p}_posts_one_per_destination
            ON {p}_posts(source_sha256, channel_id) WHERE released = 0""",
    """CREATE TABLE IF NOT EXISTS {p}_profiles (
            id TEXT PRIMARY KEY,
            revision INTEGER NOT NULL,
            name TEXT NOT NULL,
            folder_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            channel_name TEXT NOT NULL,
            settings TEXT NOT NULL,
            consent TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            revoked INTEGER NOT NULL DEFAULT 0,
            removed INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    """CREATE TABLE IF NOT EXISTS {p}_decisions (
            profile_id TEXT NOT NULL,
            profile_revision INTEGER NOT NULL,
            name TEXT NOT NULL,
            source_sha256 TEXT NOT NULL DEFAULT '',
            video_id INTEGER,
            state TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            post_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (profile_id, profile_revision, name)
        )""",
)


def migrate(db: sqlite3.Connection) -> None:
    # Individual statements: executescript() would commit Library.initialize's transaction.
    for platform in PLATFORMS:
        for template in TEMPLATES:
            db.execute(template.format(p=platform))
    db.execute("PRAGMA user_version=13")
