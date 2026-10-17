"""Real Cocoa processes controlling an isolated actual LaunchAgent."""

import argparse
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from account_support import CHANNEL, CONFIG
from background_fixture import TestAgent, block_network, compose
from PySide6.QtCore import QDate, QTime, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox
from support import make_video, settle

from shortspublisher.accounts import Connection
from shortspublisher.app import build
from shortspublisher.auth import UPLOAD_SCOPES, Tokens
from shortspublisher.ui.execution import ActivationDialog
from shortspublisher.ui.scheduling import ScheduleConfirmation, ScheduleEditor

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("prepare", "active", "complete"))
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
if args.phase == "prepare":
    assert not (root / "data/library.sqlite3").exists()
    (root / "clock.txt").write_text("2030-01-01T00:00:00+00:00")
a = compose(root)
library = a.library
library.initialize()
accounts = a.uploads.accounts
repo = accounts.repository
credentials = accounts.credentials
sessions = a.uploads.sessions
http = a.uploads.http
store = a.uploads.store
schedules = a.schedules
manager = TestAgent(root)
if args.phase == "prepare":
    key = uuid.uuid4().hex
    credentials.put(
        key,
        Tokens(
            "native-stage10-synthetic-access",
            "native-stage10-synthetic-refresh",
            time.time() + 3600,
            UPLOAD_SCOPES,
        ),
    )
    repo.save_config(CONFIG)
    repo.save(Connection(key, CHANNEL, "2030-01-01T00:00:00+00:00"))
network = block_network()
network.start()
app, window = build(
    root / "data",
    account_service=accounts,
    upload_service=a.uploads,
    schedule_service=schedules,
    background_service=manager,
)
window.show()
settle(app, window.accounts)
c = window.automatic
c.activate()


def wait():
    deadline = time.monotonic() + 20
    while c.worker or c.control_worker:
        app.processEvents()
        time.sleep(0.005)
        assert time.monotonic() < deadline, "Execution worker timeout"
    app.processEvents()


def capture(name, page=None):
    page = page or window.stack.currentWidget()
    for width, height in ((1180, 780), (760, 560)):
        window.resize(width, height)
        app.processEvents()
        assert page.horizontalScrollBar().maximum() == 0, name
        for suffix, pos in (("top", 0), ("bottom", page.verticalScrollBar().maximum())):
            page.verticalScrollBar().setValue(pos)
            app.processEvents()
            assert window.grab().save(str(root / f"{name}-{width}-{suffix}.png"))


def capture_dialog(dialog, name):
    dialog.show()
    for width, height in ((1180, 780), (760, 560)):
        window.resize(width, height)
        app.processEvents()
        assert dialog.grab().save(str(root / f"{name}-{width}.png"))


real_editor, real_confirm, real_activate = (
    ScheduleEditor.exec,
    ScheduleConfirmation.exec,
    ActivationDialog.exec,
)
current_index = [0]


def editor(dialog):
    dialog.zone.setCurrentText("UTC")
    dialog.date.setDate(QDate(2030, 1, 2))
    dialog.time.setTime(QTime(12, current_index[0] * 5, 0))
    dialog.activate_after.setChecked(True)
    capture_dialog(dialog, "schedule-editor")
    QTimer.singleShot(20, dialog.review.click)
    return real_editor(dialog)


def confirmation(dialog):
    QTimer.singleShot(10, dialog.accept)
    return real_confirm(dialog)


def activation(dialog):
    dialog.background.setChecked(True)
    capture_dialog(dialog, "automatic-public-consent")
    QTimer.singleShot(10, dialog.accept)
    return real_activate(dialog)


