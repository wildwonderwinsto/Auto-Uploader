"""Separate-process native upload workflow. All Google responses are controlled.

Real Cocoa, FFprobe, SQLite, SHA-256 and isolated Keychain; no live Google calls.
"""

import argparse
import hashlib
import time
import uuid
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

from account_support import CHANNEL, CONFIG, ControlledYouTube
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox
from support import make_video, settle
from test_preparation import VALID
from upload_support import REMOTE_ID, SESSION, ControlledUploadHTTP, UploadOAuth

from shortspublisher.accounts import AccountRepository, AccountService, Connection
from shortspublisher.app import build
from shortspublisher.auth import AccountError, Tokens
from shortspublisher.credentials import KeychainSessions, KeychainStore
from shortspublisher.library import Library
from shortspublisher.media import Inspector
from shortspublisher.preparation import PreparationStore
from shortspublisher.ui.uploads import UploadConfirmation
from shortspublisher.upload_http import UploadReply
from shortspublisher.upload_store import UploadStore
from shortspublisher.uploads import UploadService

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("prepare", "reopen", "history"))
parser.add_argument("--root", required=True, type=Path)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
library = Library(root / "data" / "library.sqlite3")
library.initialize()
repo = AccountRepository(library)
credentials = KeychainStore("ShortsPublisher.Stage5.NativeVerification")
sessions = KeychainSessions("ShortsPublisher.Stage5.NativeSessions")
oauth = UploadOAuth()
accounts = AccountService(repo, credentials, oauth, ControlledYouTube())
http = ControlledUploadHTTP(CHANNEL.id, root / "controlled-remote.bin")
store = UploadStore(library)
service = UploadService(store, accounts, sessions, http, 256 * 1024)
source = root / "original.mp4"
if args.phase == "prepare":
    assert not store.all() and repo.connection() is None, "Use a new disposable root"
    make_video(source, size="720x1280", duration=3)
    (root / "original.sha256").write_text(hashlib.sha256(source.read_bytes()).hexdigest())
    video, _ = library.save(str(source), Inspector().inspect(source, Event()))
    prep = PreparationStore(library)
    saved = prep.save(video.id, replace(VALID, privacy="public"), 0)
    prep.complete(video.id, saved.revision, video.inspection_revision)
    key = uuid.uuid4().hex
    credentials.put(
        key, Tokens("native-synthetic-access", "native-synthetic-refresh", time.time() + 3600)
    )
    repo.save_config(CONFIG)
    repo.save(Connection(key, CHANNEL, "2026-10-16T00:00:00+00:00"))

app, window = build(root / "data", account_service=accounts, upload_service=service)
window.show()
window.navigate("accounts")
settle(app, window.accounts)
page = window.uploads


def capture(name, target=None):
    target = target or window.videos
    for width, height in ((1180, 780), (760, 560)):
        window.resize(width, height)
        app.processEvents()
        assert target.horizontalScrollBar().maximum() == 0, name
        for position in ("top", "bottom"):
            target.verticalScrollBar().setValue(
                0 if position == "top" else target.verticalScrollBar().maximum()
            )
            app.processEvents()
            assert window.grab().save(str(root / f"{name}-{width}-{position}.png"))


def confirm_dialog(dialog):
    dialog.show()
    app.processEvents()
    for width, height in ((1180, 780), (760, 560)):
        window.resize(width, height)
        app.processEvents()
        assert dialog.grab().save(str(root / f"confirmation-{width}.png"))
    QTimer.singleShot(20, dialog.accept)
    return real_exec(dialog)


real_exec = UploadConfirmation.exec
if args.phase == "prepare":
    assert not window.accounts.view.upload_allowed
    oauth.error = AccountError(
        "Controlled consent denial — existing connection preserved", "denied"
    )
    window.accounts.start("authorize_upload")
    settle(app, window.accounts)
    assert repo.connection() is not None
    capture("scope-denied-controlled", window.accounts)
    oauth.error = None
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        window.accounts.upload_permission.click()
    settle(app, window.accounts)
    assert window.accounts.view.upload_allowed, window.accounts.feedback.text()
    capture("scope-granted-controlled", window.accounts)

