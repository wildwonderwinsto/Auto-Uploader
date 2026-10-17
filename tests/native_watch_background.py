"""Isolated launchd local discovery with GUI absent and all HTTP fail-closed."""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from contextlib import closing
from pathlib import Path
from threading import Event
from unittest.mock import patch

from shortspublisher.background import LaunchAgent
from shortspublisher.library import Library, LibraryService
from shortspublisher.media import Inspector
from shortspublisher.watching import WatchService

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("worker", "prepare", "continue"))
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
data = root / "data"


class DiscoveryAgent(LaunchAgent):
    def command(self):
        return [str(self.runtime), str(Path(__file__).resolve()), "worker", "--root", str(root)]


if args.phase == "worker":
    from shortspublisher.worker import main

    (root / "worker-identity.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "ppid": os.getppid(),
                "qt_imported": any(n.startswith("PySide") for n in sys.modules),
            }
        )
    )
    sys.argv = [sys.argv[0], "--data-dir", str(data)]
    with patch(
        "requests.sessions.Session.request",
        side_effect=AssertionError("No HTTP in discovery verification"),
    ):
        raise SystemExit(main())

from support import make_video, settle  # noqa: E402 — worker must not import Qt

root.mkdir(parents=True, exist_ok=True)
library = Library(data / "library.sqlite3")
library.initialize()
manager = DiscoveryAgent(library)
manager.label = "com.shortspublisher.test." + root.name
manager.login_directory = root / "login-agents"
folder = root / "incoming"
folder.mkdir(exist_ok=True)
service = WatchService(LibraryService(library, Inspector()))


def wait(predicate, seconds=45):
    deadline = time.monotonic() + seconds
    while not predicate():
        time.sleep(0.2)
        assert time.monotonic() < deadline, manager.status()


if args.phase == "prepare":
    from shortspublisher.app import build

    assert not service.folders()
    service.add(service.preview(str(folder), Event()), consent=True, existing=True, background=True)
    try:
        with patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP")):
            app, w = build(data, background_service=manager)
            w.show()
            settle(app, w.watching)
            manager.perform("enable", consent=True)
            manager.perform("start")
            wait(lambda: manager.status()["status"] in ("Running", "Paused"), 20)
            state = manager.status()
            w.watching.request_close()
            settle(app, w.watching)
            w.close()
            app.processEvents()
            (root / "prepare.json").write_text(
                json.dumps({"gui_pid": os.getpid(), "worker_pid": state["pid"]})
            )
    except BaseException:
        manager.perform("remove")
        raise
else:
    try:
        evidence = json.loads((root / "prepare.json").read_text())
        try:
            os.kill(evidence["gui_pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("GUI still running")
        assert manager.status()["pid"] == evidence["worker_pid"]
        identity = json.loads((root / "worker-identity.json").read_text())
        assert identity["ppid"] == 1 and not identity["qt_imported"]
        source = folder / "Without GUI.mp4"
        make_video(source, duration=3)
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        wait(lambda: len(library.all()) == 1)
        manager.perform("restart")
        wait(lambda: manager.status()["pid"] not in (0, evidence["worker_pid"]), 20)
        shutil.copyfile(source, folder / "Duplicate after restart.mp4")
        wait(
            lambda: any(
                r["state"] == "Duplicate content" for r in service.files(service.folders()[0]["id"])
            )
        )
        assert len(library.all()) == 1
        from shortspublisher.app import build

        with patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP")):
            app, w = build(data, background_service=manager)
            w.show()
            w.navigate("videos")
            settle(app, w.watching)
            settle(app, w.videos)
            assert len(w.videos.entries) == 1
            assert len(w.watching.folders) == 1, w.watching.feedback.text()
            assert "needs attention" not in w.watching.feedback.text()
            w.watching.set_expanded(True)
            w.watching.results.set_expanded(True)
            app.processEvents()
            w.grab().save(str(root / "reopened.png"))
            w.watching.request_close()
            settle(app, w.watching)
            w.close()
            app.processEvents()
        with closing(library.connect()) as db:
            for table in (
                "upload_attempts",
                "scheduled_jobs",
                "preparations",
                "account_connection",
            ):
                assert db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original
        (root / "result.json").write_text(
            json.dumps(
                {
                    "headless_import": True,
                    "restart_dedup": True,
                    "source_sha256": original,
                    "uploads": 0,
                    "qt_imported_in_worker": False,
                },
                indent=2,
            )
        )
    finally:
        manager.perform("remove")
        assert not manager.status()["loaded"] and not manager.plist.exists()
print("Stage 14 background " + args.phase + " PASS", flush=True)