def import_review(index):
    source = root / f"Original automatic publishing fixture 雪 {index}.mp4"
    make_video(source, size="720x1280", duration=5 - index)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    window.navigate("videos")
    page = window.videos
    settle(app, page)
    settle(app, window.uploads)
    with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(source)], "Videos")):
        page.import_button.click()
    settle(app, page)
    page.tabs.setCurrentIndex(1)
    e = page.editor
    e.title.setText("Controlled automatic Public publishing test — " + str(index))
    e.description.setPlainText(
        "Original generated portrait. Controlled Google transport only; no live upload."
    )
    e.kids.setCurrentIndex(2)
    e.synthetic.setCurrentIndex(2)
    e.privacy.setCurrentIndex(e.privacy.findData("public"))
    e.save_button.click()
    settle(app, page)
    page.tabs.setCurrentIndex(2)
    settle(app, page)
    page.review_button.click()
    settle(app, page)
    assert page.review_state.text().startswith("Review Complete")
    page.tabs.setCurrentIndex(4)
    current_index[0] = index
    with (
        patch.object(ScheduleEditor, "exec", editor),
        patch.object(ScheduleConfirmation, "exec", confirmation),
        patch.object(ActivationDialog, "exec", activation),
    ):
        window.schedule_button.click()
        settle(app, window.schedules)
        wait()
    job = next(j for j in schedules.all() if j.snapshot["path"] == str(source))
    assert job.execution_enabled == 1
    assert http.initiations == 0
    return job, digest


wait()
if args.phase == "prepare":
    job, digest = import_review(0)
    window.navigate("settings")
    settle(app, window.background)
    capture("settings-disabled")
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        window.background.buttons["enable"].click()
    settle(app, window.background)
    assert not manager.status()["pid"]
    window.background.buttons["start"].click()
    settle(app, window.background)
    deadline = time.monotonic() + 20
    while manager.status()["status"] not in ("Running", "Paused"):
        app.processEvents()
        time.sleep(0.1)
        assert time.monotonic() < deadline, manager.status()
    window.background.start("status")
    settle(app, window.background)
    capture("settings-running")
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        c.button.click()
    wait()
    (root / "prepare-result.json").write_text(
        json.dumps(
            {
                "job": job.id,
                "source": job.snapshot["path"],
                "sha256": digest,
                "gui_pid": os.getpid(),
                "worker_pid": manager.status()["pid"],
            },
            indent=2,
        )
    )
else:
    evidence = json.loads((root / "prepare-result.json").read_text())
    assert manager.status()["pid"]
    window.navigate("queue")
    settle(app, window.schedules)
    c.tick()
    wait()
    attempt = store.all()[0]
    if args.phase == "active":
        assert 0 < attempt.acknowledged < attempt.snapshot["size"], attempt
        capture("reattached-active-queue")
        window.navigate("dashboard")
        window.background.activate()
        settle(app, window.background)
        capture("reattached-active-dashboard")
        (root / "active-result.json").write_text(
            json.dumps(
                {
                    "gui_pid": os.getpid(),
                    "worker_pid": manager.status()["pid"],
                    "attempt": attempt.id,
                    "acknowledged": attempt.acknowledged,
                    "size": attempt.snapshot["size"],
                },
                indent=2,
            )
        )
    else:
        assert attempt.state == "Public Verified" and attempt.processing == "processed"
        assert len(store.all()) == 1
        assert Path(evidence["source"]).read_bytes() == (root / "received.bin").read_bytes()
        assert (
            hashlib.sha256(Path(evidence["source"]).read_bytes()).hexdigest() == evidence["sha256"]
        )
        capture("completed-queue")
        window.navigate("calendar")
        settle(app, window.schedules)
        window.schedules.calendar.calendar.setSelectedDate(QDate(2030, 1, 2))
        capture("completed-calendar")
        c.button.click()
        wait()
        assert c.data["paused"]
        window.navigate("settings")
        settle(app, window.background)
        capture("paused-settings")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            window.background.buttons["remove"].click()
        settle(app, window.background)
        assert manager.status()["status"] == "Not Installed"
        capture("removed-settings")
        for attempt in store.all():
            sessions.delete(attempt.id)
            assert sessions.get(attempt.id) is None
        credentials.delete(repo.connection().key)
        assert credentials.get(repo.connection().key) is None
        (root / "complete-result.json").write_text(
            json.dumps(
                {
                    "attempt": attempt.id,
                    "returned_id": attempt.remote_id,
                    "privacy": attempt.privacy,
                    "processing": attempt.processing,
                    "acknowledged": attempt.acknowledged,
                    "attempt_count": len(store.all()),
                    "service_removed": True,
                    "keychain_cleaned": True,
                },
                indent=2,
            )
        )
c.stop_for_quit()
wait()
for page in (window.accounts, window.videos, window.uploads, window.schedules, window.background):
    settle(app, page)
window.close()
app.processEvents()
network.stop()
print(
    "Stage 10 native "
    + args.phase
    + ": PASS — controlled Google, actual LaunchAgent and isolated Keychain",
    flush=True,
)
