"""Additive, transactional local-discovery schema. No publishing authority."""

import sqlite3


def migrate(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE watch_folders (
        id TEXT PRIMARY KEY, original_path TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        removed INTEGER NOT NULL DEFAULT 0 CHECK(removed IN (0,1)),
        background INTEGER NOT NULL DEFAULT 0 CHECK(background IN (0,1)),
        consent_at TEXT NOT NULL, initial_mode TEXT NOT NULL CHECK(initial_mode IN ('existing','new')),
        quiet_seconds REAL NOT NULL CHECK(quiet_seconds >= 1), revision INTEGER NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        scanned_at TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT ''
    )""")
    db.execute("""CREATE TABLE watch_files (
        folder_id TEXT NOT NULL REFERENCES watch_folders(id), name TEXT NOT NULL,
        signature TEXT NOT NULL, stable_since REAL NOT NULL, observations INTEGER NOT NULL,
        state TEXT NOT NULL, sha256 TEXT NOT NULL DEFAULT '', video_id INTEGER,
        failures INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
        PRIMARY KEY(folder_id,name)
    )""")
    db.execute("""CREATE TABLE content_registry (
        sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64), size INTEGER NOT NULL,
        first_seen TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE content_videos (
        sha256 TEXT NOT NULL REFERENCES content_registry(sha256), video_id INTEGER NOT NULL,
        path TEXT NOT NULL, signature TEXT NOT NULL,
        PRIMARY KEY(sha256,video_id)
    )""")
    db.execute("CREATE INDEX content_video_lookup ON content_videos(video_id)")
    db.execute("CREATE INDEX watch_file_hash ON watch_files(sha256)")
    db.execute("PRAGMA user_version=9")
