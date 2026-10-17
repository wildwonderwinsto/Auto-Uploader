"""Persistent fixture library with in-memory credential/session providers; no real HTTP."""

import json
import time
from datetime import datetime

from account_support import CHANNEL, CONFIG, ControlledYouTube, MemoryCredentials
from upload_support import ControlledUploadHTTP, MemorySessions, UploadOAuth

from shortspublisher.accounts import AccountRepository, AccountService, Connection
from shortspublisher.auth import UPLOAD_SCOPES, Tokens
from shortspublisher.execution import AutomaticPublisher
from shortspublisher.library import Library
from shortspublisher.scheduling import ScheduleService
from shortspublisher.upload_store import UploadStore
from shortspublisher.uploads import UploadService


def compose(root):
    library = Library(root / "data/library.sqlite3")
    library.initialize()
    repo = AccountRepository(library)
    if not repo.connection():
        repo.save_config(CONFIG)
        repo.save(Connection("synthetic-stage16", CHANNEL, "2030-01-01T00:00:00+00:00"))
    credentials = MemoryCredentials()
    credentials.put(
        repo.connection().key,
        Tokens("synthetic-access", "synthetic-refresh", time.time() + 3600, UPLOAD_SCOPES),
    )
    accounts = AccountService(repo, credentials, UploadOAuth(), ControlledYouTube())

    class Transport(ControlledUploadHTTP):
        def request(self, method, *args, **kwargs):
            if method == "POST":
                self.received.clear()
                with (root / "requests.jsonl").open("a") as file:
                    file.write(json.dumps({"operation": "POST"}) + "\n")
            return super().request(method, *args, **kwargs)

    http = Transport(CHANNEL.id, root / "received.bin")
    http.privacy = "public"
    uploads = UploadService(UploadStore(library), accounts, MemorySessions(), http, 262144)
    schedules = ScheduleService(
        library, lambda: datetime.fromisoformat((root / "clock.txt").read_text())
    )
    return AutomaticPublisher(schedules, uploads)
