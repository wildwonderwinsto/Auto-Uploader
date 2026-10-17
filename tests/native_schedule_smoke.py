"""Native local planning workflow, separate processes, no network or real credentials."""

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from account_support import CHANNEL, CONFIG, ControlledOAuth, ControlledYouTube, MemoryCredentials
from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox
from support import make_video, settle

from shortspublisher.accounts import AccountRepository, AccountService, Connection
from shortspublisher.app import build
from shortspublisher.library import Library
from shortspublisher.scheduling import ScheduleService
from shortspublisher.ui.scheduling import ScheduleConfirmation, ScheduleEditor

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("prepare", "reopen"))
parser.add_argument("--root", required=True, type=Path)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
library = Library(root / "data/library.sqlite3")
library.initialize()
repo = AccountRepository(library)
if args.phase == "prepare":
    repo.save_config(CONFIG)
    repo.save(Connection("isolated-unused", CHANNEL, "2030-01-01T00:00:00+00:00"))
accounts = AccountService(repo, MemoryCredentials(), ControlledOAuth(), ControlledYouTube())
clock = [datetime(2030, 1, 1, tzinfo=UTC)]
scheduler = ScheduleService(library, lambda: clock[0])
network = patch(
    "requests.Session.request", side_effect=AssertionError("Scheduling must not access HTTP")
)
network.start()
app, window = build(root / "data", account_service=accounts, schedule_service=scheduler)
window.show()
settle(app, window.accounts)
c = window.schedules


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
    app.processEvents()
    for width, height in ((1180, 780), (760, 560)):
        window.resize(width, height)
        app.processEvents()
        assert dialog.grab().save(str(root / f"{name}-{width}.png"))


real_editor = ScheduleEditor.exec
real_confirmation = ScheduleConfirmation.exec


def confirmation(dialog):
    capture_dialog(dialog, "approval")
    QTimer.singleShot(10, dialog.accept)
    return real_confirmation(dialog)


def edit_and_approve(dialog):
    dialog.zone.setCurrentText("America/Chicago")
    dialog.date.setDate(QDate(2030, 1, 3 if dialog.job else 2))
    capture_dialog(dialog, "reschedule" if dialog.job else "editor")
    QTimer.singleShot(30, dialog.review.click)
    return real_editor(dialog)


def import_review(source, index):
    window.navigate("videos")
    settle(app, window.videos)
    settle(app, window.uploads)
    page = window.videos
    with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(source)], "Videos")):
        page.import_button.click()
    settle(app, page)
    page.tabs.setCurrentIndex(1)
    e = page.editor
    e.title.setText(
        (
            "Original scheduled portrait — a long descriptive title for small window verification "
            + str(index)
        )[:100]
    )
    e.description.setPlainText(
        "Original locally generated scheduling fixture. No remote publishing.\nUnicode: 雪"
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
    app.processEvents()
    assert window.schedule_button.isEnabled()
    with (
        patch.object(ScheduleEditor, "exec", edit_and_approve),
        patch.object(ScheduleConfirmation, "exec", confirmation),
    ):
        window.schedule_button.click()
    settle(app, c)
    assert not c.editor


if args.phase == "prepare":
    assert scheduler.all() == [], "Use a new disposable root"
    window.navigate("queue")
    settle(app, c)
    capture("empty-queue")
    sources = []
    for index in (1, 2):
        source = root / f"Original portrait scheduling example with a long filename 雪 {index}.mp4"
        make_video(source, duration=index + 1)
        sources.append(source)
        import_review(source, index)
    (root / "source-hashes.json").write_text(
        json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    )
    window.navigate("queue")
    settle(app, c)
    capture("queue")
    first = c.queue.selected()
    assert first
    window.navigate("calendar")
    settle(app, c)
    c.calendar.calendar.setSelectedDate(QDate(2030, 1, 2))
    capture("calendar-day")
    assert c.calendar.list.count() == 2
    first = c.calendar.selected()
    assert first is not None
    with (
        patch.object(ScheduleEditor, "exec", edit_and_approve),
        patch.object(ScheduleConfirmation, "exec", confirmation),
    ):
        c.calendar.edit.click()
    settle(app, c)
    assert scheduler.get(first.id).local_time.startswith("2030-01-03")
    capture("calendar-rescheduled")
    window.navigate("queue")
    settle(app, c)
    capture("queue-rescheduled")
    second = next(j for j in scheduler.all() if j.id != first.id)
    for i in range(c.queue.list.count()):
        c.queue.list.setCurrentRow(i)
        if c.queue.selected().id == second.id:
            break
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        c.queue.cancel.click()
    settle(app, c)
    assert scheduler.get(second.id).state == "Cancelled"
    c.queue.filter.setCurrentText("All plans")
    capture("cancelled")
    (root / "job-ids.json").write_text(json.dumps({"active": first.id, "cancelled": second.id}))
else:
    ids = json.loads((root / "job-ids.json").read_text())
    window.navigate("queue")
    settle(app, c)
    active = scheduler.get(ids["active"])
    assert active.state == "Scheduled"
    assert scheduler.get(ids["cancelled"]).state == "Cancelled"
    capture("reopened")
    clock[0] = datetime.fromisoformat(active.scheduled_utc)
    c.start("load")
    settle(app, c)
    assert scheduler.get(active.id).state == "Due"
    capture("due-unexecuted")
    clock[0] += timedelta(seconds=60)
    c.start("load")
    settle(app, c)
    assert scheduler.get(active.id).state == "Missed"
    capture("missed")
    source = Path(active.snapshot["path"])
    moved = source.with_suffix(".temporarily-moved")
    source.rename(moved)
    try:
        c.start("load")
        settle(app, c)
        assert scheduler.get(active.id).state == "Needs Attention"
        capture("attention")
    finally:
        moved.rename(source)
    window.navigate("calendar")
    settle(app, c)
    c.calendar.calendar.setSelectedDate(QDate(2030, 1, 3))
    capture("calendar-attention")
    for name, digest in json.loads((root / "source-hashes.json").read_text()).items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest
with library.connect() as db:
    assert db.execute("SELECT count(*) FROM upload_attempts").fetchone()[0] == 0
assert window.close()
settle(app, c)
settle(app, window.videos)
settle(app, window.uploads)
settle(app, window.accounts)
assert not window.isVisible()
network.stop()
print(
    f"Stage 8 native {args.phase}: PASS. Real local media/SQLite/widgets; zero upload attempts and all HTTP forbidden."
)
