"""Cocoa recurring approval, real controlled transport and separate-process persistence."""

import argparse
import hashlib
import json
import os
import time
import uuid
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from account_support import CHANNEL, CONFIG
from background_fixture import TestAgent, block_network, compose
from PySide6.QtCore import Qt, QTime, QTimer
from support import make_video, settle
from test_preparation import VALID

from shortspublisher.accounts import Connection
from shortspublisher.app import build
from shortspublisher.auth import UPLOAD_SCOPES, Tokens
from shortspublisher.media import Inspector
from shortspublisher.preparation import PreparationStore
from shortspublisher.recurring import RecurringService
from shortspublisher.ui.recurring import BatchConfirmation, RecurringEditor

parser = argparse.ArgumentParser()
parser.add_argument(
    "phase", choices=("prepare", "reopen", "background-prepare", "background-reopen")
)
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
preparing = args.phase.endswith("prepare")
background = args.phase.startswith("background")
if preparing:
    assert not (root / "data/library.sqlite3").exists()
    (root / "clock.txt").write_text("2030-01-01T00:00:00+00:00")
a = compose(root)
a.library.initialize()
r = RecurringService(a.schedules)
accounts = a.uploads.accounts
manager = TestAgent(root)
if preparing:
    key = uuid.uuid4().hex
    accounts.credentials.put(
        key,
        Tokens("synthetic-stage15", "synthetic-stage15-refresh", time.time() + 3600, UPLOAD_SCOPES),
    )
    accounts.repository.save_config(CONFIG)
    accounts.repository.save(Connection(key, CHANNEL, "2030-01-01T00:00:00+00:00"))
    a.startup()
    sources = []
    for index in range(2):
        source = root / f"Generated recurring portrait 雪 {index}.mp4"
        make_video(source, size="720x1280", duration=3 + index)
        video, _ = a.library.save(str(source), Inspector().inspect(source, Event()))
        preps = PreparationStore(a.library)
        prep = preps.save(
            video.id, replace(VALID, privacy="public", title=f"Native recurring video {index}"), 0
        )
        preps.complete(video.id, prep.revision, video.inspection_revision)
        sources.append(
            {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
        )
    (root / "sources.json").write_text(json.dumps(sources, indent=2))
network = block_network()
network.start()
app, w = build(
    root / "data",
    account_service=accounts,
    upload_service=a.uploads,
    schedule_service=a.schedules,
    background_service=manager,
)
w.show()
w.recurring.activate()
settle(app, w.recurring)
for controller in (w.accounts, w.videos, w.uploads, w.schedules):
    settle(app, controller)
c = w.recurring
sizes = []


def capture(name, page=None):
    page = page or w.stack.currentWidget()
    for width, height in ((760, 560), (1180, 780), (1600, 1000)):
        w.resize(width, height)
        app.processEvents()
        sizes.append(
            {"view": name, "requested": [width, height], "actual": [w.width(), w.height()]}
        )
        assert page.horizontalScrollBar().maximum() == 0, name
        for suffix, position in (("top", 0), ("bottom", page.verticalScrollBar().maximum())):
            page.verticalScrollBar().setValue(position)
            app.processEvents()
            assert w.grab().save(str(root / f"{name}-{width}-{suffix}.png"))


def refresh():
    c.refresh()
    settle(app, c)


def editor(dialog):
    dialog.zone.setText("UTC")
    dialog.frequency.setCurrentIndex(dialog.frequency.findData(2))
    dialog.start_time.setTime(QTime(9, 0))
    dialog.end_time.setTime(QTime(21, 0))
    dialog.background.setChecked(background)
    for index in range(dialog.videos.count()):
        item = dialog.videos.item(index)
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            item.setCheckState(Qt.CheckState.Checked)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(root / "recurring-editor.png"))
    QTimer.singleShot(20, dialog.review.click)
    return original_editor(dialog)


def confirm(dialog):
    dialog.show()
    app.processEvents()
    assert not dialog.consent.isChecked() and not dialog.restrictions.isChecked()
    dialog.grab().save(str(root / "batch-consent.png"))
    dialog.consent.setChecked(True)
    dialog.restrictions.setChecked(True)
    assert dialog.approve.isEnabled()
    QTimer.singleShot(20, dialog.approve.click)
    return original_confirm(dialog)


