"""Launch the exact production entry point against a new empty, paused library."""

import argparse
import json
import time
from pathlib import Path

from shortspublisher.background import LaunchAgent
from shortspublisher.library import Library

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
assert not (root / "library.sqlite3").exists()
library = Library(root / "library.sqlite3")
library.initialize()
manager = LaunchAgent(library)
manager.label = "com.shortspublisher.test." + root.name
manager.login_directory = root / "login-agents"
try:
    manager.perform("enable", consent=True)
    manager.perform("start")
    deadline = time.monotonic() + 20
    while manager.status()["status"] not in ("Running", "Paused"):
        time.sleep(0.1)
        assert time.monotonic() < deadline, manager.status()
    state = manager.status()
    assert state["pid"] and state["owner"]["kind"] == "background"
    time.sleep(2.2)
    assert manager.status()["status"] == "Paused"
    manager.perform("stop")
    assert not manager.status()["pid"]
    log = (root / "background/logs/worker.log").read_text()
    assert "initialized" in log and "shutdown complete" in log
    (root / "entry-result.json").write_text(
        json.dumps(
            {
                "entry": manager.command()[1],
                "initialized_pid": state["pid"],
                "paused": True,
                "graceful_shutdown": True,
                "no_account_configured": True,
            },
            indent=2,
        )
    )
finally:
    manager.perform("remove")
    assert not manager.status()["loaded"]
print("Stage 10 production headless entry under actual launchd: PASS")
