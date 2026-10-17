"""Actual LaunchAgent worker with controlled dependencies and isolated real Keychain."""

import argparse
import json
import logging
import os
import signal
import sys
from pathlib import Path
from threading import Event

from background_fixture import block_network, compose

from shortspublisher.worker import run

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
logging.basicConfig(filename=root / "native-worker.log", level=logging.INFO)
assert not any(n.startswith("PySide6") for n in sys.modules)
a = compose(root)
connection = (
    a.uploads.accounts.repository.connection()
    if hasattr(a.uploads.accounts, "repository")
    else a.uploads.accounts.repo.connection()
)
# Read the actual isolated Keychain entry inside the launchd-managed process.
has_credential = a.uploads.accounts.credentials.get(connection.key) is not None
(root / "worker-identity.json").write_text(
    json.dumps(
        {
            "pid": os.getpid(),
            "uid": os.getuid(),
            "ppid": os.getppid(),
            "qt_imported": False,
            "keychain_read": has_credential,
        }
    )
)
cancel = Event()
signal.signal(signal.SIGTERM, lambda *_: cancel.set())
with block_network():
    raise SystemExit(run(a, cancel))
