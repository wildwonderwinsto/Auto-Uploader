"""Desktop entry point; composition belongs here, not in individual pages."""

import argparse
import logging
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QCoreApplication, QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from .accounts import AccountRepository, AccountService
from .background import LaunchAgent
from .credentials import KeychainSessions, KeychainStore
from .diagnostics import configure_logging
from .library import Library, LibraryService
from .media import Inspector
from .scheduling import ScheduleService
from .ui import theme
from .ui.formatting import use_english_interface
from .ui.icons import app_icon
from .ui.window import MainWindow
from .upload_store import UploadStore
from .uploads import UploadService


def build(
    data_dir: Path | None = None,
    ffprobe: str | None = None,
    account_service: AccountService | None = None,
    upload_service: UploadService | None = None,
    schedule_service: ScheduleService | None = None,
    background_service: LaunchAgent | None = None,
) -> tuple[QApplication, MainWindow]:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([])
    # The internal names locate the existing data folder and must not change.
    app.setApplicationName("ShortsPublisher")
    app.setOrganizationName("ShortsPublisher")
    app.setApplicationDisplayName("ShortsScheduler")
    app.setWindowIcon(app_icon())  # the scheduler mark in the Dock, not a generic document
    use_english_interface()
    theme.apply(app)
    directory = data_dir or default_data_dir()
    library = Library(directory / "library.sqlite3")
    accounts = account_service or AccountService(AccountRepository(library), KeychainStore())
    uploads = upload_service or UploadService(UploadStore(library), accounts, KeychainSessions())
    window = MainWindow(
        LibraryService(library, Inspector(ffprobe)),
        accounts,
        uploads,
        schedule_service,
        background_service,
    )
    app.aboutToQuit.connect(window.watching.stop_for_quit)
    app.aboutToQuit.connect(window.background.stop_for_quit)
    app.aboutToQuit.connect(window.automatic.stop_for_quit)
    app.aboutToQuit.connect(window.recurring.stop_for_quit)
    app.aboutToQuit.connect(window.automation.stop_for_quit)
    app.aboutToQuit.connect(window.uploads.stop_for_quit)
    app.aboutToQuit.connect(window.accounts.stop_for_quit)
    for social in window.socials:
        app.aboutToQuit.connect(social.stop_for_quit)
    app.aboutToQuit.connect(window.dashboard.stop_for_quit)
    if library.path.exists():
        QTimer.singleShot(0, window.accounts.activate)
    else:
        window.accounts.checked = True  # a new library has no saved YouTube account

    app.installEventFilter(window)
    return app, window


BACKUPS_KEPT = 10


def default_data_dir() -> Path:
    """~/Library/Application Support/ShortsPublisher/ShortsPublisher (the internal names locate
    the data folder and must not change)."""
    QCoreApplication.setApplicationName("ShortsPublisher")
    QCoreApplication.setOrganizationName("ShortsPublisher")
    return Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    )


def backup_library(directory: Path) -> Path | None:
    """A consistent copy of the library (SQLite's backup API, committed WAL data included),
    checked before it counts; the newest copies are kept. A new user has nothing to copy."""
    source = directory / "library.sqlite3"
    if not source.exists():
        return None
    backups = directory / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backups / f"library-before-launch-{stamp}.sqlite3"
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original,
        closing(sqlite3.connect(destination)) as copy,
    ):
        original.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The library backup failed its integrity check; nothing was opened.")
    for old in sorted(backups.glob("library-before-launch-*.sqlite3"))[:-BACKUPS_KEPT]:
        old.unlink()
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ShortsScheduler desktop")
    parser.add_argument("--log-dir", type=Path, help="Override the OS application log directory")
    parser.add_argument(
        "--smoke-test", action="store_true", help="Open, render, then close after 1s"
    )
    parser.add_argument(
        "--data-dir", type=Path, help="Override application data directory (for isolated testing)"
    )
    parser.add_argument("--ffprobe", help="Explicit path to the FFprobe executable")
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Copy the library to backups/ before opening (the ShortsScheduler.app does this)",
    )
    args = parser.parse_args(argv)
    if args.backup:
        backup_library(args.data_dir or default_data_dir())
    app, window = build(args.data_dir, args.ffprobe)
    app.aboutToQuit.connect(window.videos.stop_for_quit)
    directory = (
        args.log_dir
        or (args.data_dir / "logs" if args.data_dir else None)
        or default_data_dir() / "logs"
    )
    logger = configure_logging(directory)
    previous = sys.excepthook

    def report(
        kind: type[BaseException], value: BaseException, traceback: TracebackType | None
    ) -> None:
        logger.error("Unhandled application error", exc_info=(kind, value, traceback))
        previous(kind, value, traceback)
        if not issubclass(kind, KeyboardInterrupt):
            QMessageBox.critical(
                window,
                "Application error",
                "An unexpected error occurred. See the application log or terminal for details.",
            )

    sys.excepthook = report
    try:
        logger.info("Starting desktop shell")
        window.show()
        window.schedules.activate()
        window.automatic.activate()
        window.background.activate()
        if args.smoke_test:
            QTimer.singleShot(1000, window.close)
        code = app.exec()
        logger.info("Desktop closed with code %s", code)
        return code
    finally:
        sys.excepthook = previous
        logging.shutdown()
