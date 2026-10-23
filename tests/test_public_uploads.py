"""Public intent, independent verification and legacy preservation; no real Google calls."""

import copy
import shutil
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import patch

import test_uploads as fixtures
from account_support import CHANNEL
from upload_support import REMOTE_ID

from shortspublisher.auth import AccountError, Tokens
from shortspublisher.library import SCHEMA_VERSION, Library
from shortspublisher.upload_http import UploadHTTP, UploadReply, classify
from shortspublisher.upload_store import UploadStore


class PublicUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UploadServiceTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.UploadServiceTests.doClassCleanups()

    def setUp(self):
        self.f = fixtures.UploadServiceTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.http.privacy = "public"

    def prepare(self):
        result = self.f.run_action("prepare_public", (self.f.video.id, 1, CHANNEL.id))
        self.assertNotIn("error", result, result.get("error"))
        return result["confirmation"]

    def publish(self):
        old = self.prepare()
        result = self.f.run_action("confirm_public", old.id)
        self.assertNotIn("error", result, result.get("error"))
        return self.f.store.get(old.id)

    def test_explicit_public_payload_and_independent_verification(self):
        old = self.publish()
        self.assertEqual(old.requested_privacy, "public")
        self.assertEqual(self.f.http.payload["status"]["privacyStatus"], "public")
        self.assertNotIn("publishAt", self.f.http.payload["status"])
        self.assertEqual(old.state, "Public Verified")
        self.assertEqual(old.verification_status, "verified")
        self.assertTrue(old.verified_at)
        self.assertEqual(self.f.http.verify_calls, 1)
        self.assertEqual(bytes(self.f.http.received), self.f.path.read_bytes())

    def test_private_action_stays_private_despite_reviewed_public(self):
        self.f.http.privacy = "private"
        old = self.f.confirm()
        self.assertEqual(old.requested_privacy, "private")
        self.assertEqual(old.state, "Uploaded Private")

    def test_public_requires_separate_confirmation_action(self):
        old = self.prepare()
        result = self.f.run_action("confirm", old.id)
        self.assertIn("Public confirmation", result["error"])
        self.assertEqual(self.f.http.initiations, 0)
        self.assertEqual(self.f.store.get(old.id).state, "Awaiting Confirmation")

    def test_private_cannot_be_publicly_confirmed(self):
        old = self.f.prepare()
        self.assertIn("error", self.f.run_action("confirm_public", old.id))
        self.assertEqual(self.f.http.initiations, 0)

    def test_cancel_public_confirmation_no_remote_actions(self):
        old = self.prepare()
        self.f.run_action("cancel_confirmation", old.id)
        self.assertEqual(self.f.store.get(old.id).state, "Cancelled")
        self.assertEqual(self.f.http.initiations, 0)
        self.assertEqual(self.f.http.verify_calls, 0)

    def test_private_or_unlisted_review_cannot_prepare_public(self):
        for privacy in ("private", "unlisted"):
            prep = self.f.preps.get(self.f.video.id)
            changed = self.f.preps.save(
                self.f.video.id, replace(prep.metadata, privacy=privacy), prep.revision
            )
            self.f.preps.complete(
                self.f.video.id, changed.revision, self.f.video.inspection_revision
            )
            result = self.f.run_action(
                "prepare_public", (self.f.video.id, changed.revision, CHANNEL.id)
            )
            self.assertIn("planned privacy", result["error"])
        self.assertEqual(self.f.http.initiations, 0)

    def test_metadata_change_after_public_confirmation_stops_upload(self):
        old = self.prepare()
        prep = self.f.preps.get(self.f.video.id)
        self.f.preps.save(self.f.video.id, replace(prep.metadata, privacy="private"), prep.revision)
        self.assertIn("error", self.f.run_action("confirm_public", old.id))
        self.assertEqual(self.f.http.initiations, 0)

    def test_missing_scope_public_preflight(self):
        self.f.credentials.values.clear()
        result = self.f.run_action("prepare_public", (self.f.video.id, 1, CHANNEL.id))
        self.assertIn("error", result)
        self.assertEqual(self.f.http.initiations, 0)

    def test_wrong_channel_public_preflight(self):
        self.f.youtube.items = [replace(CHANNEL, id="wrong")]
        self.assertIn(
            "error", self.f.run_action("prepare_public", (self.f.video.id, 1, CHANNEL.id))
        )
        self.assertEqual(self.f.http.initiations, 0)

    def test_public_source_change_rejected(self):
        old = self.prepare()
        self.f.path.write_bytes(b"changed")
        self.assertIn("error", self.f.run_action("confirm_public", old.id))
        self.assertEqual(self.f.http.initiations, 0)

    def test_public_resume_preserves_intent_session_and_offsets(self):
        old = self.prepare()
        cancel = Event()
        self.f.http.after_chunk = cancel.set
        self.f.run_action("confirm_public", old.id, cancel)
        paused = self.f.store.get(old.id)
        self.assertEqual(paused.state, "Paused")
        self.assertGreater(paused.acknowledged, 0)
        self.f.http.after_chunk = None
        self.f.run_action("resume", old.id)
        self.assertEqual(self.f.store.get(old.id).state, "Public Verified")
        self.assertEqual(self.f.http.initiations, 1)
        self.assertEqual(self.f.http.payload["status"]["privacyStatus"], "public")

    def test_public_lost_final_response_reuses_session(self):
        self.f.http.lost_final = True
        self.assertEqual(self.publish().state, "Public Verified")
        self.assertEqual(self.f.http.initiations, 1)
        self.assertEqual(len(set(self.f.http.offsets)), len(self.f.http.offsets))

    def test_public_expired_session_blocks_new_attempt(self):
        self.f.http.query_replies = [UploadReply(404)]
        old = self.prepare()
        self.f.run_action("confirm_public", old.id)
        self.assertEqual(self.f.store.get(old.id).state, "Needs Reconciliation")
        self.assertIn(
            "error", self.f.run_action("prepare_public", (self.f.video.id, 1, CHANNEL.id))
        )
        self.assertEqual(self.f.http.initiations, 1)

    def test_public_insert_private_discrepancy(self):
        self.f.http.privacy = "private"
        old = self.publish()
        self.assertEqual(old.state, "Needs Reconciliation")
        self.assertEqual(old.verification_status, "discrepancy")
        self.assertEqual(old.privacy, "private")
        self.assertIn("requested Public", old.error)
        self.assertEqual(old.remote_id, REMOTE_ID)

    def test_public_insert_success_list_private_discrepancy(self):
        result = self.f.http.result()
        result["status"]["privacyStatus"] = "private"
        self.f.http.verify_reply = UploadReply(200, {"items": [result]})
        old = self.publish()
        self.assertEqual(old.state, "Needs Reconciliation")
        self.assertEqual(old.privacy, "private")
        self.assertEqual(self.f.http.verify_calls, 1)

    def test_public_list_wrong_channel(self):
        result = self.f.http.result()
        result["snippet"]["channelId"] = "wrong"
        self.f.http.verify_reply = UploadReply(200, {"items": [result]})
        old = self.publish()
        self.assertEqual(old.verification_status, "discrepancy")
        self.assertIn("channel", old.error)

    def test_public_verification_unavailable_retains_id_and_blocks_resume(self):
        self.f.http.verify_reply = UploadReply(503)
        old = self.publish()
        self.assertEqual(old.state, "Transfer Complete")
        self.assertEqual(old.remote_id, REMOTE_ID)
        self.assertEqual(old.verification_status, "unverified")
        self.assertFalse(old.verified_at)
        self.assertIn("error", self.f.run_action("resume", old.id))
        self.f.http.verify_reply = None
        self.f.run_action("verify", old.id)
        saved = self.f.store.get(old.id)
        self.assertEqual(saved.state, "Public Verified")
        self.assertEqual(saved.completed_at, old.completed_at)
        self.assertEqual(self.f.http.initiations, 1)

    def test_public_wrong_or_missing_list_id_never_verified(self):
        self.f.http.verify_reply = UploadReply(200, {"items": [{"id": "wrong"}]})
        self.assertEqual(self.publish().verification_status, "unverified")

    def test_missing_transfer_id_never_claims_success(self):
        self.f.http.query_replies = [UploadReply(201, {"status": {"privacyStatus": "public"}})]
        old = self.prepare()
        self.f.run_action("confirm_public", old.id)
        self.assertEqual(self.f.store.get(old.id).state, "Needs Reconciliation")
        self.assertFalse(self.f.store.get(old.id).remote_id)

    def test_public_processing_pending(self):
        self.f.http.processing = "uploaded"
        old = self.publish()
        self.assertEqual(old.state, "Processing")
        self.assertEqual(old.verification_status, "verified")
        self.assertEqual(old.privacy, "public")

    def test_public_processing_rejected_reports_reason(self):
        body = self.f.http.result()
        body["status"].update(uploadStatus="rejected", rejectionReason="copyright")
        self.f.http.verify_reply = UploadReply(200, {"items": [body]})
        old = self.publish()
        self.assertEqual(old.state, "Failed")
        self.assertIn("copyright", old.error)
        self.assertEqual(self.f.http.initiations, 1)

    def test_public_rate_limit_and_refresh_recovery(self):
        self.f.http.chunk_replies = [
            UploadReply(401),
            UploadReply(429, headers={"retry-after": "0"}),
        ]
        self.assertEqual(self.publish().state, "Public Verified")
        self.assertEqual(self.f.oauth.refresh_count, 1)
        self.assertEqual(self.f.http.initiations, 1)

    def test_public_restart_is_history_only_and_duplicate_blocked(self):
        old = self.publish()
        self.f.run_action("history")
        saved = UploadStore(self.f.library).get(old.id)
        self.assertEqual(saved.requested_privacy, "public")
        self.assertEqual(saved.verified_at, old.verified_at)
        self.assertEqual(self.f.http.initiations, 1)
        self.assertIn(
            "error", self.f.run_action("prepare_public", (self.f.video.id, 1, CHANNEL.id))
        )

    def test_http_rejects_missing_mismatched_and_unsupported_intent(self):
        snapshot = self.prepare().snapshot
        for intent, payload in (
            (None, "public"),
            ("private", "public"),
            ("public", "private"),
            ("unlisted", "unlisted"),
            ("public", None),
        ):
            data = copy.deepcopy(snapshot)
            data["requested_privacy"] = intent
            data["payload"]["status"]["privacyStatus"] = payload
            with patch.object(UploadHTTP, "request") as request, self.assertRaises(AccountError):
                UploadHTTP().initiate(Tokens("a", "r", 0), data)
            request.assert_not_called()

    def test_http_public_publish_at_is_rejected(self):
        snapshot = self.prepare().snapshot
        snapshot["payload"]["status"]["publishAt"] = "tomorrow"
        with patch.object(UploadHTTP, "request") as request, self.assertRaises(AccountError):
            UploadHTTP().initiate(Tokens("a", "r", 0), snapshot)
        request.assert_not_called()

    def test_snapshot_intent_cannot_be_updated_by_store(self):
        old = self.prepare()
        with self.assertRaises(ValueError):
            self.f.store.update(old.id, old.state, snapshot={})

    def test_v4_migration_preserves_all_old_values(self):
        target = self.f.root / "v4.sqlite3"
        shutil.copy2(Path(__file__).parent / "fixtures/library-v4.sqlite3", target)
        with sqlite3.connect(target) as db:
            tables = (
                "videos",
                "preparations",
                "account_config",
                "account_connection",
                "upload_attempts",
            )
            before = {t: db.execute("SELECT * FROM " + t).fetchall() for t in tables}
        Library(target).initialize()
        with sqlite3.connect(target) as db:
            for table, rows in before.items():
                after = db.execute("SELECT * FROM " + table).fetchall()
                self.assertEqual([r[: len(rows[0])] for r in after] if rows else after, rows)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        for old in UploadStore(Library(target)).all():
            self.assertEqual(old.requested_privacy, "private")
            self.assertEqual(old.verification_status, "historical")

    def test_v4_migration_rollback(self):
        target = self.f.root / "v4.sqlite3"
        shutil.copy2(Path(__file__).parent / "fixtures/library-v4.sqlite3", target)
        library = Library(target)
        original = library.connect

        def connect():
            db = original()
            db.set_authorizer(
                lambda action, arg1, arg2, database, source: (
                    sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_DROP_INDEX
                    else sqlite3.SQLITE_OK
                )
            )
            return db

        with (
            patch.object(library, "connect", side_effect=connect),
            self.assertRaises(sqlite3.DatabaseError),
        ):
            library.initialize()
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertNotIn(
                "verified_at", [r[1] for r in db.execute("PRAGMA table_info(upload_attempts)")]
            )

    def test_platform_restrictions_are_specific_sanitized_and_not_retried(self):
        for reason in ("forbiddenPrivacySetting", "uploadForbidden", "insufficientPermissions"):
            reply = UploadReply(
                403,
                body={
                    "error": {"message": "secret-transport-body", "errors": [{"reason": reason}]}
                },
            )
            problem = classify(reply)
            self.assertEqual(problem.kind, "permission")
            self.assertNotIn("secret-transport-body", str(problem))
        old = self.prepare()
        self.f.http.initiation_reply = reply
        self.f.run_action("confirm_public", old.id)
        self.assertNotEqual(self.f.store.get(old.id).state, "Public Verified")
        self.assertEqual(self.f.http.initiations, 1)
