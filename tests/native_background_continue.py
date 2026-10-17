"""Drive separate native GUI processes while a genuine launchd worker remains alive."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from background_fixture import TestAgent, compose

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
evidence = json.loads((root / "prepare-result.json").read_text())
try:
    os.kill(evidence["gui_pid"], 0)
except ProcessLookupError:
    pass
else:
    raise AssertionError("Setup GUI still exists")
manager = TestAgent(root)
state = manager.status()
assert state["pid"] == evidence["worker_pid"] and state["status"] in ("Running", "Paused"), state
identity = json.loads((root / "worker-identity.json").read_text())
assert identity["ppid"] == 1 and identity["keychain_read"] and not identity["qt_imported"]
a = compose(root)
job = a.schedules.get(evidence["job"])
clock = root / "clock.new"
clock.write_text(job.scheduled_utc)
clock.replace(root / "clock.txt")
deadline = time.monotonic() + 25
while not any(x.acknowledged for x in a.uploads.store.all()):
    time.sleep(0.1)
    assert time.monotonic() < deadline, (root / "native-worker.log").read_text()
subprocess.run(
    [sys.executable, "tests/native_background_smoke.py", "active", "--root", str(root)],
    check=True,
    timeout=30,
)
deadline = time.monotonic() + 40
while not any(x.state == "Public Verified" for x in a.uploads.store.all()):
    time.sleep(0.1)
    assert time.monotonic() < deadline
assert manager.status()["pid"] == identity["pid"]
# Explicit restart after success cannot produce another session or attempt.
manager.perform("restart")
deadline = time.monotonic() + 20
while manager.status()["status"] not in ("Running", "Paused"):
    time.sleep(0.1)
    assert time.monotonic() < deadline
assert manager.status()["pid"] != identity["pid"]
time.sleep(1)
requests = [json.loads(line) for line in (root / "requests.jsonl").read_text().splitlines()]
assert sum(x["operation"] == "POST" for x in requests) == 1
assert sum(x["operation"] == "VERIFY" for x in requests) >= 1
assert len(a.uploads.store.all()) == 1
subprocess.run(
    [sys.executable, "tests/native_background_smoke.py", "complete", "--root", str(root)],
    check=True,
    timeout=60,
)
assert not manager.status()["loaded"] and not manager.plist.exists()
(root / "process-evidence.json").write_text(
    json.dumps(
        {
            "setup_gui_exited": True,
            "worker_parent": identity["ppid"],
            "keychain_read_under_launchd": True,
            "headless": True,
            "post_count": 1,
            "restart_no_duplicate": True,
            "test_service_removed": True,
        },
        indent=2,
    )
)
print("Stage 10 native independent-process continuation: PASS", flush=True)
