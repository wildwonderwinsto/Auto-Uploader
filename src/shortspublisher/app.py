"""Desktop entry point; composition belongs here, not in individual pages."""

import argparse
import logging
import sys
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QStandardPaths, QTimer
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
    use_english_interface()
    theme.apply(app)
    directory = data_dir or Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    )
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
    app.aboutToQuit.connect(window.tiktok.stop_for_quit)
    app.aboutToQuit.connect(window.dashboard.stop_for_quit)
    if library.path.exists():
        QTimer.singleShot(0, window.accounts.activate)
    else:
        window.accounts.checked = True  # a new library has no saved YouTube account

    app.installEventFilter(window)
    return app, window


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
    args = parser.parse_args(argv)
    app, window = build(args.data_dir, args.ffprobe)
    app.aboutToQuit.connect(window.videos.stop_for_quit)
    directory = (
        args.log_dir
        or (args.data_dir / "logs" if args.data_dir else None)
        or Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        )
        / "logs"
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
