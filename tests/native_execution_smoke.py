"""Native Stage 9 scheduled execution. Isolated real Keychain; controlled Google only."""

import argparse
import hashlib
import json
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from account_support import CHANNEL, CONFIG, ControlledYouTube
from PySide6.QtCore import QDate, QTime, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox
from support import make_video, settle
from upload_support import ControlledUploadHTTP, UploadOAuth

from shortspublisher.accounts import AccountRepository, AccountService, Connection
from shortspublisher.app import build
from shortspublisher.auth import UPLOAD_SCOPES, Tokens
from shortspublisher.credentials import KeychainSessions, KeychainStore
from shortspublisher.library import Library
from shortspublisher.scheduling import ScheduleService
from shortspublisher.ui.execution import ActivationDialog
from shortspublisher.ui.scheduling import ScheduleConfirmation, ScheduleEditor
from shortspublisher.upload_store import UploadStore
from shortspublisher.uploads import UploadService

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("prepare", "reopen"))
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
library = Library(root / "data/library.sqlite3")
library.initialize()
repo = AccountRepository(library)
credentials = KeychainStore("ShortsPublisher.Stage9.NativeVerification")
sessions = KeychainSessions("ShortsPublisher.Stage9.NativeSessions")
youtube = ControlledYouTube()
accounts = AccountService(repo, credentials, UploadOAuth(), youtube)
http = ControlledUploadHTTP(CHANNEL.id, root / "controlled-remote.bin")
http.privacy = "public"
store = UploadStore(library)
uploads = UploadService(store, accounts, sessions, http, 256 * 1024)
clock = [datetime(2030, 1, 1, tzinfo=UTC)]
schedules = ScheduleService(library, lambda: clock[0])
if args.phase == "prepare":
    assert repo.connection() is None and not schedules.all(), "Use a fresh disposable root"
    key = uuid.uuid4().hex
    credentials.put(
        key,
        Tokens(
            "native-stage9-synthetic-access",
            "native-stage9-synthetic-refresh",
            time.time() + 3600,
            UPLOAD_SCOPES,
        ),
    )
    repo.save_config(CONFIG)
    repo.save(Connection(key, CHANNEL, "2030-01-01T00:00:00+00:00"))
else:
    clock[0] = datetime(2030, 1, 2, 13, tzinfo=UTC)

# Even an accidentally uninjected API operation cannot access Google in this harness.
network = patch(
    "requests.Session.request", side_effect=AssertionError("Unexpected real network request")
)
network.start()
app, window = build(
    root / "data", account_service=accounts, upload_service=uploads, schedule_service=schedules
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
    assert c.data["paused"] == 1
    capture("paused-dashboard")
    jobs, hashes = [], {}
    for index in range(3):
        job, digest = import_review(index)
        jobs.append(job)
        hashes[job.snapshot["path"]] = digest
    window.navigate("queue")
    settle(app, window.schedules)
    capture("activated-paused-queue")
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        c.button.click()
    wait()
    assert c.data["paused"] == 0
    http.delay = 0.35
    clock[0] = datetime.fromisoformat(jobs[0].scheduled_utc)
    # Actual Qt timeout invokes the dispatcher; no manual upload action or final modal.
    c.timer.start(1)
    deadline = time.monotonic() + 10
    while not any(a.acknowledged for a in store.all()):
        app.processEvents()
        time.sleep(0.005)
        assert time.monotonic() < deadline
    capture("automatic-progress")
    window.navigate("dashboard")
    capture("active-dashboard")
    wait()
    attempt = store.all()[0]
    assert attempt.state == "Public Verified" and attempt.processing == "processed"
    assert http.initiations == 1 and http.verify_calls >= 1
    assert bytes(http.received) == Path(jobs[0].snapshot["path"]).read_bytes()
    window.navigate("queue")
    settle(app, window.schedules)
    capture("completed-queue")
    window.navigate("calendar")
    settle(app, window.schedules)
    window.schedules.calendar.calendar.setSelectedDate(QDate(2030, 1, 2))
    capture("completed-calendar")
    c.button.click()  # Global pause needs no modal and does not modify remote content.
    wait()
    clock[0] = datetime.fromisoformat(jobs[1].scheduled_utc) + timedelta(seconds=61)
    c.timer.start(1)
    app.processEvents()
    wait()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        c.button.click()
    wait()
    assert schedules.get(jobs[1].id).state == "Missed"
    youtube.items = [replace(CHANNEL, id="UC_unexpected")]
    clock[0] = datetime.fromisoformat(jobs[2].scheduled_utc)
    c.timer.start(1)
    app.processEvents()
    time.sleep(0.01)
    app.processEvents()
    wait()
    assert schedules.get(jobs[2].id).state == "Needs Attention"
    assert http.initiations == 1
    window.navigate("queue")
    settle(app, window.schedules)
    capture("missed-and-blocked")
    evidence = {
        "jobs": [j.id for j in jobs],
        "attempts": [a.id for a in store.all()],
        "returned_id": attempt.remote_id,
        "privacy": attempt.privacy,
        "processing": attempt.processing,
        "acknowledged": attempt.acknowledged,
        "source_hashes": hashes,
        "initiations": http.initiations,
    }
    (root / "prepare-result.json").write_text(json.dumps(evidence, indent=2))
else:
    evidence = json.loads((root / "prepare-result.json").read_text())
    c.timer.start(1)
    app.processEvents()
    time.sleep(0.01)
    app.processEvents()
    wait()
    assert sorted(a.id for a in store.all()) == sorted(evidence["attempts"])
    assert http.initiations == 0 and not http.offsets
    completed = next(a for a in store.all() if a.remote_id)
    assert completed.remote_id == evidence["returned_id"] and completed.state == "Public Verified"
    assert len(store.all()) == 2  # One successful transfer; one blocked preflight, never initiated.
    window.navigate("queue")
    settle(app, window.schedules)
    capture("reopened-history")
    c.running = False
    c.timer.stop()
    for attempt in store.all():
        sessions.delete(attempt.id)
        assert sessions.get(attempt.id) is None
    connection = repo.connection()
    credentials.delete(connection.key)
    assert credentials.get(connection.key) is None
    (root / "restart-result.json").write_text(
        json.dumps(
            {
                "attempts": evidence["attempts"],
                "new_initiations": http.initiations,
                "keychain_cleaned": True,
            },
            indent=2,
        )
    )

for path, digest in evidence["source_hashes"].items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
c.stop_for_quit()
wait()
for page in (window.accounts, window.videos, window.uploads, window.schedules):
    settle(app, page)
window.close()
app.processEvents()
network.stop()
print(
    f"Stage 9 native {args.phase}: PASS. Controlled Google responses, real isolated Keychain; no live upload."
)
