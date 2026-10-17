"""Native visual audit with isolated real media/SQLite and controlled account responses."""

import argparse
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea
from support import settle
from test_execution_ui import ExecutionUiTests

from shortspublisher.background import LaunchAgent
from shortspublisher.ui.execution import ActivationDialog
from shortspublisher.ui.uploads import UploadConfirmation

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=True)
ExecutionUiTests.setUpClass()
f = ExecutionUiTests()
with patch.object(LaunchAgent, "os_status", return_value={"loaded": False, "pid": 0}):
    f.setUp()
    try:
        w, app = f.window, f.app
        sizes = []
        job = f.approve()
        if hasattr(w.videos, "preview"):
            w.navigate("videos")
            settle(app, w.videos)
            w.videos.continue_editing()
            settle(app, w.videos)
            w.videos.tabs.setCurrentIndex(1)
            w.resize(1180, 780)
            preview = w.videos.preview
            before = hashlib.sha256(f.f.ui.fixture.path.read_bytes()).hexdigest()
            preview.play.click()
            deadline = time.monotonic() + 10
            while (
                preview.player.position() < 300
                or not preview.video.videoSink().videoFrame().isValid()
            ):
                app.processEvents()
                QTest.qWait(20)
                assert time.monotonic() < deadline, preview.status.text()
            preview.player.pause()
            app.processEvents()
            w.videos.verticalScrollBar().setValue(w.videos.verticalScrollBar().maximum() // 2)
            app.processEvents()
            w.grab().save(str(args.root / "preview-playing.png"))
            assert hashlib.sha256(f.f.ui.fixture.path.read_bytes()).hexdigest() == before
            (args.root / "preview-result.json").write_text(
                json.dumps(
                    {
                        "decoded_frame": True,
                        "position_ms": preview.player.position(),
                        "source_unchanged": True,
                        "muted": preview.audio.isMuted(),
                    }
                )
            )
            preview.release()
        for route in w.indices:
            w.navigate(route)
            for controller in (w.videos, w.uploads, w.accounts, w.schedules, w.background):
                settle(app, controller)
            if route == "videos":
                w.grab().save(str(args.root / "library-landing.png"))
                w.videos.continue_editing()
                settle(app, w.videos)
            tabs = range(w.videos.tabs.count()) if route == "videos" else (None,)
            for tab in tabs:
                if tab is not None:
                    w.videos.tabs.setCurrentIndex(tab)
                for width, height in ((760, 560), (1180, 780), (1600, 1000)):
                    w.resize(width, height)
                    app.processEvents()
                    sizes.append(
                        {
                            "route": route,
                            "tab": tab,
                            "requested": [width, height],
                            "actual": [w.width(), w.height()],
                        }
                    )
                    target = w.stack.currentWidget()
                    for position in ("top", "bottom"):
                        if isinstance(target, QScrollArea):
                            bar = target.verticalScrollBar()
                            bar.setValue(0 if position == "top" else bar.maximum())
                        app.processEvents()
                        w.grab().save(str(args.root / f"{route}-{tab}-{width}-{position}.png"))
        f.f.c.service.cancel(job.id, job.revision)
        attempt = f.f.ui.fixture.prepare()
        for name, dialog in (
            ("public-activation", ActivationDialog(job, w)),
            ("private-confirmation", UploadConfirmation(attempt, w)),
        ):
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(args.root / f"{name}.png"))
            dialog.close()
        (args.root / "window-sizes.json").write_text(json.dumps(sizes, indent=2))
        assert f.f.ui.fixture.http.initiations == 0
    finally:
        f.doCleanups()
        ExecutionUiTests.tearDownClass()
