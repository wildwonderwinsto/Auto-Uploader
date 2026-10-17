"""Local byte-I/O benchmark: 30 distinct padded valid MP4s; no upload or live account.

Run with an output JSON path. Temporary sources are removed after hash preservation checks.
"""

import hashlib
import json
import sys
import time
from pathlib import Path
from threading import Event
from unittest.mock import patch

from test_automation import AutomationTests

AutomationTests.setUpClass()
f = AutomationTests()
f.setUp()
try:
    f.approve(mode="review")
    first = f.arrival("Review_00.mp4")
    original = first.read_bytes()
    for i in range(30):
        p = f.root / f"Review_{i:02}.mp4"
        p.write_bytes(original + b"\0" * (8 * 1024 * 1024) + str(i).encode())
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in f.root.iterdir()}
    f.scan()
    assert len(f.state()["decisions"]) == 30 and not f.f.r.state()["items"]
    timings = []
    counts = []
    for _ in range(3):
        with patch.object(f.scanner, "hash_file", wraps=f.scanner.hash_file) as h:
            start = time.perf_counter()
            f.service.reconcile(f.scanner, Event(), background=False)
            timings.append(time.perf_counter() - start)
            counts.append(h.call_count)
    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in f.root.iterdir()}
    result = {
        "files": 30,
        "bytes": sum(p.stat().st_size for p in f.root.iterdir()),
        "waiting_review": len(f.state()["decisions"]),
        "reconcile_seconds": timings,
        "hash_calls": counts,
        "source_hashes_match": True,
        "queue_entries": len(f.f.r.state()["items"]),
        "upload_attempts": len(f.u.store.all()),
    }
    Path(sys.argv[1]).write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
finally:
    f.doCleanups()
    AutomationTests.tearDownClass()
