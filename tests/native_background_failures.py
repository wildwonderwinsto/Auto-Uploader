"""Actual isolated launchd crash/restart and blocked-preflight integration."""

import argparse
import hashlib
import json
import time
import uuid
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Event

from account_support import CHANNEL, CONFIG
from background_fixture import TestAgent, compose
from support import make_video
from test_preparation import VALID

from shortspublisher.accounts import Connection
from shortspublisher.auth import SCOPE, UPLOAD_SCOPES, Tokens
from shortspublisher.background_state import read
from shortspublisher.media import Inspector
from shortspublisher.preparation import PreparationStore

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
base = args.root.resolve()
base.mkdir(parents=True, exist_ok=True)
results = []


def wait_for(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while not predicate():
        time.sleep(0.1)
        assert time.monotonic() < deadline, "Timed out"


for mode in (
    "wrong-channel",
    "missing-credential",
    "missing-scope",
    "changed-source",
    "missed",
    "crash",
    "stale",
):
    root = base / mode
    root.mkdir()
    (root / "mode.txt").write_text(mode)
    (root / "clock.txt").write_text("2030-01-01T00:00:00+00:00")
    a = compose(root)
    a.library.initialize()
    manager = TestAgent(root)
    manager.label = "com.shortspublisher.test." + base.name + "-" + mode
    key = uuid.uuid4().hex
    credentials = a.uploads.accounts.credentials
    sessions = a.uploads.sessions
    repo = a.uploads.accounts.repository
    credentials.put(
        key,
        Tokens(
            "native-stage10-synthetic-access",
            "native-stage10-synthetic-refresh",
            time.time() + 3600,
            frozenset((SCOPE,)) if mode == "missing-scope" else UPLOAD_SCOPES,
        ),
    )
    repo.save_config(CONFIG)
    repo.save(Connection(key, CHANNEL, "2030-01-01T00:00:00+00:00"))
    source = root / "original.mp4"
    make_video(source, size="720x1280", duration=3)
    video, _ = a.library.save(str(source), Inspector().inspect(source, Event()))
    prep = PreparationStore(a.library)
    saved = prep.save(video.id, replace(VALID, privacy="public"), 0)
    prep.complete(video.id, saved.revision, video.inspection_revision)
    proposal = a.schedules.preview(
        video.id, CHANNEL.id, "2030-01-02T12:00:00", "UTC", 0, None, None, Event()
    )
    job = a.schedules.commit(proposal, approve=True)
    a.authorize(job.id, job.revision, Event(), background=True)
    a.control(False)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        manager.perform("enable", consent=True)
        if mode == "missing-credential":
            credentials.delete(key)
        if mode == "missed":
            (root / "clock.txt").write_text("2030-01-02T12:00:01+00:00")
        manager.perform("start")
        wait_for(lambda manager=manager: manager.status()["status"] in ("Running", "Paused"))
        if mode == "stale":
            manager.launchctl("kill", "SIGSTOP", manager.target)
            try:
                with closing(a.library.connect()) as db, db:
                    db.execute("update background_owner set heartbeat='2000-01-01T00:00:00+00:00'")
                assert manager.status()["status"] == "Stale / unresponsive"
                a.dispatch(Event(), lambda _: None)
                assert not a.uploads.store.all()
            finally:
                manager.launchctl("kill", "SIGCONT", manager.target)
        elif mode == "missed":
            wait_for(lambda a=a, job=job: a.schedules.get(job.id).state == "Missed")
        else:
            if mode == "changed-source":
                with source.open("ab") as file:
                    file.write(b"controlled-change")
            clock = root / "clock.new"
            clock.write_text(job.scheduled_utc)
            clock.replace(root / "clock.txt")
            if mode == "crash":
                wait_for(lambda a=a: any(x.acknowledged for x in a.uploads.store.all()))
                old = a.uploads.store.all()[0]
                manager.launchctl("kill", "SIGKILL", manager.target)
                wait_for(lambda manager=manager: not manager.os_status()["pid"])
                assert manager.status()["status"] == "Unavailable"
                manager.perform("start")
                wait_for(
                    lambda manager=manager: manager.status()["status"] in ("Running", "Paused")
                )
                time.sleep(1)
                assert len(a.uploads.store.all()) == 1
                assert a.uploads.store.all()[0].id == old.id
                assert a.uploads.store.all()[0].state == "Interrupted"
            else:
                wait_for(lambda a=a, job=job: a.schedules.get(job.id).state == "Needs Attention")
        requests = (
            [json.loads(line) for line in (root / "requests.jsonl").read_text().splitlines()]
            if (root / "requests.jsonl").exists()
            else []
        )
        posts = sum(x["operation"] == "POST" for x in requests)
        assert posts == (1 if mode == "crash" else 0), (mode, requests)
        if mode != "changed-source":
            assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        results.append(
            {
                "case": mode,
                "initiations": posts,
                "attempts": len(a.uploads.store.all()),
                "owner": read(a.library)["owner"]["kind"],
                "passed": True,
            }
        )
        print("Stage 10 actual launchd " + mode + ": PASS", flush=True)
    finally:
        manager.perform("remove")
        for attempt in a.uploads.store.all():
            sessions.delete(attempt.id)
            assert sessions.get(attempt.id) is None
        credentials.delete(key)
        assert credentials.get(key) is None
        assert not manager.status()["loaded"]
(base / "results.json").write_text(json.dumps(results, indent=2))
