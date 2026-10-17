"""Stage 12 native, separate-process local-only browsing and decoder verification."""

import argparse
import hashlib
import json
import shutil
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from account_support import CHANNEL, CONFIG, ControlledYouTube, MemoryCredentials
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from support import make_video, settle
from test_preparation import VALID
from upload_support import ControlledUploadHTTP, MemorySessions, UploadOAuth

from shortspublisher.accounts import AccountRepository, AccountService, Connection
from shortspublisher.app import build
from shortspublisher.auth import UPLOAD_SCOPES, Tokens
from shortspublisher.background import LaunchAgent
from shortspublisher.library import Library
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
repo.save_config(CONFIG)
repo.save(Connection("stage12-controlled", CHANNEL, "2026-10-17T00:00:00+00:00"))
credentials = MemoryCredentials()
credentials.put(
    "stage12-controlled",
    Tokens("controlled-access", "controlled-refresh", time.time() + 3600, UPLOAD_SCOPES),
)
accounts = AccountService(repo, credentials, UploadOAuth(), ControlledYouTube())
http = ControlledUploadHTTP(CHANNEL.id)
uploads = UploadService(UploadStore(library), accounts, MemorySessions(), http)
sizes = []


def wait_until(predicate):
    deadline = time.monotonic() + 12
    while not predicate():
        app.processEvents()
        QTest.qWait(20)
        assert time.monotonic() < deadline, page.preview.status.text()


def capture(name):
    for width, height in ((760, 560), (1180, 780), (1600, 1000)):
        window.resize(width, height)
        app.processEvents()
        sizes.append(
            {
                "screen": name,
                "requested": [width, height],
                "actual": [window.width(), window.height()],
            }
        )
        assert page.horizontalScrollBar().maximum() == 0
        for position in ("top", "bottom"):
            bar = page.verticalScrollBar()
            bar.setValue(0 if position == "top" else bar.maximum())
            app.processEvents()
            window.grab().save(str(root / f"{name}-{width}-{position}.png"))


with (
    patch.object(LaunchAgent, "os_status", return_value={"loaded": False, "pid": 0}),
    patch("requests.sessions.Session.request", side_effect=AssertionError("No real HTTP allowed")),
):
    app, window = build(root / "data", account_service=accounts, upload_service=uploads)
    window.show()
    window.navigate("accounts")
    settle(app, window.accounts)
    window.navigate("videos")
    page = window.videos
    settle(app, page)
    settle(app, window.uploads)
    if args.phase == "prepare":
        assert not library.all(), "Use a new root"
        capture("empty")
        originals = {}
        for i in range(24):
            name = (
                ("A very long original filename " * 4 + " Café 雪")
                if i == 0
                else f"Portrait {i:02}"
            )
            path = root / (name + ".mp4")
            if i < 3:
                make_video(path, size="270x480", duration=3 + i)
            else:
                shutil.copy2(root / "Portrait 01.mp4", path)
            originals[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            page.start("import", str(path))
            settle(app, page)
            assert page.selected.path == str(path), page.feedback.text()
            if i == 0:
                capture("one-video")
        (root / "originals.json").write_text(json.dumps(originals, indent=2))
        page.show_library()
        settle(app, page)
        capture("many-videos")
        page.search.setText("absent")
        capture("no-results")
        assert page.selected is not None
        page.search.setText("Cafe\u0301")
        assert page.list.count() == 1
        page.list.setCurrentRow(0)
        settle(app, page)
        identity = page.selected.id
        page.tabs.setCurrentIndex(1)
        page.editor.title.setText("Café morning — saved title")
        page.editor.description.setPlainText(VALID.description)
        # Use the existing editor's value controls through its load API, then edit title.
        saved = page.editor.preparation
        page.editor.load(replace(saved, metadata=replace(VALID, privacy="public")))
        page.editor.title.setText("Café morning — saved title")
        page.save_metadata()
        settle(app, page)
        page.complete_review()
        settle(app, page)
        assert page.entries[identity].reviewed
        capture("details")
        preview = page.preview
        window.resize(1180, 780)
        preview.play.click()
        wait_until(lambda: preview.player.position() > 300 and not preview.video.frame.isNull())
        assert preview.audio.isMuted()
        preview.play.click()
        assert preview.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        preview.seek.setFocus()
        preview.seek.setSingleStep(500)
        QTest.keyClick(preview.seek, Qt.Key.Key_Right)
        wait_until(lambda: preview.player.position() >= 700)
        position = preview.player.position()
        preview.toggle_mute()
        assert not preview.audio.isMuted()
        preview.toggle_mute()
        capture("preview-paused")
        page.library_section.set_expanded(True)
        settle(app, page)
        page.search.setText("morning")
        assert page.list.count() == 1
        for index in range(page.sort.count()):
            page.sort.setCurrentIndex(index)
            assert page.selected.id == identity
        page.filter.setCurrentIndex(page.filter.findData("reviewed"))
        assert page.list.count() == 1
        capture("reviewed-filter")
        for tab in (2, 3, 4):
            page.tabs.setCurrentIndex(tab)
            settle(app, page)
            capture(f"step-{tab}")
        page.reload_button.click()
        settle(app, page)
        assert page.selected.id == identity
        window.navigate("dashboard")
        window.navigate("videos")
        settle(app, page)
        assert page.selected.id == identity
        page.continue_editing()
        settle(app, page)
        preview.set_source(str(root / "missing.mp4"))
        preview.toggle()
        assert "File not found" in preview.status.text()
        capture("missing-preview")
        bad = root / "unsupported.mp4"
        bad.write_text("not media")
        preview.set_source(str(bad))
        preview.toggle()
        wait_until(lambda: "could not be played" in preview.status.text())
        capture("unsupported-preview")
        (root / "result.json").write_text(
            json.dumps(
                {
                    "selected_id": identity,
                    "seek_ms": position,
                    "count": len(library.all()),
                    "uploads": len(uploads.store.all()),
                },
                indent=2,
            )
        )
    else:
        result = json.loads((root / "result.json").read_text())
        assert len(library.all()) == result["count"]
        page.search.setText("morning")
        assert page.list.count() == 1
        page.list.setCurrentRow(0)
        settle(app, page)
        assert page.selected.id == result["selected_id"]
        assert page.entries[page.selected.id].reviewed
        assert len(uploads.store.all()) == result["uploads"] == 0
        capture("reopened")
    assert http.initiations == 0
    for path, digest in json.loads((root / "originals.json").read_text()).items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
    (root / f"{args.phase}-sizes.json").write_text(json.dumps(sizes, indent=2))
    window.close()
    for controller in (page, window.accounts, window.uploads, window.schedules, window.background):
        settle(app, controller)
    app.processEvents()
print("Stage 12 native", args.phase, "passed; original sources unchanged; zero upload requests")
