"""Native Stage16 acceptance: real isolated LaunchAgent and synthetic Keychain only.

Run prepare, then continue in a separate OS process. Requires authorized native/launchd execution.
No production label, data root or credentials are used. Every continuation path cleans test services.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from threading import Event

from account_support import CHANNEL, CONFIG
from background_fixture import TestAgent, block_network, compose
from support import make_video, settle

from shortspublisher.accounts import Connection
from shortspublisher.auth import UPLOAD_SCOPES, Tokens
from shortspublisher.automation import AutomationService
from shortspublisher.cadence import Rule
from shortspublisher.library import LibraryService
from shortspublisher.media import Inspector
from shortspublisher.watching import WatchService

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("prepare", "continue", "reopen"))
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
folder = root / "incoming"
folder.mkdir(exist_ok=True)
if args.phase == "prepare":
    assert not (root / "data/library.sqlite3").exists()
    (root / "clock.txt").write_text("2030-01-01T00:00:00+00:00")
a = compose(root)
a.library.initialize()
r = AutomationService(a.schedules)
manager = TestAgent(root)


def clock(value):
    path = root / "clock.new"
    path.write_text(value)
    path.replace(root / "clock.txt")


def wait_for(predicate, timeout=45):
    deadline = time.monotonic() + timeout
    while not predicate():
        time.sleep(0.1)
        assert time.monotonic() < deadline, "Timed out waiting for isolated worker"


with block_network():
    if args.phase == "prepare":
        key = uuid.uuid4().hex
        a.uploads.accounts.credentials.put(
            key,
            Tokens(
                "synthetic-stage16", "synthetic-stage16-refresh", time.time() + 3600, UPLOAD_SCOPES
            ),
        )
        a.uploads.accounts.repository.save_config(CONFIG)
        a.uploads.accounts.repository.save(Connection(key, CHANNEL, "2030-01-01T00:00:00+00:00"))
        scanner = WatchService(
            LibraryService(a.library, Inspector()), lambda: a.schedules.clock().timestamp()
        )
        folder_id = scanner.add(
            scanner.preview(str(folder), Event()),
            consent=True,
            existing=False,
            background=True,
            quiet=1,
        )
        settings = dict(
            mode="automatic",
            title_template="{filename} #Shorts",
            description="Generated isolated background acceptance fixture",
            tags=["original"],
            made_for_kids=False,
            synthetic=False,
            rule=Rule("UTC", tuple(range(7)), "09:00", "21:00", 2).encode(),
            limit=3,
            background=True,
            pattern="*.mp4",
            max_seconds=180,
            max_bytes=2048 * 1024**2,
        )
        proposal = r.preview(folder_id, CHANNEL.id, "Background automation", settings)
        r.approve(proposal, Event(), consent=True, declarations=True, restrictions=True)
        a.control(False)
        # Actual GUI is opened and exits before a new video is placed by the continuation.
        from shortspublisher.app import build

        app, w = build(
            root / "data",
            account_service=a.uploads.accounts,
            upload_service=a.uploads,
            schedule_service=a.schedules,
            background_service=manager,
        )
        w.show()
        w.navigate("automation")
        settle(app, w.automation)
        w.grab().save(str(root / "prepare-gui.png"))
        manager.perform("enable", consent=True)
        manager.perform("start")
        wait_for(lambda: manager.status()["status"] in ("Running", "Paused"))
        (root / "prepare-result.json").write_text(
            json.dumps({"gui_pid": os.getpid(), "worker_pid": manager.status()["pid"]}, indent=2)
        )
        for c in (w.automation, w.recurring, w.watching, w.automatic, w.background):
            c.stop_for_quit()
        for c in (w.accounts, w.videos, w.uploads, w.schedules):
            settle(app, c)
        w.close()
        app.processEvents()
    elif args.phase == "continue":
        try:
            evidence = json.loads((root / "prepare-result.json").read_text())
            try:
                os.kill(evidence["gui_pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError("Prepare GUI has not exited")
            identity = json.loads((root / "worker-identity.json").read_text())
            assert (
                identity["ppid"] == 1 and not identity["qt_imported"] and identity["keychain_read"]
            )
            target = folder / "Background_new_original.mp4"
            make_video(target, size="720x1280", duration=3)

            # Wait for the real worker to make the first observation; no manual scan.
            def observed():
                from contextlib import closing

                with closing(a.library.connect()) as db:
                    return bool(
                        db.execute(
                            "SELECT 1 FROM watch_files WHERE name=?", (target.name,)
                        ).fetchone()
                    )

            wait_for(observed)
            clock("2030-01-01T00:00:02+00:00")
            wait_for(lambda: len(r.recurring.state()["items"]) == 1)
            clock(r.recurring.state()["plans"][0]["next_slot"])
            wait_for(lambda: any(x.state == "Public Verified" for x in a.uploads.store.all()))
            assert target.read_bytes() == (root / "received.bin").read_bytes()
            manager.perform("restart")
            wait_for(lambda: manager.status()["status"] in ("Running", "Paused"))
            time.sleep(1)
            requests = [json.loads(x) for x in (root / "requests.jsonl").read_text().splitlines()]
            assert sum(x["operation"] == "POST" for x in requests) == 1
            subprocess.run(
                [sys.executable, __file__, "reopen", "--root", str(root)], check=True, timeout=45
            )
            (root / "process-evidence.json").write_text(
                json.dumps(
                    {
                        "gui_exited": True,
                        "worker_parent": identity["ppid"],
                        "headless": True,
                        "enrolled_after_gui_exit": True,
                        "public_verified": True,
                        "post_count": 1,
                        "source_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    },
                    indent=2,
                )
            )
        finally:
            if manager.status()["loaded"] or manager.plist.exists():
                manager.perform("remove")
            a.uploads.accounts.credentials.delete(a.uploads.accounts.repository.connection().key)
            for attempt in a.uploads.store.all():
                a.uploads.sessions.delete(attempt.id)
    else:
        from shortspublisher.app import build

        app, w = build(
            root / "data",
            account_service=a.uploads.accounts,
            upload_service=a.uploads,
            schedule_service=a.schedules,
            background_service=manager,
        )
        w.show()
        w.navigate("automation")
        settle(app, w.automation)
        assert (
            len(a.uploads.store.all()) == 1 and a.uploads.store.all()[0].state == "Public Verified"
        )
        w.grab().save(str(root / "reopened-gui.png"))
        for c in (w.automation, w.recurring, w.watching, w.automatic, w.background):
            c.stop_for_quit()
        for c in (w.accounts, w.videos, w.uploads, w.schedules):
            settle(app, c)
        w.close()
        app.processEvents()
print("Stage16 isolated background " + args.phase + " PASS", flush=True)
