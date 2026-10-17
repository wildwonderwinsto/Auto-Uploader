"""Nonsecret background configuration and OS-lock-backed dispatch ownership."""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from threading import Event
from typing import TYPE_CHECKING, Any

from filelock import FileLock, Timeout

from .auth import AccountError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .library import Library

SERVICE_CONSENT = (
    "Allow separately approved Public uploads while the desktop interface is closed. "
    "No second confirmation appears at execution time. The Mac must be logged in, awake and online. "
    "Keychain access may require approval. Global Pause prevents new starts; stopping does not "
    "delete remote content. Login startup is a separate choice."
)
BACKGROUND_CONSENT = (
    "I also authorize this exact scheduled PUBLIC upload through the background service, "
    "including while the desktop interface is closed."
)


def migrate(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE background_config (
        id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
        requested INTEGER NOT NULL DEFAULT 0 CHECK(requested IN (0,1)),
        login INTEGER NOT NULL DEFAULT 0 CHECK(login IN (0,1)), consent_at TEXT NOT NULL DEFAULT '',
        wording TEXT NOT NULL DEFAULT '')""")
    db.execute("INSERT INTO background_config(id) VALUES(1)")
    db.execute("""CREATE TABLE background_owner (
        id INTEGER PRIMARY KEY CHECK(id=1), token TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT '', pid INTEGER NOT NULL DEFAULT 0,
        heartbeat TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'Stopped',
        error TEXT NOT NULL DEFAULT '')""")
    db.execute("INSERT INTO background_owner(id) VALUES(1)")
    db.execute("""CREATE TABLE background_consent (
        job_id TEXT PRIMARY KEY REFERENCES scheduled_jobs(id), binding TEXT NOT NULL,
        revision INTEGER NOT NULL, approved_at TEXT NOT NULL, wording TEXT NOT NULL)""")
    db.execute("""CREATE TABLE execution_owner (
        execution_id TEXT PRIMARY KEY REFERENCES scheduled_executions(id),
        token TEXT NOT NULL, kind TEXT NOT NULL, pid INTEGER NOT NULL)""")
    db.execute("PRAGMA user_version=8")


def now() -> str:
    return datetime.now(UTC).isoformat()


def read(library: Library) -> dict[str, Any]:
    with closing(library.connect()) as db:
        result = dict(db.execute("SELECT * FROM background_config WHERE id=1").fetchone())
        result["owner"] = dict(db.execute("SELECT * FROM background_owner WHERE id=1").fetchone())
        return result


class Ownership:
    """Never steal an OS lock based on heartbeat age. Keep it until transfer/shutdown finishes."""

    def __init__(self, library: Library, kind: str = "foreground"):
        self.library, self.kind = library, kind
        self.token = uuid.uuid4().hex
        self.lock = FileLock(
            str(library.path.with_suffix(".dispatcher.lock")), mode=0o600, thread_local=False
        )
        self.depth = 0

    @contextmanager
    def acquire(self, wait: Event | None = None) -> Iterator[bool]:
        acquired = False
        while True:
            try:
                self.lock.acquire(timeout=0)
                acquired = True
                break
            except Timeout:
                if wait is None or wait.wait(1) or not read(self.library)["requested"]:
                    break
        if not acquired:
            yield False
            return
        entered = False
        try:
            with closing(self.library.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                config = db.execute("SELECT * FROM background_config WHERE id=1").fetchone()
                permitted = (
                    bool(config["enabled"] and config["requested"])
                    if self.kind == "background"
                    else not config["requested"]
                )
                if permitted:
                    if self.depth == 0:
                        db.execute(
                            "UPDATE background_owner SET token=?,kind=?,pid=?,heartbeat=?,state='Starting',error='' WHERE id=1",
                            (self.token, self.kind, os.getpid(), now()),
                        )
                    self.depth += 1
                    entered = True
            yield entered
        finally:
            try:
                if entered:
                    self.depth -= 1
                    if self.depth == 0:
                        with closing(self.library.connect()) as db, db:
                            db.execute(
                                "UPDATE background_owner SET state='Stopped',heartbeat=? WHERE id=1 AND token=?",
                                (now(), self.token),
                            )
            finally:
                self.lock.release()

    def heartbeat(self, state: str = "Running", error: str = "") -> None:
        if not self.lock.is_locked:
            raise AccountError("Dispatcher ownership was released.")
        with closing(self.library.connect()) as db, db:
            cursor = db.execute(
                "UPDATE background_owner SET heartbeat=?,state=?,error=? WHERE id=1 AND token=? AND pid=?",
                (now(), state, error, self.token, os.getpid()),
            )
            if cursor.rowcount != 1:
                raise AccountError("Dispatcher ownership changed; execution stopped.")


def validate_owner(db: sqlite3.Connection, execution_id: str, token: str) -> None:
    row = db.execute(
        "SELECT * FROM execution_owner WHERE execution_id=?", (execution_id,)
    ).fetchone()
    if row is None:
        # Old claimed attempts cannot acquire background scope through migration.
        config = db.execute("SELECT * FROM background_config WHERE id=1").fetchone()
        if config["requested"]:
            raise AccountError(
                "The background service owns scheduling. Inspect the existing attempt."
            )
        return
    owner = db.execute("SELECT * FROM background_owner WHERE id=1").fetchone()
    if (
        not token
        or row["token"] != token
        or owner["token"] != token
        or owner["pid"] != os.getpid()
        or owner["state"] == "Stopped"
    ):
        raise AccountError("Scheduled execution ownership is no longer valid.")
    if row["kind"] == "background":
        config = db.execute("SELECT * FROM background_config WHERE id=1").fetchone()
        consent = db.execute(
            """SELECT b.binding FROM background_consent b JOIN scheduled_executions e
            ON e.job_id=b.job_id WHERE e.id=? AND b.binding=e.binding AND b.revision=e.consent_revision""",
            (execution_id,),
        ).fetchone()
        if not config["enabled"] or not config["requested"] or not consent:
            raise AccountError("Background publication permission is missing or was stopped.")
