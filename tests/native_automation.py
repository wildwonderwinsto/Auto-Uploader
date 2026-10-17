"""Stage16 native profile UI and separate-process persistence; controlled providers only."""

import argparse
import hashlib
import json
import os
import shutil
from datetime import timedelta
from pathlib import Path
from threading import Event
from unittest.mock import patch

from automation_fixture import compose
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QLabel
from support import make_video, settle

from shortspublisher.app import build
from shortspublisher.automation import AutomationService
from shortspublisher.background import LaunchAgent
from shortspublisher.ui.automation import ProfileConsent, ProfileEditor
from shortspublisher.ui.watching import FolderConsent

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("prepare", "reopen"))
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--native-picker", action="store_true")
parser.add_argument("--picker-cancel-first", action="store_true")
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
folder = root / "incoming"
folder.mkdir(exist_ok=True)
if args.phase == "prepare":
    assert not (root / "data/library.sqlite3").exists()
    (root / "clock.txt").write_text("2030-01-01T00:00:00+00:00")
a = compose(root)
r = AutomationService(a.schedules)
sizes = []
with (
    patch.object(LaunchAgent, "os_status", return_value={"loaded": False, "pid": 0}),
    patch("requests.sessions.Session.request", side_effect=AssertionError("No real HTTP")),
):
    app, w = build(
        root / "data",
        account_service=a.uploads.accounts,
        upload_service=a.uploads,
        schedule_service=a.schedules,
    )
    w.show()
    QTimer.singleShot(
        600000 if args.native_picker else 120000, lambda: os._exit(2)
    )  # Bound a stalled modal test; no credentials are persisted.
    w.automation.activate()
    for controller in (w.accounts, w.videos, w.uploads, w.schedules, w.watching, w.automation):
        settle(app, controller)
    c = w.automation
    w.watching.service.clock = lambda: a.schedules.clock().timestamp()
    w.watching.timer.stop()
    w.watching.debounce.stop()
    w.watching.notifications.blockSignals(True)

    def capture(name):
        for width, height in ((760, 560), (1180, 780), (1600, 1000)):
            w.resize(width, height)
            app.processEvents()
            page = w.stack.currentWidget()
            assert page.horizontalScrollBar().maximum() == 0
            sizes.append(
                {"view": name, "requested": [width, height], "actual": [w.width(), w.height()]}
            )
            for suffix, value in (("top", 0), ("bottom", page.verticalScrollBar().maximum())):
                page.verticalScrollBar().setValue(value)
                app.processEvents()
                w.grab().save(str(root / f"{args.phase}-{name}-{width}-{suffix}.png"))

    def refresh():
        c.activate()
        settle(app, c)
        w.recurring.activate()
        settle(app, w.recurring)
        w.uploads.start("history")
        settle(app, w.uploads)

    def advance(seconds):
        (root / "clock.txt").write_text(
            (a.schedules.clock() + timedelta(seconds=seconds)).isoformat()
        )

    def scan():
        w.watching.service.scan(Event())
        advance(2)
        w.watching.service.scan(Event())
        refresh()

    original_editor = ProfileEditor.exec
    original_consent = ProfileConsent.exec
    original_folder = FolderConsent.exec

    def folder_consent(d):
        assert str(folder) in [label.text() for label in d.findChildren(QLabel)]
        d.show()
        app.processEvents()
        d.grab().save(str(root / "folder-consent.png"))
        d.existing.setCurrentIndex(2)
        d.quiet.setValue(1)
        d.consent.setChecked(True)
        QTimer.singleShot(10, d.approve.click)
        return original_folder(d)

    def editor(d):
        def choose():
            if args.native_picker:
                if args.picker_cancel_first:
                    print(
                        "Cancel the first real chooser; select the folder in the second.",
                        flush=True,
                    )
                    d.choose_folder()
                    assert (
                        not c.worker
                        and not w.watching.service.folders()
                        and not r.state()["profiles"]
                    )
                    (root / "picker-cancel.json").write_text(
                        json.dumps({"cancel_created_folders": 0, "cancel_created_profiles": 0})
                    )
                print("Choose this isolated folder: " + str(folder), flush=True)
                d.choose_folder()
            else:
                with patch.object(QFileDialog, "getExistingDirectory", return_value=str(folder)):
                    d.choose_folder()

            def configured():
                if (
                    c.worker
                    or d.folder.findData(
                        w.watching.service.folders()[0]["id"]
                        if w.watching.service.folders()
                        else ""
                    )
                    < 0
                ):
                    QTimer.singleShot(30, configured)
                    return
                d.mode.setCurrentIndex(d.mode.findData("automatic"))
                d.kids.setCurrentIndex(d.kids.findData(False))
                d.synthetic.setCurrentIndex(d.synthetic.findData(False))
                d.zone.setText("UTC")
                d.frequency.setCurrentIndex(d.frequency.findData(2))
                d.description.setPlainText("Original generated fixture; controlled transport only.")
                d.tags.setText("shorts, original")
                for i in range(d.steps.count()):
                    d.steps.setCurrentIndex(i)
                    app.processEvents()
                    scroll = d.steps.widget(i)
                    for suffix, value in (
                        ("top", 0),
                        ("middle", scroll.verticalScrollBar().maximum() // 2),
                        ("bottom", scroll.verticalScrollBar().maximum()),
                    ):
                        scroll.verticalScrollBar().setValue(value)
                        app.processEvents()
                        d.grab().save(str(root / f"profile-editor-{i}-{suffix}.png"))
                d.review.click()

            QTimer.singleShot(30, configured)

        QTimer.singleShot(30, choose)
        return original_editor(d)

    def consent(d):
        d.show()
        app.processEvents()
        assert not d.approve.isEnabled()
        d.grab().save(str(root / "profile-consent.png"))
        for box in (d.scope, d.declarations, d.restrictions):
            box.setChecked(True)
        QTimer.singleShot(10, d.approve.click)
        return original_consent(d)

    try:
        w.navigate("automation")
        settle(app, c)
        if args.phase == "prepare":
            a.startup()
            with (
                patch.object(ProfileEditor, "exec", editor),
                patch.object(ProfileConsent, "exec", consent),
                patch.object(FolderConsent, "exec", folder_consent),
            ):
                c.start("open")
                settle(app, c, timeout=600 if args.native_picker else 40)
            assert len(r.state()["profiles"]) == 1, c.feedback.text()
            assert a.status()["paused"]
            a.control(False)
            capture("configured")
            first = folder / "First_original.mp4"
            make_video(first, size="270x480", duration=3)
            scan()
            shutil.copyfile(first, folder / "Identical_renamed.mp4")
            scan()
            second = folder / "Second_original.mp4"
            make_video(second, size="270x480", duration=4)
            scan()
            assert len(r.recurring.state()["items"]) == 2, r.state()
            assert any(d["state"] == "Duplicate — skipped" for d in r.state()["decisions"])
            capture("enrolled")
            for expected in (first, second):
                (root / "clock.txt").write_text(r.recurring.state()["plans"][0]["next_slot"])
                result = a.dispatch(Event(), lambda _: None)
                assert not result.get("error"), result.get("error")
                assert expected.read_bytes() == (root / "received.bin").read_bytes()
            assert len(a.uploads.store.all()) == 2
            assert all(
                x.state == "Public Verified" and x.processing == "processed"
                for x in a.uploads.store.all()
            )
            refresh()
            capture("published")
            (root / "source-hashes.json").write_text(
                json.dumps(
                    {
                        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in folder.glob("*.mp4")
                    },
                    indent=2,
                )
            )
        else:
            a.startup()
            assert len(a.uploads.store.all()) == 2
            p = r.state()["profiles"][0]
            r.control(p["id"], p["revision"], "pause")
            make_video(folder / "Paused_arrival.mp4", duration=5)
            scan()
            assert len(r.recurring.state()["items"]) == 2
            advance(86400)
            r.control(p["id"], p["revision"], "resume")
            scan()
            a.dispatch(Event(), lambda _: None)
            assert len(a.uploads.store.all()) == 2
            assert len(r.recurring.state()["items"]) == 3
            refresh()
            capture("reopened")
            for path, digest in json.loads((root / "source-hashes.json").read_text()).items():
                assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        for page in ("queue", "calendar", "dashboard"):
            w.navigate(page)
            settle(app, w.schedules)
            capture(page)
        (root / f"{args.phase}-result.json").write_text(
            json.dumps(
                {
                    "sizes": sizes,
                    "attempts": len(a.uploads.store.all()),
                    "live_upload": False,
                    "qt_platform": os.environ.get("QT_QPA_PLATFORM", ""),
                    "native_picker_operated": args.native_picker,
                    "source_hashes_checked": args.phase == "reopen",
                },
                indent=2,
            )
        )
    finally:
        for controller in (w.automation, w.recurring, w.watching, w.automatic, w.background):
            controller.stop_for_quit()
        for controller in (w.accounts, w.videos, w.uploads, w.schedules):
            settle(app, controller)
        w.close()
        app.processEvents()
print(
    "Stage16 "
    + os.environ.get("QT_QPA_PLATFORM", "default")
    + " "
    + args.phase
    + " PASS; in-memory credentials, controlled transport",
    flush=True,
)
