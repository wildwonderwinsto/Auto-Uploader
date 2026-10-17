"""Real Settings failure and Stop-and-Quit against an empty paused test workspace."""

import argparse
import json
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox
from support import settle

from shortspublisher.app import build
from shortspublisher.background import LaunchAgent
from shortspublisher.library import Library

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
library = Library(root / "data/library.sqlite3")
library.initialize()
manager = LaunchAgent(library)
manager.label = "com.shortspublisher.test." + root.name
manager.login_directory = root / "login-agents"
app, window = build(root / "data", background_service=manager)
window.show()
window.navigate("settings")
settle(app, window.accounts)
page = window.background
settle(app, page)


def capture(name):
    for width, height in ((760, 560), (1180, 780)):
        window.resize(width, height)
        app.processEvents()
        assert page.horizontalScrollBar().maximum() == 0
        for suffix, pos in (("top", 0), ("bottom", page.verticalScrollBar().maximum())):
            page.verticalScrollBar().setValue(pos)
            app.processEvents()
            assert window.grab().save(str(root / f"{name}-{width}-{suffix}.png"))


try:
    runtime = manager.runtime
    manager.runtime = root / "missing-python"
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        page.buttons["enable"].click()
    settle(app, page)
    assert "missing" in page.status.text()
    assert not manager.plist.exists()
    capture("missing-runtime-error")
    manager.runtime = runtime
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        page.buttons["enable"].click()
    settle(app, page)
    page.buttons["start"].click()
    settle(app, page)
    deadline = time.monotonic() + 20
    while manager.status()["status"] not in ("Running", "Paused"):
        app.processEvents()
        time.sleep(0.1)
        assert time.monotonic() < deadline
    page.start("status")
    settle(app, page)
    capture("started-production-worker")
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        page.quit_button.click()
    settle(app, page, timeout=45)
    deadline = time.monotonic() + 10
    while window.isVisible():
        app.processEvents()
        time.sleep(0.05)
        assert time.monotonic() < deadline
    assert not manager.status()["pid"]
    (root / "result.json").write_text(
        json.dumps(
            {
                "missing_runtime_reported": True,
                "stop_and_quit_closed_gui": True,
                "worker_stopped": True,
            },
            indent=2,
        )
    )
finally:
    manager.runtime = runtime
    manager.perform("remove")
    for controller in (
        window.accounts,
        window.videos,
        window.uploads,
        window.schedules,
        window.background,
    ):
        settle(app, controller)
    window.close()
    app.processEvents()
print("Stage 10 native error presentation and full Stop-and-Quit: PASS")
