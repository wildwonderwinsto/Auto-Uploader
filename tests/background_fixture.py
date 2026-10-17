"""Isolated native launchd test composition. Never imported by production."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from account_support import CHANNEL, ControlledYouTube
from upload_support import ControlledUploadHTTP, UploadOAuth

from shortspublisher.accounts import AccountRepository, AccountService
from shortspublisher.background import LaunchAgent
from shortspublisher.credentials import KeychainSessions, KeychainStore
from shortspublisher.execution import AutomaticPublisher
from shortspublisher.library import Library
from shortspublisher.scheduling import ScheduleService
from shortspublisher.upload_store import UploadStore
from shortspublisher.uploads import UploadService


class TestAgent(LaunchAgent):
    def __init__(self, root):
        super().__init__(Library(root / "data/library.sqlite3"))
        self.test_root = root
        self.label = "com.shortspublisher.test." + root.name
        self.login_directory = root / "login-agents"

    def command(self):
        return [
            str(self.runtime),
            str(Path(__file__).with_name("background_fixture_worker.py").resolve()),
            "--root",
            str(self.test_root),
        ]


def compose(root):
    library = Library(root / "data/library.sqlite3")
    credentials = KeychainStore("ShortsPublisher.Stage10.NativeVerification")
    sessions = KeychainSessions("ShortsPublisher.Stage10.NativeSessions")
    youtube = ControlledYouTube()
    mode_path = root / "mode.txt"
    mode = mode_path.read_text().strip() if mode_path.exists() else ""
    if mode == "wrong-channel":
        youtube.items = [replace(CHANNEL, id="UC_wrong")]
    accounts = AccountService(AccountRepository(library), credentials, UploadOAuth(), youtube)

    class Transport(ControlledUploadHTTP):
        def request(self, method, *args, **kwargs):
            if method == "POST":
                with (root / "requests.jsonl").open("a") as file:
                    file.write(
                        json.dumps({"operation": "POST", "pid": __import__("os").getpid()}) + "\n"
                    )
            result = super().request(method, *args, **kwargs)
            if method == "GET":
                with (root / "requests.jsonl").open("a") as file:
                    file.write(
                        json.dumps({"operation": "VERIFY", "pid": __import__("os").getpid()}) + "\n"
                    )
            return result

    http = Transport(CHANNEL.id, root / "received.bin")
    http.privacy = "public"
    http.delay = 2
    uploads = UploadService(UploadStore(library), accounts, sessions, http, 262144)

    def clock():
        return datetime.fromisoformat((root / "clock.txt").read_text().strip())

    schedules = ScheduleService(library, clock)
    return AutomaticPublisher(schedules, uploads)


def block_network():
    return patch(
        "requests.Session.request", side_effect=AssertionError("Unexpected real network request")
    )
