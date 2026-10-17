"""Additive v10→v11 migration. No standing authority is created on upgrade."""

import sqlite3


def migrate(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE automation_profiles (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, platform TEXT NOT NULL CHECK(platform='youtube'),
        folder_id TEXT NOT NULL REFERENCES watch_folders(id), folder_binding TEXT NOT NULL,
        channel_id TEXT NOT NULL, settings TEXT NOT NULL, revision INTEGER NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0,
        removed INTEGER NOT NULL DEFAULT 0, consent_at TEXT NOT NULL, consent TEXT NOT NULL,
        plan_id TEXT NOT NULL UNIQUE REFERENCES recurring_plans(id), created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT ''
    )""")
    db.execute("""CREATE TABLE automation_decisions (
        id TEXT PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES automation_profiles(id),
        profile_revision INTEGER NOT NULL, folder_id TEXT NOT NULL, name TEXT NOT NULL,
        source_hash TEXT NOT NULL DEFAULT '', video_id INTEGER, state TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '',
        preparation_revision INTEGER NOT NULL DEFAULT 0, media_token TEXT NOT NULL DEFAULT '',
        snapshot TEXT NOT NULL DEFAULT '', item_id TEXT UNIQUE REFERENCES recurring_items(id),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(profile_id,profile_revision,name)
    )""")
    db.execute("""CREATE TABLE automation_events (
        id INTEGER PRIMARY KEY, profile_id TEXT NOT NULL REFERENCES automation_profiles(id),
        revision INTEGER NOT NULL, created_at TEXT NOT NULL, action TEXT NOT NULL, record TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX automation_decisions_video ON automation_decisions(video_id)")
    db.execute("PRAGMA user_version=11")