original_editor = RecurringEditor.exec
original_confirm = BatchConfirmation.exec
try:
    if preparing:
        w.navigate("videos")
        settle(app, w.videos)
        capture("library")
        with (
            patch.object(RecurringEditor, "exec", editor),
            patch.object(BatchConfirmation, "exec", confirm),
        ):
            c.start("open")
            settle(app, c, timeout=30)
        assert len(r.state()["items"]) == 2, c.feedback.text()
        assert not r.state()["plans"][0]["enabled"]
        assert not a.uploads.store.all()
        w.navigate("queue")
        settle(app, w.schedules)
        capture("approved-paused")
        c.toggle()
        settle(app, c)
        assert r.state()["plans"][0]["enabled"]
        a.control(False)
        refresh()
        w.navigate("calendar")
        settle(app, w.schedules)
        capture("opportunities")
        w.navigate("dashboard")
        capture("dashboard")
        if background:
            manager.perform("enable", consent=True)
            manager.perform("start")
            deadline = time.monotonic() + 20
            while manager.status()["status"] not in ("Running", "Paused"):
                app.processEvents()
                time.sleep(0.1)
                assert time.monotonic() < deadline, manager.status()
        else:
            plan = r.state()["plans"][0]
            (root / "clock.txt").write_text(plan["next_slot"])
            a.uploads.http.delay = 0
            a.dispatch(Event(), lambda _: None)
            assert a.uploads.store.all()[0].state == "Public Verified"
            assert a.uploads.store.all()[0].processing == "processed"
            assert r.state()["items"][1]["state"] == "Queued"
            refresh()
            w.navigate("queue")
            settle(app, w.schedules)
            capture("first-published")
        (root / "prepare-result.json").write_text(
            json.dumps(
                {
                    "gui_pid": os.getpid(),
                    "worker_pid": manager.status()["pid"] if background else 0,
                    "next_slot": r.state()["plans"][0]["next_slot"],
                    "background": background,
                },
                indent=2,
            )
        )
    else:
        assert len(r.state()["plans"]) == 1 and len(r.state()["items"]) == 2
        if not background:
            assert len(a.uploads.store.all()) == 1
            a.startup()
            (root / "clock.txt").write_text(r.state()["plans"][0]["next_slot"])
            a.uploads.http.received.clear()
            a.uploads.http.delay = 0
            result = a.dispatch(Event(), lambda _: None)
            assert len(a.uploads.store.all()) == 2, result
            assert all(x.state == "Public Verified" for x in a.uploads.store.all())
        else:
            assert len(a.uploads.store.all()) == 1
            assert a.uploads.store.all()[0].state == "Public Verified"
        refresh()
        w.navigate("queue")
        settle(app, w.schedules)
        capture("reopened-results")
        plan = r.state()["plans"][0]
        r.control(plan["id"], plan["revision"], "pause")
        (root / "clock.txt").write_text(r.state()["plans"][0]["next_slot"])
        a.dispatch(Event(), lambda _: None)
        assert len(a.uploads.store.all()) == (1 if background else 2)
        for source in json.loads((root / "sources.json").read_text()):
            assert hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest() == source["sha256"]
        if background:
            manager.perform("remove")
            assert not manager.status()["loaded"] and not manager.plist.exists()
        for attempt in a.uploads.store.all():
            a.uploads.sessions.delete(attempt.id)
        accounts.credentials.delete(accounts.repository.connection().key)
        assert accounts.credentials.get(accounts.repository.connection().key) is None
        (root / "complete-result.json").write_text(
            json.dumps(
                {
                    "attempts": len(a.uploads.store.all()),
                    "source_hashes_unchanged": True,
                    "service_removed": not manager.plist.exists(),
                    "test_credentials_removed": True,
                    "live_upload": False,
                },
                indent=2,
            )
        )
    (root / f"{args.phase}-sizes.json").write_text(json.dumps(sizes, indent=2))
finally:
    for controller in (w.recurring, w.watching, w.automatic, w.background):
        controller.stop_for_quit()
    for controller in (w.accounts, w.videos, w.uploads, w.schedules):
        settle(app, controller)
    w.close()
    app.processEvents()
    network.stop()
print(f"Stage15 native {args.phase}: PASS; controlled Google only", flush=True)