window.navigate("videos")
settle(app, window.videos)
settle(app, page)
window.videos.list.setCurrentRow(0)
settle(app, window.videos)
window.videos.tabs.setCurrentIndex(3)
app.processEvents()

if args.phase == "prepare":
    assert page.upload_button.isEnabled(), page.reason.text()
    with patch.object(UploadConfirmation, "exec", return_value=QDialog.DialogCode.Rejected):
        page.upload_button.click()
        settle(app, page)
    assert http.initiations == 0 and page.selected().state == "Cancelled"
    # Enter a real modal dialog, inspect it, then supply the harness confirmation.
    http.delay = 0.4
    gate = Event()
    http.after_chunk = lambda: gate.wait(10) if len(http.offsets) == 2 else None
    with patch.object(UploadConfirmation, "exec", confirm_dialog):
        page.upload_button.click()
        deadline = time.monotonic() + 8
        while len(http.offsets) < 2:
            app.processEvents()
            QTest.qWait(5)
            assert time.monotonic() < deadline
    capture("uploading-controlled")
    window.navigate("dashboard")
    window.navigate("videos")
    page.stop_button.click()
    gate.set()
    http.after_chunk = None
    settle(app, page)
    assert page.selected().state == "Paused", page.feedback.text()
    assert 0 < page.progress.value() < 100
    capture("paused-controlled")
    assert sessions.get(page.selected().id) == SESSION
    # Resume, then close while another request is active.
    offsets = len(http.offsets)
    page.start_selected("resume")
    deadline = time.monotonic() + 8
    while len(http.offsets) == offsets:
        app.processEvents()
        QTest.qWait(5)
        assert time.monotonic() < deadline
    assert not window.close()
    settle(app, page)
    assert store.all()[0].state == "Paused"
    assert http.initiations == 1
elif args.phase == "reopen":
    old = page.selected()
    assert old.state == "Paused" and old.acknowledged > 0
    assert http.initiations == 0 and http.offsets == []
    capture("reopened-paused-controlled")
    http.query_replies = [UploadReply(404)]
    page.start_selected("resume")
    settle(app, page)
    assert page.selected().state == "Needs Reconciliation"
    assert not page.upload_button.isEnabled()
    capture("needs-reconciliation-controlled")
    # Controlled server restores the same session; no new session or consent bypass.
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        page.resume_button.click()
    settle(app, page)
    assert page.selected().state == "Uploaded Private", page.feedback.text()
    assert page.selected().remote_id == REMOTE_ID
    assert page.selected().privacy == "private"
    assert http.initiations == 0
    assert http.offsets[0] == old.acknowledged
    assert bytes(http.received) == source.read_bytes()
    capture("uploaded-private-controlled")
else:
    assert page.selected().state == "Uploaded Private"
    assert page.selected().remote_id == REMOTE_ID
    assert page.progress.value() == 100
    assert http.initiations == 0 and not http.offsets
    capture("history-reopened-controlled")
    for attempt in store.all():
        sessions.delete(attempt.id)
        assert sessions.get(attempt.id) is None
    key = repo.connection().key
    credentials.delete(key)
    assert credentials.get(key) is None
    repo.clear()

assert window.close()
settle(app, page)
assert not window.isVisible()
assert hashlib.sha256(source.read_bytes()).hexdigest() == (root / "original.sha256").read_text()
for path in root.rglob("*"):
    if path.is_file() and path.suffix not in (".png", ".mp4", ".bin"):
        data = path.read_bytes()
        for secret in (
            b"native-synthetic-access",
            b"native-synthetic-refresh",
            b"synthetic-capability",
            b"test-access-sensitive",
            b"test-refresh-sensitive",
        ):
            assert secret not in data, path
print(
    f"Stage 5 native {args.phase}: PASS. Controlled Google responses, real isolated Keychain. No live upload."
)
