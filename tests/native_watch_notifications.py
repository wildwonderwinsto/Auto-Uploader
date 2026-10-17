"""Real Cocoa filesystem notifications plus normal periodic scan, without manual scan calls."""

import argparse
import json
import shutil
import time
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from support import make_video, settle

from shortspublisher.library import Library, LibraryService
from shortspublisher.media import Inspector
from shortspublisher.ui.watching import WatchPanel
from shortspublisher.watching import WatchService

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
folder = root / "incoming"
folder.mkdir()
source = root / "generated.mp4"
make_video(source)
library = Library(root / "library.sqlite3")
library.initialize()
service = WatchService(LibraryService(library, Inspector()))
service.add(service.preview(str(folder), Event()), consent=True, existing=True, quiet=1)
app = QApplication([])
panel = WatchPanel(service)
panel.set_expanded(True)
panel.show()
events = []
ticks = []
panel.notifications.directoryChanged.connect(lambda p: events.append(p))
panel.timer.timeout.connect(lambda: ticks.append(time.monotonic()))
with patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP")):
    panel.activate()
    settle(app, panel)
    started = time.monotonic()
    shutil.copyfile(source, folder / "Arrival.mp4")
    deadline = started + 20
    while len(library.all()) != 1:
        app.processEvents()
        QTest.qWait(20)
        assert time.monotonic() < deadline, panel.feedback.text()
    settle(app, panel)
    assert events and ticks, (events, ticks)
    assert panel.activity.count() == 1
    (root / "result.json").write_text(
        json.dumps(
            {
                "native_events": len(events),
                "periodic_ticks": len(ticks),
                "elapsed_seconds": time.monotonic() - started,
                "manual_scan_calls": 0,
                "imported": 1,
            },
            indent=2,
        )
    )
    panel.request_close()
    settle(app, panel)
    panel.close()
print("Stage 14 native notifications and periodic reconciliation PASS")
