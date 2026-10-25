"""Keeps the Mac and its display awake while an automatic upload is planned.

Planned means auto uploading is on and something will upload at a later time without the user:
an activated scheduled upload, a queued video, an active watched folder, or an upload running now.
Posts already handed to Buffer are not counted: Buffer sends those itself.

macOS's `caffeinate` holds the power assertions. `-w` ties it to this process, so a quit or a
crash never leaves the Mac awake. The app and the background service each hold their own."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from collections.abc import Sequence

# -d display, -i idle sleep, -s system sleep on AC power. Closing the lid still sleeps the Mac.
COMMAND = ("/usr/bin/caffeinate", "-d", "-i", "-s")
BUFFER_TABLES = ("tiktok", "instagram", "facebook")
PLANNED = (
    """SELECT 1 FROM scheduled_jobs WHERE execution_enabled=1 AND state IN ('Scheduled','Due')""",
    """SELECT 1 FROM recurring_items i JOIN recurring_plans p ON p.id=i.plan_id
        WHERE p.enabled=1 AND i.state='Queued'""",
    """SELECT 1 FROM queue_items i JOIN queues q ON q.id=i.queue_id
        WHERE q.enabled=1 AND q.removed=0 AND i.state='Waiting'""",
    "SELECT 1 FROM automation_profiles WHERE enabled=1 AND revoked=0 AND removed=0",
    *(
        f"SELECT 1 FROM {p}_profiles WHERE enabled=1 AND revoked=0 AND removed=0"
        for p in BUFFER_TABLES
    ),
    "SELECT 1 FROM upload_attempts WHERE state IN ('Preparing','Uploading')",
)


def planned(db: sqlite3.Connection) -> bool:
    paused = db.execute("SELECT paused FROM publishing_control WHERE id=1").fetchone()
    if paused is None or paused[0]:
        return False
    return any(db.execute(f"{query} LIMIT 1").fetchone() for query in PLANNED)


class Keeper:
    """Holds `caffeinate` while wanted; safe to call on every refresh."""

    def __init__(self, command: Sequence[str] = COMMAND):
        self.command = tuple(command)
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def holding(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def hold(self, wanted: bool) -> None:
        if wanted and not self.holding:
            try:
                self.process = subprocess.Popen(
                    [*self.command, "-w", str(os.getpid())],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                self.process = None
        elif not wanted and self.process is not None:
            process, self.process = self.process, None
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
