"""Advance a recurring opportunity after the Cocoa process has exited."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from background_fixture import TestAgent, block_network, compose

from shortspublisher.recurring import RecurringService

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
root = parser.parse_args().root.resolve()
evidence = json.loads((root / "prepare-result.json").read_text())
try:
    os.kill(evidence["gui_pid"], 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError("Setup GUI still exists")
manager = TestAgent(root)
a = compose(root)
r = RecurringService(a.schedules)
try:
    identity = json.loads((root / "worker-identity.json").read_text())
    assert identity["ppid"] == 1 and not identity["qt_imported"] and identity["keychain_read"]
    assert manager.status()["pid"] == evidence["worker_pid"]
    clock = root / "clock.new"
    clock.write_text(r.state()["plans"][0]["next_slot"])
    clock.replace(root / "clock.txt")
    deadline = time.monotonic() + 45
    with block_network():
        while not any(x.state == "Public Verified" for x in a.uploads.store.all()):
            time.sleep(0.1)
            assert time.monotonic() < deadline, (root / "native-worker.log").read_text()
    attempt = a.uploads.store.all()[0]
    assert Path(attempt.snapshot["path"]).read_bytes() == (root / "received.bin").read_bytes()
    assert r.state()["items"][1]["state"] == "Queued"
    manager.perform("restart")
    deadline = time.monotonic() + 20
    while manager.status()["status"] not in ("Running", "Paused"):
        time.sleep(0.1)
        assert time.monotonic() < deadline
    time.sleep(1)
    requests = [json.loads(x) for x in (root / "requests.jsonl").read_text().splitlines()]
    assert sum(x["operation"] == "POST" for x in requests) == 1
    subprocess.run(
        [sys.executable, "tests/native_recurring.py", "background-reopen", "--root", str(root)],
        check=True,
        timeout=60,
    )
    (root / "process-evidence.json").write_text(
        json.dumps(
            {
                "setup_gui_exited": True,
                "worker_parent": identity["ppid"],
                "headless": True,
                "public_verified": True,
                "unchanged_bytes": True,
                "post_count": 1,
                "restart_no_duplicate": True,
                "second_item_retained": True,
                "service_removed": not manager.status()["loaded"],
            },
            indent=2,
        )
    )
finally:
    if manager.status()["loaded"] or manager.plist.exists():
        manager.perform("remove")
    connection = a.uploads.accounts.repository.connection()
    if connection:
        a.uploads.accounts.credentials.delete(connection.key)
    for attempt in a.uploads.store.all():
        a.uploads.sessions.delete(attempt.id)
print("Stage15 native independent background recurring: PASS", flush=True)
