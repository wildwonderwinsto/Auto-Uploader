"""Additive v16→v17 migration: video queues that span platforms.

A queue keeps its name, posting times and platforms. Its YouTube videos stay in the recurring plan
it links to (Stage 10's tables and dispatcher, unchanged); its TikTok, Instagram and Facebook
videos wait here, in order, and are sent to Buffer one at a time. No existing table changes and no
queue, item or consent is created."""

import sqlite3

TABLES = (
    """CREATE TABLE IF NOT EXISTS queues (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        rule TEXT NOT NULL,
        platforms TEXT NOT NULL DEFAULT '[]',
        plan_id TEXT,
        background INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        removed INTEGER NOT NULL DEFAULT 0,
        revision INTEGER NOT NULL DEFAULT 1,
        consent TEXT NOT NULL,
        consent_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS queue_items (
        id TEXT PRIMARY KEY,
        queue_id TEXT NOT NULL REFERENCES queues(id),
        platform TEXT NOT NULL CHECK (platform IN ('tiktok', 'instagram', 'facebook')),
        video_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        state TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        post_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS queue_items_in_order
        ON queue_items(queue_id, platform, state, position)""",
)


def migrate(db: sqlite3.Connection) -> None:
    # Individual statements: executescript() would commit Library.initialize's transaction.
    for statement in TABLES:
        db.execute(statement)
    db.execute("PRAGMA user_version=17")
