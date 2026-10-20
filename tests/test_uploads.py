import hashlib
import shutil
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

import requests
from account_support import CHANNEL, CONFIG, ControlledYouTube, MemoryCredentials
from support import make_video
from test_preparation import VALID
from upload_support import REMOTE_ID, SESSION, ControlledUploadHTTP, MemorySessions, UploadOAuth

from shortspublisher.accounts import AccountRepository, AccountService, Connection
from shortspublisher.auth import SCOPE, UPLOAD_SCOPES, AccountError, Tokens
from shortspublisher.library import Library, LibraryError
from shortspublisher.media import Inspector
from shortspublisher.preparation import PreparationStore
from shortspublisher.upload_http import (
    UploadHTTP,
    UploadReply,
    acknowledged,
    classify,
    retry_delay,
    session_url,
)
from shortspublisher.upload_store import UploadStore
from shortspublisher.uploads import UploadService


class UploadServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixture.cleanup)
        cls.media = Path(cls.fixture.name) / "original.mp4"
        make_video(cls.media, size="720x1280", duration=3)
        assert cls.media.stat().st_size > 256 * 1024

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "video.mp4"
        shutil.copy2(self.media, self.path)
        self.library = Library(self.root / "library.sqlite3")
        self.library.initialize()
        media = Inspector().inspect(self.path, Event())
        self.video, _ = self.library.save(str(self.path), media)
        self.preps = PreparationStore(self.library)
        prep = self.preps.save(self.video.id, replace(VALID, privacy="public"), 0)
        self.preps.complete(self.video.id, prep.revision, self.video.inspection_revision)
        self.repo = AccountRepository(self.library)
        self.repo.save_config(CONFIG)
        self.repo.save(Connection("test-key", CHANNEL, "2026-10-15T00:00:00+00:00"))
        self.credentials = MemoryCredentials()
        self.credentials.put(
            "test-key",
            Tokens("synthetic-access", "synthetic-refresh", time.time() + 3600, UPLOAD_SCOPES),
        )
        self.oauth = UploadOAuth()
        self.youtube = ControlledYouTube()
        self.accounts = AccountService(self.repo, self.credentials, self.oauth, self.youtube)
        self.sessions = MemorySessions()
        self.http = ControlledUploadHTTP(CHANNEL.id)
        self.store = UploadStore(self.library)
        self.service = UploadService(
            self.store, self.accounts, self.sessions, self.http, 256 * 1024
        )

    def run_action(self, action, arg=None, cancel=None, progress=lambda _: None):
        return self.service.execute(action, arg, cancel or Event(), progress)

    def prepare(self):
        result = self.run_action("prepare", (self.video.id, 1, CHANNEL.id))
        self.assertNotIn("error", result, result.get("error"))
        return result["confirmation"]

    def confirm(self):
        old = self.prepare()
        result = self.run_action("confirm", old.id)
        self.assertNotIn("error", result, result.get("error"))
        return self.store.get(old.id)

    def test_restart_keeps_original_chunk_size(self):
        old = self.prepare()
        cancel = Event()
        self.http.after_chunk = cancel.set
        self.run_action("confirm", old.id, cancel)
        self.http.after_chunk = None
        self.service.chunk_size = 2 * 1024 * 1024
        self.run_action("resume", old.id)
        self.assertEqual(self.http.offsets, list(range(0, self.path.stat().st_size, 256 * 1024)))
        self.assertEqual(self.store.get(old.id).state, "Uploaded Private")

    def test_failed_remote_reverification_labels_cached_and_prevents_transfer(self):
        old = self.confirm()
        self.http.verify_reply = UploadReply(503)
        result = self.run_action("verify", old.id)
        self.assertIn("error", result)
        saved = self.store.get(old.id)
        self.assertEqual(saved.state, "Needs Reconciliation")
        self.assertIn("cached", saved.error)
        self.assertEqual(saved.remote_id, REMOTE_ID)
        self.assertIn("error", self.run_action("resume", old.id))
        self.assertEqual(self.http.initiations, 1)

    def test_initial_401_refreshes_once_before_retry(self):
        original = self.http.initiate
        calls = []

        def initiate(tokens, snapshot):
            calls.append(tokens.access)
            return UploadReply(401) if len(calls) == 1 else original(tokens, snapshot)

        with patch.object(self.http, "initiate", side_effect=initiate):
            self.assertEqual(self.confirm().state, "Uploaded Private")
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.oauth.refresh_count, 1)

    def test_private_upload_exact_snapshot_and_source_preserved(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        old = self.confirm()
        self.assertEqual(old.state, "Uploaded Private")
        self.assertEqual(old.remote_id, REMOTE_ID)
        self.assertEqual(old.privacy, "private")
        self.assertEqual(
            self.http.payload["status"],
            {
                "privacyStatus": "private",
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": False,
            },
        )
        self.assertEqual(self.http.payload["snippet"]["categoryId"], "22")
        self.assertEqual(self.http.payload["snippet"]["title"], VALID.title)
        self.assertEqual(hashlib.sha256(self.http.received).hexdigest(), before)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(self.http.initiations, 1)
        self.assertEqual(self.http.offsets, list(range(0, self.path.stat().st_size, 256 * 1024)))

    def test_prepare_is_not_upload_consent(self):
        old = self.prepare()
        self.assertEqual(old.state, "Awaiting Confirmation")
        self.assertEqual(self.http.initiations, 0)
        self.assertFalse(self.sessions.values)
        result = self.run_action("resume", old.id)
        self.assertIn("confirmation", result["error"])
        self.assertEqual(self.http.initiations, 0)

    def test_missing_upload_scope(self):
        self.credentials.values["test-key"].scopes = frozenset((SCOPE,))
        result = self.run_action("prepare", (self.video.id, 1, CHANNEL.id))
        self.assertIn("permission", result["error"])
        self.assertEqual(self.http.initiations, 0)

    def test_missing_credentials(self):
        self.credentials.values.clear()
        self.assertIn("error", self.run_action("prepare", (self.video.id, 1, CHANNEL.id)))

    def test_wrong_channel_and_ambiguous_channels(self):
        for channels in (
            [],
            [replace(CHANNEL, id="wrong")],
            [CHANNEL, replace(CHANNEL, id="other")],
        ):
            self.youtube.items = channels
            self.assertIn("error", self.run_action("prepare", (self.video.id, 1, CHANNEL.id)))
        self.assertEqual(self.http.initiations, 0)

    def test_stale_metadata_and_incomplete_review(self):
        self.preps.save(self.video.id, replace(VALID, title="changed"), 1)
        result = self.run_action("prepare", (self.video.id, 2, CHANNEL.id))
        self.assertIn("review", result["error"])
        self.assertEqual(self.http.initiations, 0)

    def test_source_changes_after_confirmation(self):
        old = self.prepare()
        self.path.write_bytes(self.path.read_bytes() + b"changed")
        result = self.run_action("confirm", old.id)
        self.assertIn("SHA-256", result["error"])
        self.assertEqual(self.http.initiations, 0)

    def test_metadata_changes_after_confirmation(self):
        old = self.prepare()
        self.preps.save(self.video.id, replace(VALID, title="changed"), 1)
        self.assertIn("error", self.run_action("confirm", old.id))
        self.assertEqual(self.http.initiations, 0)

    def test_source_changes_during_transfer_stops_chunks(self):
        old = self.prepare()
        self.http.after_chunk = lambda: self.path.write_bytes(self.path.read_bytes() + b"x")
        result = self.run_action("confirm", old.id)
        self.assertIn("error", result)
        self.assertEqual(len(self.http.offsets), 1)
        self.assertEqual(self.store.get(old.id).state, "Needs Reconciliation")

    def test_duplicate_same_video_and_fingerprint_blocked(self):
        self.confirm()
        self.assertIn("blocks", self.run_action("prepare", (self.video.id, 1, CHANNEL.id))["error"])

    def test_cancel_confirmation_has_no_side_effect(self):
        old = self.prepare()
        self.run_action("cancel_confirmation", old.id)
        self.assertEqual(self.store.get(old.id).state, "Cancelled")
        self.assertIn("error", self.run_action("confirm", old.id))
        self.assertEqual(self.http.initiations, 0)

    def test_stop_resume_uses_one_session_and_acknowledged_offset(self):
        old = self.prepare()
        cancel = Event()
        self.http.after_chunk = cancel.set
        self.run_action("confirm", old.id, cancel)
        stopped = self.store.get(old.id)
        self.assertEqual(stopped.state, "Paused")
        self.assertEqual(stopped.acknowledged, 256 * 1024)
        self.http.after_chunk = None
        self.assertNotIn("error", self.run_action("resume", old.id))
        self.assertEqual(self.http.initiations, 1)
        self.assertEqual(self.http.offsets[1], 256 * 1024)
        self.assertEqual(self.store.get(old.id).state, "Uploaded Private")

    def test_lost_final_response_reconciled_without_reupload(self):
        self.http.lost_final = True
        old = self.confirm()
        self.assertEqual(old.remote_id, REMOTE_ID)
        self.assertEqual(self.http.initiations, 1)
        self.assertEqual(len(self.http.received), self.path.stat().st_size)

    def test_expired_session_needs_reconciliation_and_no_new_session(self):
        old = self.prepare()
        cancel = Event()
        self.http.after_chunk = cancel.set
        self.run_action("confirm", old.id, cancel)
        self.http.after_chunk = None
        self.http.query_replies = [UploadReply(404)]
        self.run_action("resume", old.id)
        self.assertEqual(self.store.get(old.id).state, "Needs Reconciliation")
        self.assertEqual(self.http.initiations, 1)
        self.assertIn("error", self.run_action("prepare", (self.video.id, 1, CHANNEL.id)))

    def test_lost_initial_response_not_retried(self):
        self.http.initiation_reply = UploadReply(0)
        old = self.prepare()
        self.run_action("confirm", old.id)
        self.assertEqual(self.store.get(old.id).state, "Needs Reconciliation")
        self.run_action("resume", old.id)
        self.assertEqual(self.http.initiations, 1)
        self.assertFalse(self.http.received)

    def test_secure_session_write_failure_sends_no_media(self):
        old = self.prepare()
        self.sessions.fail = True
        result = self.run_action("confirm", old.id)
        self.assertIn("error", result)
        self.assertFalse(self.http.received)
        self.assertNotIn("synthetic capability", result["error"])
        self.assertEqual(self.store.get(old.id).state, "Needs Reconciliation")

    def test_wrong_session_host_not_followed(self):
        self.http.initiation_reply = UploadReply(
            200, headers={"location": "https://evil.invalid/session"}
        )
        old = self.prepare()
        self.run_action("confirm", old.id)
        self.assertFalse(self.http.received)
        self.assertFalse(self.sessions.values)

    def test_retryable_status_resumes_after_query(self):
        for status in (429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.http.chunk_replies = [UploadReply(status, headers={"retry-after": "0"})]
                old = self.confirm()
                self.assertEqual(old.state, "Uploaded Private")
                self.run_action("release", old.id)
                self.http.received.clear()

    def test_repeated_transient_failures_are_bounded(self):
        self.http.chunk_replies = [UploadReply(503)] * 5
        old = self.prepare()
        self.run_action("confirm", old.id)
        self.assertEqual(len(self.http.offsets), 4)
        self.assertEqual(self.store.get(old.id).state, "Interrupted")
        self.assertEqual(self.http.initiations, 1)

    def test_401_refreshes_once_then_queries(self):
        self.http.chunk_replies = [UploadReply(401)]
        old = self.confirm()
        self.assertEqual(old.state, "Uploaded Private")
        self.assertEqual(self.oauth.refresh_count, 1)

    def test_quota_and_upload_limit_are_not_retried(self):
        for reason in ("quotaExceeded", "uploadLimitExceeded"):
            self.http.chunk_replies = [
                UploadReply(403, {"error": {"errors": [{"reason": reason}]}})
            ]
            old = self.prepare()
            result = self.run_action("confirm", old.id)
            self.assertIn("error", result)
            self.assertEqual(self.store.get(old.id).state, "Interrupted")
            self.run_action("release", old.id)
        self.assertEqual(len(self.http.offsets), 2)

    def test_permanent_error_saved(self):
        self.http.chunk_replies = [UploadReply(400)]
        old = self.prepare()
        self.run_action("confirm", old.id)
        self.assertEqual(self.store.get(old.id).state, "Failed")

    def test_nonprivate_result_critical_no_remote_mutation(self):
        self.http.privacy = "public"
        old = self.confirm()
        self.assertEqual(old.state, "Needs Reconciliation")
        self.assertIn("CRITICAL", old.error)
        self.assertEqual(old.remote_id, REMOTE_ID)
        self.assertEqual(old.privacy, "public")

    def test_unexpected_returned_channel_is_critical(self):
        self.http.channel_id = "different"
        old = self.confirm()
        self.assertEqual(old.state, "Needs Reconciliation")
        self.assertIn("destination", old.error)

    def test_processing_not_publication(self):
        self.http.processing = "processing"
        old = self.confirm()
        self.assertEqual(old.state, "Processing")
        self.http.processing = "processed"
        self.run_action("verify", old.id)
        self.assertEqual(self.store.get(old.id).state, "Uploaded Private")

    def test_missing_privacy_verification_unavailable_preserves_id(self):
        self.http.privacy = ""
        self.http.verify_reply = UploadReply(503)
        old = self.confirm()
        self.assertEqual(old.state, "Transfer Complete")
        self.assertEqual(old.remote_id, REMOTE_ID)
        self.assertIn("not yet verified", old.error)
        self.http.privacy = "private"
        self.http.verify_reply = None
        self.run_action("verify", old.id)
        self.assertEqual(self.store.get(old.id).state, "Uploaded Private")

    def test_acknowledged_progress_only(self):
        updates = []
        old = self.prepare()
        self.run_action("confirm", old.id, progress=lambda a: updates.append(a.acknowledged))
        self.assertEqual(updates, sorted(updates))
        self.assertEqual(updates[0], 0)
        self.assertEqual(updates[-1], self.path.stat().st_size)

    def test_interrupted_state_on_restart_never_sends(self):
        old = self.prepare()
        self.store.update(old.id, "Preparing")
        self.run_action("history")
        self.assertEqual(self.store.get(old.id).state, "Interrupted")
        self.assertEqual(self.http.initiations, 0)

    def test_release_requires_deliberate_action_and_retains_history(self):
        old = self.confirm()
        self.run_action("release", old.id)
        fresh = self.prepare()
        self.assertNotEqual(fresh.id, old.id)
        self.assertEqual(len(self.store.all()), 2)
        self.assertEqual(self.http.initiations, 1)

    def test_no_tokens_or_session_capabilities_in_sqlite(self):
        old = self.confirm()
        self.assertEqual(self.sessions.get(old.id), SESSION)
        raw = self.library.path.read_bytes()
        for secret in (SESSION, "synthetic-access", "synthetic-refresh"):
            self.assertNotIn(secret.encode(), raw)
        self.assertNotIn(
            "synthetic-capability", repr(UploadReply(200, headers={"location": SESSION}))
        )

    def test_database_failure_before_post_has_no_remote_side_effect(self):
        old = self.prepare()
        with patch.object(self.store, "update", side_effect=sqlite3.OperationalError("injected")):
            self.run_action("confirm", old.id)
        self.assertEqual(self.http.initiations, 0)

    def test_write_failure_after_final_response_is_recoverable(self):
        old = self.prepare()
        original = self.store.update

        def fail_complete(identity, state, **kwargs):
            if state == "Transfer Complete":
                raise sqlite3.OperationalError("injected")
            return original(identity, state, **kwargs)

        with patch.object(self.store, "update", side_effect=fail_complete):
            self.run_action("confirm", old.id)
        self.assertEqual(self.store.get(old.id).state, "Needs Reconciliation")
        self.run_action("resume", old.id)
        self.assertEqual(self.store.get(old.id).state, "Uploaded Private")
        self.assertEqual(self.http.initiations, 1)

    def test_concurrent_service_rejected(self):
        other = UploadService(self.store, self.accounts, self.sessions, self.http)
        with self.service.lock:
            result = other.execute("prepare", (self.video.id, 1, CHANNEL.id), Event())
        self.assertIn("Another", result["error"])
        self.assertEqual(self.http.initiations, 0)

    def test_oauth_upgrade_preserves_existing_on_denial_or_wrong_identity(self):
        original = self.credentials.get("test-key")
        self.oauth.error = AccountError("Denied", "cancelled")
        view = self.accounts.execute("authorize_upload", None, Event())
        self.assertFalse(view.upload_allowed)
        self.assertIs(self.credentials.get("test-key"), original)
        self.oauth.error = None
        self.youtube.items = [replace(CHANNEL, id="wrong")]
        self.accounts.execute("authorize_upload", None, Event())
        self.assertIs(self.credentials.get("test-key"), original)

    def test_oauth_upload_scope_confirmed_and_persisted(self):
        self.credentials.values["test-key"].scopes = frozenset((SCOPE,))
        view = self.accounts.execute("authorize_upload", None, Event())
        self.assertTrue(view.upload_allowed)
        self.assertEqual(self.credentials.get("test-key").scopes, UPLOAD_SCOPES)

    def test_missing_new_refresh_token_does_not_destroy_readonly(self):
        old = self.credentials.get("test-key")
        old.scopes = frozenset((SCOPE,))
        with patch.object(
            self.oauth,
            "authorize_upload",
            return_value=Tokens("new-access", "", time.time() + 3600, UPLOAD_SCOPES),
        ):
            view = self.accounts.execute("authorize_upload", None, Event())
        self.assertFalse(view.upload_allowed)
        self.assertIs(self.credentials.get("test-key"), old)

    def test_missing_new_refresh_token_can_reuse_verified_capable_grant(self):
        with patch.object(
            self.oauth,
            "authorize_upload",
            return_value=Tokens("new-access", "", time.time() + 3600, UPLOAD_SCOPES),
        ):
            view = self.accounts.execute("authorize_upload", None, Event())
        self.assertTrue(view.upload_allowed)
        self.assertTrue(self.credentials.get("test-key").refresh)

    def test_migration_from_genuine_v3_preserves_every_existing_table(self):
        fixture = Path(__file__).parent / "fixtures" / "library-v3.sqlite3"
        with sqlite3.connect(fixture) as source, sqlite3.connect(self.library.path) as target:
            source.backup(target)
        tables = ("videos", "preparations", "account_config", "account_connection")
        with closing(self.library.connect()) as db:
            before = {
                table: [tuple(row) for row in db.execute("SELECT * FROM " + table)]
                for table in tables
            }
        self.library.initialize()
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 12)
            for table in tables:
                self.assertEqual(
                    before[table], [tuple(row) for row in db.execute("SELECT * FROM " + table)]
                )

    def test_migration_failure_rollback(self):
        with closing(self.library.connect()) as db, db:
            db.execute("DROP TABLE upload_attempts")
            db.execute("PRAGMA user_version=3")
            db.execute("CREATE TABLE upload_attempts (conflict TEXT)")
        with self.assertRaises(sqlite3.OperationalError):
            self.library.initialize()
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)

    def test_future_schema_rejected(self):
        with closing(self.library.connect()) as db, db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(LibraryError):
            self.library.initialize()


