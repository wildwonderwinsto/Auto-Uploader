"""Stage 14 Cocoa workflow, generated media, real persistence; no publishing providers."""

import argparse
import hashlib
import json
import shutil
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from support import make_video, settle

from shortspublisher.app import build
from shortspublisher.background import LaunchAgent
from shortspublisher.ui.watching import FolderConsent

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("prepare", "reopen"))
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--native-picker", action="store_true")
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
folder = root / "incoming"
folder.mkdir(exist_ok=True)
sizes = []
with (
    patch.object(LaunchAgent, "os_status", return_value={"loaded": False, "pid": 0}),
    patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP allowed")),
):
    app, w = build(root / "data")
    w.show()
    w.navigate("videos")
    panel, page = w.watching, w.videos
    settle(app, panel)
    settle(app, page)
    panel.set_expanded(True)
    tick = [100.0 if args.phase == "prepare" else 1000.0]
    panel.service.clock = lambda: tick[0]
    panel.timer.stop()
    panel.debounce.stop()
    panel.notifications.blockSignals(
        True
    )  # Deterministic scan clock; event/reconciliation paths have dedicated UI tests.

    def drain():
        settle(app, panel)
        settle(app, page)
        app.processEvents()

    def scan(elapsed=0):
        tick[0] += elapsed
        panel.start("scan")
        drain()

    def capture(name):
        for width, height in ((760, 560), (1180, 780), (1600, 1000)):
            w.resize(width, height)
            app.processEvents()
            sizes.append(
                {"view": name, "requested": [width, height], "actual": [w.width(), w.height()]}
            )
            assert page.horizontalScrollBar().maximum() == 0
            for position in ("top", "bottom"):
                page.verticalScrollBar().setValue(
                    0 if position == "top" else page.verticalScrollBar().maximum()
                )
                app.processEvents()
                w.grab().save(str(root / f"{args.phase}-{name}-{width}-{position}.png"))

    if args.phase == "prepare":
        assert not panel.service.folders()

        def consent():
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, FolderConsent):
                dialog.existing.setCurrentIndex(1)
                dialog.consent.setChecked(True)
                dialog.grab().save(str(root / "consent.png"))
                dialog.approve.click()
            else:
                QTimer.singleShot(100, consent)

        QTimer.singleShot(100, consent)
        if args.native_picker:
            print("NATIVE PICKER READY: " + str(folder), flush=True)
            panel.choose_folder()
        else:
            with patch.object(QFileDialog, "getExistingDirectory", return_value=str(folder)):
                panel.choose_folder()
        drain()
        assert len(panel.service.folders()) == 1
        capture("configured")
        source = root / "generated.mp4"
        make_video(source, size="270x480", duration=3)
        raw = source.read_bytes()
        target = folder / "Slow portrait.mp4"
        with target.open("wb") as output:
            for end in (len(raw) // 3, 2 * len(raw) // 3, len(raw)):
                start = output.tell()
                output.write(raw[start:end])
                output.flush()
                scan(20)
                assert not panel.service.library.all(), "Premature import"
                QTest.qWait(100)
        panel.results.set_expanded(True)
        capture("waiting")
        scan(16)
        assert len(panel.service.library.all()) == 1
        capture("imported")
        video = panel.service.library.all()[0]
        page.search.setText("Slow portrait")
        assert page.list.count() == 1
        page.list.setCurrentRow(0)
        drain()
        page.preview.toggle()
        deadline = time.monotonic() + 15
        while page.preview.player.position() < 300:
            app.processEvents()
            QTest.qWait(20)
            assert time.monotonic() < deadline
        page.preview.player.pause()
        capture("preview")
        page.back_button.click()
        drain()
        shutil.copyfile(target, folder / "Same bytes.mp4")
        scan()
        scan(16)
        target.rename(folder / "Renamed portrait.mp4")
        scan()
        scan(16)
        assert len(panel.service.library.all()) == 1
        capture("duplicates")
        (folder / "invalid.mp4").write_bytes(b"not a video")
        scan()
        scan(16)
        capture("invalid")
        (root / "source-hash.json").write_text(
            json.dumps({"sha256": hashlib.sha256(raw).hexdigest()})
        )
    else:
        assert len(panel.service.folders()) == 1
        assert len(panel.service.library.all()) == 1
        scan()
        scan(16)
        assert len(panel.service.library.all()) == 1
        make_video(folder / "Distinct.mp4", duration=4)
        scan()
        scan(16)
        assert len(panel.service.library.all()) == 2
        drain()
        panel.pause.click()
        drain()
        assert not panel.current()["enabled"]
        make_video(folder / "Paused arrival.mp4", duration=5)
        scan(30)
        assert len(panel.service.library.all()) == 2
        capture("paused")
        drain()
        panel.pause.click()
        drain()
        assert panel.current()["enabled"]
        scan()
        scan(16)
        assert len(panel.service.library.all()) == 3
        capture("resumed")
        expected = json.loads((root / "source-hash.json").read_text())["sha256"]
        for name in ("Same bytes.mp4", "Renamed portrait.mp4"):
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
    with closing(panel.service.library.connect()) as db:
        for table in ("upload_attempts", "scheduled_jobs", "preparations"):
            assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
    (root / f"{args.phase}-result.json").write_text(
        json.dumps(
            {
                "sizes": sizes,
                "videos": len(panel.service.library.all()),
                "zero_uploads": True,
                "native_picker": args.native_picker,
            },
            indent=2,
        )
    )
    panel.request_close()
    drain()
    w.close()
    app.processEvents()
print("Stage 14 native " + args.phase + " PASS", flush=True)
