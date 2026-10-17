"""Additive v9→v10 migration; no plan or consent created during upgrade."""

import sqlite3


def migrate(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE recurring_plans (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, platform TEXT NOT NULL CHECK(platform='youtube'),
        channel_id TEXT NOT NULL, channel_title TEXT NOT NULL, rule TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), background INTEGER NOT NULL CHECK(background IN (0,1)),
        revision INTEGER NOT NULL, approved_revision INTEGER NOT NULL,
        consent_at TEXT NOT NULL, consent TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        next_slot TEXT NOT NULL, watermark TEXT NOT NULL, approved_limit INTEGER NOT NULL CHECK(approved_limit BETWEEN 1 AND 10)
    )""")
    db.execute("""CREATE TABLE recurring_items (
        id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES recurring_plans(id), video_id INTEGER NOT NULL,
        channel_id TEXT NOT NULL, source_hash TEXT NOT NULL, snapshot TEXT NOT NULL, position INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('Queued','Reserved','Needs Attention','Removed')),
        reason TEXT NOT NULL DEFAULT '', approved_at TEXT NOT NULL, binding TEXT NOT NULL,
        job_id TEXT UNIQUE REFERENCES scheduled_jobs(id), removed_at TEXT NOT NULL DEFAULT ''
    )""")
    db.execute(
        "CREATE UNIQUE INDEX recurring_content_channel ON recurring_items(channel_id,source_hash) WHERE state!='Removed'"
    )
    db.execute("CREATE INDEX recurring_order ON recurring_items(plan_id,state,position,id)")
    db.execute("""CREATE TABLE recurring_slots (
        plan_id TEXT NOT NULL REFERENCES recurring_plans(id), instant TEXT NOT NULL,
        state TEXT NOT NULL, reason TEXT NOT NULL, through_time TEXT NOT NULL,
        item_id TEXT UNIQUE REFERENCES recurring_items(id), job_id TEXT UNIQUE REFERENCES scheduled_jobs(id),
        PRIMARY KEY(plan_id,instant)
    )""")
    db.execute("""CREATE TABLE recurring_events (
        id INTEGER PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES recurring_plans(id),
        created_at TEXT NOT NULL, action TEXT NOT NULL, record TEXT NOT NULL
    )""")
    db.execute("PRAGMA user_version=10")
