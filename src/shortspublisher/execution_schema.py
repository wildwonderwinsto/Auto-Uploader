"""Transactional v6→v7 migration. Called within Library.initialize's transaction."""

import sqlite3


def migrate(db: sqlite3.Connection) -> None:
    # Rebuild the constrained table without altering old values or event JSON.
    sql = db.execute("SELECT sql FROM sqlite_master WHERE name='scheduled_jobs'").fetchone()[0]
    db.execute(
        sql.replace("scheduled_jobs", "scheduled_jobs_new", 1).replace(
            "CHECK(execution_enabled=0)", "CHECK(execution_enabled IN (0,1))"
        )
    )
    db.execute("INSERT INTO scheduled_jobs_new SELECT * FROM scheduled_jobs")
    db.execute("""CREATE TABLE schedule_events_new (
        job_id TEXT NOT NULL REFERENCES scheduled_jobs_new(id), revision INTEGER NOT NULL,
        record TEXT NOT NULL, PRIMARY KEY(job_id,revision))""")
    db.execute("INSERT INTO schedule_events_new SELECT * FROM schedule_events")
    db.execute("DROP TABLE schedule_events")
    db.execute("DROP TABLE scheduled_jobs")
    db.execute("ALTER TABLE scheduled_jobs_new RENAME TO scheduled_jobs")
    db.execute("ALTER TABLE schedule_events_new RENAME TO schedule_events")
    for column in ("video_id", "source_hash"):
        name = "video" if column == "video_id" else "hash"
        db.execute(f"""CREATE UNIQUE INDEX active_schedule_{name}
            ON scheduled_jobs(platform,channel_id,{column}) WHERE state NOT IN ('Draft','Cancelled')""")
    db.execute("""CREATE TABLE publishing_control (
        id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL CHECK(paused IN (0,1)),
        daily_limit INTEGER NOT NULL CHECK(daily_limit BETWEEN 1 AND 10), updated_at TEXT NOT NULL)""")
    db.execute("INSERT INTO publishing_control VALUES (1,1,3,'')")
    db.execute("""CREATE TABLE execution_consent (
        job_id TEXT PRIMARY KEY REFERENCES scheduled_jobs(id), revision INTEGER NOT NULL,
        binding TEXT NOT NULL, approved_at TEXT NOT NULL, wording TEXT NOT NULL)""")
    db.execute("""CREATE TABLE scheduled_executions (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES scheduled_jobs(id),
        attempt_id TEXT NOT NULL UNIQUE REFERENCES upload_attempts(id),
        consent_revision INTEGER NOT NULL, binding TEXT NOT NULL,
        claimed_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '',
        phase TEXT NOT NULL CHECK(phase IN ('Claimed','Started','Blocked')),
        error TEXT NOT NULL DEFAULT '')""")
    db.execute("""CREATE TRIGGER require_execution_consent BEFORE UPDATE OF execution_enabled
        ON scheduled_jobs WHEN NEW.execution_enabled=1 AND NOT EXISTS
        (SELECT 1 FROM execution_consent WHERE job_id=NEW.id)
        BEGIN SELECT RAISE(ABORT,'Explicit execution consent required'); END""")
    db.execute("PRAGMA user_version=7")
