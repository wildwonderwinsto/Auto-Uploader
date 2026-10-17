"""Stage 13 native workflow and visual audit; generated media and controlled Google only."""

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox
from support import settle
from test_execution_ui import ExecutionUiTests

from shortspublisher.background import LaunchAgent
from shortspublisher.ui.scheduling import ScheduleEditor

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=True)
ExecutionUiTests.setUpClass()
f = ExecutionUiTests()
with (
    patch.object(LaunchAgent, "os_status", return_value={"loaded": False, "pid": 0}),
    patch("requests.sessions.Session.request", side_effect=AssertionError("No live HTTP")),
):
    f.setUp()
    try:
        w, app = f.window, f.app
        p = w.videos
        sizes = []

        def capture(name):
            for width, height in ((760, 560), (1180, 780), (1600, 1000)):
                w.resize(width, height)
                app.processEvents()
                sizes.append(
                    {"view": name, "requested": [width, height], "actual": [w.width(), w.height()]}
                )
                assert p.horizontalScrollBar().maximum() == 0
                p.verticalScrollBar().setValue(0)
                app.processEvents()
                w.grab().save(str(args.root / f"{name}-{width}.png"))

        w.navigate("videos")
        settle(app, p)
        assert not p.card.isVisible()
        capture("library")
        p.continue_button.click()
        settle(app, p)
        p.editor.title.setText("Native unsaved draft")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            p.back_button.click()
        assert p.editor.dirty and p.card.isVisible()
        w.navigate("videos")
        settle(app, p)
        assert p.editor.dirty and not p.card.isVisible()
        capture("parked-draft")
        p.continue_button.click()
        assert p.editor.title.text() == "Native unsaved draft"
        p.save_and_review()
        settle(app, p)
        p.complete_review()
        settle(app, p)
        assert p.publish_next.isEnabled()
        capture("review")
        paths = []
        for name in ("New first.mp4", "New second.mp4"):
            path = Path(f.f.ui.fixture.root).resolve() / name
            path.write_bytes(f.f.ui.fixture.path.read_bytes())
            paths.append(str(path))
        p.start("import_many", paths)
        settle(app, p)
        assert p.selected.path == paths[0] and p.tabs.currentIndex() == 1
        assert p.editor.preparation.video_id == p.selected.id
        capture("new-import")
        p.back_button.click()
        settle(app, p)
        capture("multiple-library")
        w.show_upload_history()
        settle(app, p)
        assert w.uploads.video_id is None
        capture("history")
        w.navigate("accounts")
        settle(app, w.accounts)
        capture("account")
        w.accounts.diagnostics.set_expanded(True)
        capture("account-details")
        editor = ScheduleEditor(w.schedules, p.selected.id, w.accounts.view.connection.channel.id)
        editor.show()
        app.processEvents()
        editor.grab().save(str(args.root / "schedule-input.png"))
        editor.close()
        assert f.f.ui.fixture.http.initiations == 0
        (args.root / "result.json").write_text(
            json.dumps(
                {"sizes": sizes, "uploads": 0, "multi_import": paths, "unsaved_preserved": True},
                indent=2,
            )
        )
    finally:
        f.doCleanups()
        ExecutionUiTests.tearDownClass()
print("Stage 13 native workflow passed; zero upload requests")
