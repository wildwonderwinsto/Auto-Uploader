"""Keeps the Mac and its display awake while an automatic upload is planned.

Planned means auto uploading is on and something will upload at a later time without the user:
an activated scheduled upload, a queued video, an active watched folder, or an upload running now.
Posts already handed to Buffer are not counted: Buffer sends those itself.

macOS's `caffeinate` holds the power assertions. `-w` ties it to this process, so a quit or a
crash never leaves the Mac awake. The app and the background service each hold their own.

While planned, this process also declares latency-critical work to macOS (an NSProcessInfo
activity), which exempts it from App Nap: a window hidden behind others still checks every few
seconds, so an upload starts within its 60-second window."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

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


# NSActivityUserInitiated (includes idle-sleep prevention) | NSActivityLatencyCritical.
ACTIVITY_OPTIONS = 0x00FFFFFF | 0xFF00000000
ACTIVITY_REASON = b"ShortsScheduler: an upload is planned at a set time"


class Activity:
    """An NSProcessInfo activity, called through the Objective-C runtime (no PyObjC needed).
    Anything unavailable (another OS, a missing framework) makes it a silent no-op."""

    def __init__(self) -> None:
        self.token: int | None = None
        self.runtime: ctypes.CDLL | None = None
        if sys.platform != "darwin":
            return
        try:
            ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
            runtime = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc") or "libobjc.dylib")
            runtime.objc_getClass.restype = ctypes.c_void_p
            runtime.objc_getClass.argtypes = [ctypes.c_char_p]
            runtime.sel_registerName.restype = ctypes.c_void_p
            runtime.sel_registerName.argtypes = [ctypes.c_char_p]
            runtime.objc_autoreleasePoolPush.restype = ctypes.c_void_p
            runtime.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
            self.runtime = runtime
        except (OSError, AttributeError):
            self.runtime = None

    def send(self, receiver: int, selector: bytes, *args: tuple[Any, Any]) -> int:
        """objc_msgSend with an exact prototype per call (required on Apple silicon)."""
        assert self.runtime is not None
        prototype = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, *(kind for kind, _ in args)
        )
        address = ctypes.cast(self.runtime.objc_msgSend, ctypes.c_void_p).value
        if not address:
            raise OSError("objc_msgSend is unavailable")
        call = prototype(address)
        selector_id = self.runtime.sel_registerName(selector)
        return call(receiver, selector_id, *(value for _, value in args)) or 0

    @property
    def active(self) -> bool:
        return self.token is not None

    def begin(self) -> None:
        if self.runtime is None or self.token is not None:
            return
        pool = self.runtime.objc_autoreleasePoolPush()
        try:
            info = self.send(self.runtime.objc_getClass(b"NSProcessInfo"), b"processInfo")
            reason = self.send(
                self.runtime.objc_getClass(b"NSString"),
                b"stringWithUTF8String:",
                (ctypes.c_char_p, ACTIVITY_REASON),
            )
            token = self.send(
                info,
                b"beginActivityWithOptions:reason:",
                (ctypes.c_uint64, ACTIVITY_OPTIONS),
                (ctypes.c_void_p, reason),
            )
            # Returned autoreleased: keep it until endActivity:, or the activity ends early.
            self.token = self.send(token, b"retain") if token else None
        except (OSError, AttributeError, ctypes.ArgumentError):
            self.token = None
        finally:
            self.runtime.objc_autoreleasePoolPop(pool)

    def end(self) -> None:
        if self.runtime is None or self.token is None:
            return
        token, self.token = self.token, None
        pool = self.runtime.objc_autoreleasePoolPush()
        try:
            info = self.send(self.runtime.objc_getClass(b"NSProcessInfo"), b"processInfo")
            self.send(info, b"endActivity:", (ctypes.c_void_p, token))
            self.send(token, b"release")
        finally:
            self.runtime.objc_autoreleasePoolPop(pool)


class Keeper:
    """Holds `caffeinate` (and the App Nap exemption) while wanted; safe to call on every
    refresh."""

    def __init__(self, command: Sequence[str] = COMMAND, activity: Activity | None = None):
        self.command = tuple(command)
        self.process: subprocess.Popen[bytes] | None = None
        self.activity = activity or Activity()

    @property
    def holding(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def hold(self, wanted: bool) -> None:
        if wanted:
            self.activity.begin()
        else:
            self.activity.end()
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
