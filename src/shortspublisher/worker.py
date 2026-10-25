"""Headless per-user publisher. Production has no simulation/provider switches."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
from contextlib import closing
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event, Thread

from .accounts import AccountRepository, AccountService
from .awake import Keeper
from .background_state import Ownership, read
from .credentials import KeychainSessions, KeychainStore
from .execution import AutomaticPublisher
from .library import SCHEMA_VERSION, Library, LibraryService
from .media import Cancelled, Inspector
from .scheduling import ScheduleService
from .upload_store import Attempt, UploadStore
from .uploads import UploadService
from .watching import POLL_SECONDS, WatchService


def run(publisher: AutomaticPublisher, cancel: Event, keeper: Keeper | None = None) -> int:
    """Dependency injection is for tests; the CLI always composes production services."""
    library = publisher.library
    publisher.ownership = Ownership(library, "background")
    owner = publisher.ownership
    log = logging.getLogger("shortspublisher.worker")
    # Keeps the Mac and its display awake while an automatic upload is planned.
    keeper = keeper or Keeper()
    with owner.acquire(wait=cancel) as acquired:
        if not acquired:
            log.warning("Worker not authorized or dispatcher already owned")
            return 2
        stop_monitor = Event()

        def monitor() -> None:
            while not stop_monitor.wait(2):
                try:
                    config = read(library)
                    if not config["enabled"] or not config["requested"]:
                        cancel.set()
                    with closing(library.connect()) as db:
                        paused = db.execute(
                            "SELECT paused FROM publishing_control WHERE id=1"
                        ).fetchone()[0]
                    owner.heartbeat(
                        "Stopping" if cancel.is_set() else "Paused" if paused else "Running"
                    )
                except Exception:
                    log.error("Worker health storage unavailable; stopping safely")
                    cancel.set()
                    return

        def discover() -> None:
            # launchd does not inherit the interactive shell's Homebrew PATH.
            executable = shutil.which("ffprobe") or shutil.which(
                "ffprobe", path="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
            )
            scanner = WatchService(
                LibraryService(library, Inspector(executable)),
                lambda: publisher.schedules.clock().timestamp(),
            )
            while not cancel.is_set() and not stop_monitor.is_set():
                try:
                    scanner.scan(cancel, background=True)
                except Cancelled:
                    return
                except Exception:
                    log.warning("Local discovery needs attention; retrying on reconciliation")
                if stop_monitor.wait(POLL_SECONDS):
                    return

        discovery_thread = Thread(target=discover, name="local-discovery", daemon=True)
        monitor_thread = Thread(target=monitor, name="publisher-health", daemon=True)
        try:
            publisher.startup()
            owner.heartbeat()
            monitor_thread.start()
            discovery_thread.start()
            log.info("Background worker initialized; dispatcher ownership acquired")
            previous: dict[str, str] = {}

            def progress(attempt: Attempt) -> None:
                if previous.get(attempt.id) != attempt.state:
                    log.log(
                        logging.WARNING
                        if attempt.state in ("Needs Reconciliation", "Interrupted", "Failed")
                        else logging.INFO,
                        "Upload %s: %s (%s acknowledged bytes)",
                        attempt.id,
                        attempt.state,
                        attempt.acknowledged,
                    )
                    previous[attempt.id] = attempt.state

            while not cancel.is_set():
                result = publisher.dispatch(cancel, progress)
                keeper.hold(bool(result.get("awake")))
                for attempt in result["attempts"]:
                    progress(attempt)
                for job in result["jobs"]:
                    if previous.get(job.id) != job.state:
                        log.log(
                            logging.WARNING
                            if job.state in ("Missed", "Needs Attention")
                            else logging.INFO,
                            "Schedule %s: %s",
                            job.id,
                            job.state,
                        )
                        previous[job.id] = job.state
                delay = 5.0
                for job in result["jobs"]:
                    if job.execution_enabled and job.state == "Scheduled":
                        remaining = (
                            datetime.fromisoformat(job.scheduled_utc) - publisher.schedules.clock()
                        ).total_seconds()
                        if remaining > 0:
                            delay = min(delay, max(0.01, remaining))
                cancel.wait(delay)
            log.info("Worker shutdown complete; no automatic resume")
            return 0
        except Exception:
            # The cause, so a stop is never a mystery (no credentials are ever logged).
            log.exception("Background operation failed safely; inspect persisted history")
            owner.heartbeat(
                "Error", "Background operation failed. Inspect history and restart explicitly."
            )
            return 1
        finally:
            keeper.hold(False)
            stop_monitor.set()
            cancel.set()
            if discovery_thread.is_alive():
                discovery_thread.join()
            if monitor_thread.is_alive():
                monitor_thread.join(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="ShortsPublisher authorized background worker")
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    directory = args.data_dir
    if not directory.is_absolute() or not (directory / "library.sqlite3").is_file():
        return 2
    os.umask(0o077)
    library = Library(directory / "library.sqlite3")
    # A worker cannot perform a migration or manufacture service consent.
    with closing(library.connect()) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            return 2
    config = read(library)
    if not config["enabled"] or not config["requested"] or not config["consent_at"]:
        return 2
    logs = directory / "background" / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    logger = logging.getLogger("shortspublisher")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(logs / "worker.log", maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    cancel = Event()
    signal.signal(signal.SIGTERM, lambda *_: cancel.set())
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    accounts = AccountService(AccountRepository(library), KeychainStore())
    uploads = UploadService(UploadStore(library), accounts, KeychainSessions())
    return run(AutomaticPublisher(ScheduleService(library), uploads), cancel)


if __name__ == "__main__":
    raise SystemExit(main())