class UploadProtocolTests(unittest.TestCase):
    def test_range_parsing(self):
        self.assertEqual(acknowledged(UploadReply(308), 100), 0)
        self.assertEqual(acknowledged(UploadReply(308, headers={"range": "bytes=0-49"}), 100), 50)
        for value in ("bytes=2-5", "bytes=0-999", "garbage"):
            with self.assertRaises(AccountError):
                acknowledged(UploadReply(308, headers={"range": value}), 100)

    def test_secure_session_url_validation(self):
        self.assertEqual(session_url(SESSION), SESSION)
        for url in (
            "http://www.googleapis.com/upload/youtube/v3/videos",
            "https://evil.invalid/x",
            "https://www.googleapis.com:444/upload/youtube/v3/videos",
            "https://user@www.googleapis.com/upload/youtube/v3/videos",
        ):
            with self.assertRaises(AccountError):
                session_url(url)

    def test_retry_after_respected_and_long_cooldown_pauses(self):
        self.assertEqual(retry_delay(UploadReply(429, headers={"retry-after": "4"}), 0), 4)
        with self.assertRaises(AccountError):
            retry_delay(UploadReply(429, headers={"retry-after": "120"}), 0)

    def test_http_sends_token_header_and_no_redirect(self):
        reply = requests.Response()
        reply.status_code = 308
        reply._content = b""
        reply._content_consumed = True
        reply.headers["Range"] = "bytes=0-255"
        with patch("shortspublisher.upload_http.requests.request", return_value=reply) as request:
            result = UploadHTTP().chunk(Tokens("sensitive", "r", 0), SESSION, b"a" * 256, 0, 512)
        self.assertEqual(result.status, 308)
        self.assertEqual(request.call_args.kwargs["headers"]["Content-Range"], "bytes 0-255/512")
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer sensitive")
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        self.assertEqual(request.call_args.kwargs["timeout"], (5, 30))

    def test_transport_exception_is_sanitized(self):
        with patch(
            "shortspublisher.upload_http.requests.request",
            side_effect=requests.ConnectionError(SESSION),
        ):
            reply = UploadHTTP().query(Tokens("a", "r", 0), SESSION, 100)
        self.assertEqual(reply.status, 0)
        self.assertNotIn("synthetic-capability", str(classify(reply)))

    def test_nonprivate_payload_cannot_reach_transport(self):
        for status in (
            {"privacyStatus": "public"},
            {"privacyStatus": "unlisted"},
            {"privacyStatus": "private", "publishAt": "tomorrow"},
            {},
        ):
            with (
                patch("shortspublisher.upload_http.requests.request") as request,
                self.assertRaises(AccountError),
            ):
                UploadHTTP().initiate(
                    Tokens("a", "r", 0), {"payload": {"status": status}, "size": 1}
                )
            request.assert_not_called()
