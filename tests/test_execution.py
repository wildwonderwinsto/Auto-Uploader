import json
import shutil
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import patch

import test_scheduling as fixtures
from upload_support import REMOTE_ID

from shortspublisher.execution import AutomaticPublisher, binding
from shortspublisher.library import SCHEMA_VERSION, Library
from shortspublisher.scheduling import ScheduleError
from shortspublisher.upload_http import UploadReply


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.SchedulingTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.SchedulingTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.SchedulingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.s, self.u = self.f.s, self.f.f
        self.a = AutomaticPublisher(self.s, self.u.service)
        self.u.http.privacy = "public"
        self.a.startup()

    def active(self):
        job = self.f.approve()
        self.a.authorize(job.id, job.revision, Event())
        self.a.control(False)
        return self.s.get(job.id)

    def due(self, job):
        self.f.now = datetime.fromisoformat(job.scheduled_utc)

    def dispatch(self, cancel=None, progress=lambda _: None):
        return self.a.dispatch(cancel or Event(), progress)

    def test_migration_defaults_paused(self):
        self.assertEqual(self.a.status()["paused"], 1)

    def test_disabled_plan_never_uploads(self):
        job = self.f.approve()
        self.a.control(False)
        self.due(job)
        self.dispatch()
        self.assertEqual(self.u.store.all(), [])

    def test_activation_bound_to_exact_plan(self):
        job = self.active()
        with closing(self.u.library.connect()) as db:
            consent = db.execute("select * from execution_consent").fetchone()
            self.assertEqual(consent["binding"], binding(job))
            self.assertEqual(consent["revision"], 1)
            self.assertIn("No second confirmation", consent["wording"])

    def test_due_upload_uses_existing_engine_and_verifies(self):
        job = self.active()
        self.due(job)
        progress = []
        result = self.dispatch(progress=lambda a: progress.append(a.acknowledged))
        attempt = result["attempts"][0]
        self.assertEqual(attempt.state, "Public Verified")
        self.assertEqual(attempt.remote_id, REMOTE_ID)
        self.assertGreater(self.u.http.verify_calls, 0)
        self.assertEqual(bytes(self.u.http.received), self.u.path.read_bytes())
        self.assertEqual(attempt.acknowledged, self.u.path.stat().st_size)
        self.assertTrue(progress)
        self.assertEqual(self.u.http.payload["status"]["privacyStatus"], "public")
        self.assertNotIn("publishAt", self.u.http.payload["status"])
        self.assertEqual(self.u.http.payload["snippet"]["categoryId"], "22")

    def test_repeated_dispatch_and_restart_no_second_upload(self):
        job = self.active()
        self.due(job)
        self.dispatch()
        for _ in range(3):
            self.a.startup()
            self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.assertEqual(len(self.u.store.all()), 1)

    def test_pause_prevents_claim(self):
        job = self.active()
        self.a.control(True)
        self.due(job)
        self.dispatch()
        self.assertFalse(self.u.store.all())

    def test_resume_never_catches_up(self):
        job = self.active()
        self.a.control(True)
        self.due(job)
        self.a.control(False)
        self.dispatch()
        self.assertEqual(self.s.get(job.id).state, "Missed")
        self.assertFalse(self.u.store.all())

    def test_startup_even_one_second_late_misses(self):
        job = self.active()
        self.due(job)
        self.f.now += timedelta(seconds=1)
        self.a.startup()
        self.dispatch()
        self.assertEqual(self.s.get(job.id).state, "Missed")
        self.assertFalse(self.u.store.all())

    def test_sleep_misses_window(self):
        job = self.active()
        self.due(job)
        self.f.now += timedelta(hours=3)
        self.dispatch()
        self.assertEqual(self.s.get(job.id).state, "Missed")
        self.assertFalse(self.u.store.all())

    def test_no_early_dispatch(self):
        self.active()
        self.dispatch()
        self.assertFalse(self.u.store.all())

    def test_boundary_sixty_seconds_missed(self):
        job = self.active()
        self.due(job)
        self.f.now += timedelta(seconds=60)
        self.dispatch()
        self.assertFalse(self.u.store.all())

    def test_clock_backwards_no_upload(self):
        job = self.active()
        self.due(job)
        self.f.now -= timedelta(seconds=1)
        self.dispatch()
        self.assertFalse(self.u.store.all())

    def test_reschedule_invalidates_activation(self):
        job = self.active()
        p = self.f.preview(
            local="2030-01-03T12:00:00", job_id=job.id, expected_revision=job.revision
        )
        updated = self.s.commit(p, approve=True)
        self.assertEqual(updated.execution_enabled, 0)
        with closing(self.u.library.connect()) as db:
            self.assertEqual(db.execute("select count(*) from execution_consent").fetchone()[0], 0)

    def test_cancel_before_claim(self):
        job = self.active()
        self.s.cancel(job.id, job.revision)
        self.due(job)
        self.dispatch()
        self.assertFalse(self.u.store.all())

    def test_cancel_after_claim_rejected(self):
        job = self.active()
        self.due(job)
        self.assertTrue(self.a.claim())
        with self.assertRaisesRegex(ScheduleError, "already claimed"):
            self.s.cancel(job.id, self.s.get(job.id).revision)

    def test_reschedule_after_claim_rejected(self):
        job = self.active()
        self.due(job)
        self.a.claim()
        with self.assertRaises(ScheduleError):
            self.f.preview(
                local="2030-01-03T12:00:00",
                job_id=job.id,
                expected_revision=self.s.get(job.id).revision,
            )

    def test_two_concurrent_claims_one_attempt(self):
        job = self.active()
        self.due(job)
        with ThreadPoolExecutor(2) as pool:
            claims = list(pool.map(lambda _: self.a.claim(), range(2)))
        self.assertEqual(sum(c is not None for c in claims), 1)
        self.assertEqual(len(self.u.store.all()), 1)

    def test_claim_atomic_rollback(self):
        job = self.active()
        self.due(job)
        with patch.object(self.s, "_write", side_effect=sqlite3.OperationalError("controlled")):
            with self.assertRaises(sqlite3.Error):
                self.a.claim()
        self.assertFalse(self.u.store.all())
        self.assertFalse(self.a.status()["executions"])

    def test_no_consent_generic_execution_rejected(self):
        result = self.u.run_action("scheduled", "invented")
        self.assertIn("error", result)
        self.assertEqual(self.u.http.initiations, 0)

    def test_tampered_snapshot_rejected_by_upload_boundary(self):
        job = self.active()
        self.due(job)
        identity = self.a.claim()
        attempt = self.u.store.all()[0]
        snapshot = attempt.snapshot
        snapshot["payload"]["snippet"]["title"] = "Unapproved"
        with closing(self.u.library.connect()) as db, db:
            db.execute(
                "update upload_attempts set snapshot=? where id=?",
                (json.dumps(snapshot), attempt.id),
            )
        self.assertIn("error", self.u.run_action("scheduled", identity))
        self.assertEqual(self.u.http.initiations, 0)

    def test_missing_credentials_block_before_post(self):
        job = self.active()
        self.u.credentials.delete("test-key")
        self.due(job)
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 0)
        self.assertEqual(self.s.get(job.id).state, "Needs Attention")
        self.assertEqual(self.a.status()["executions"][0]["phase"], "Blocked")

    def test_source_missing_blocks(self):
        job = self.active()
        self.u.path.rename(self.u.path.with_suffix(".moved"))
        self.due(job)
        self.dispatch()
        self.assertEqual(self.s.get(job.id).state, "Needs Attention")
        self.assertEqual(self.u.http.initiations, 0)

    def test_metadata_edit_blocks(self):
        job = self.active()
        p = self.u.preps.get(self.u.video.id)
        self.u.preps.save(self.u.video.id, replace(p.metadata, title="Changed"), p.revision)
        self.due(job)
        self.dispatch()
        self.assertEqual(self.s.get(job.id).state, "Needs Attention")
        self.assertEqual(self.u.http.initiations, 0)

    def test_paused_during_preflight_blocks_post(self):
        job = self.active()
        self.due(job)
        original = self.u.service.credentials

        def check(*args, **kwargs):
            result = original(*args, **kwargs)
            self.a.control(True)
            return result

        with patch.object(self.u.service, "credentials", side_effect=check):
            self.dispatch()
        self.assertEqual(self.u.http.initiations, 0)

    def test_slow_preflight_cannot_start_late(self):
        job = self.active()
        self.due(job)
        original = self.u.service.credentials

        def check(*args, **kwargs):
            result = original(*args, **kwargs)
            self.f.now += timedelta(seconds=61)
            return result

        with patch.object(self.u.service, "credentials", side_effect=check):
            self.dispatch()
        self.assertEqual(self.u.http.initiations, 0)

    def test_pause_active_upload_does_not_stop_transfer(self):
        job = self.active()
        self.due(job)
        self.u.http.after_chunk = lambda: self.a.control(True)
        result = self.dispatch()
        self.assertEqual(result["attempts"][0].state, "Public Verified")
        self.assertEqual(result["paused"], 1)

    def test_lost_initial_response_never_new_post(self):
        job = self.active()
        self.due(job)
        self.u.http.initiation_reply = UploadReply(503)
        self.dispatch()
        self.dispatch()
        self.a.startup()
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.assertEqual(self.u.store.all()[0].state, "Needs Reconciliation")

    def test_cancel_transfer_explicit_same_session_resume(self):
        job = self.active()
        self.due(job)
        cancel = Event()
        self.u.http.after_chunk = cancel.set
        self.dispatch(cancel)
        self.assertEqual(self.u.store.all()[0].state, "Paused")
        self.u.http.after_chunk = None
        self.a.startup()
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.u.run_action("resume", self.u.store.all()[0].id)
        self.assertEqual(self.u.store.all()[0].state, "Public Verified")
        self.assertEqual(self.u.http.initiations, 1)

    def test_private_result_preserved_as_discrepancy(self):
        job = self.active()
        self.due(job)
        self.u.http.privacy = "private"
        self.dispatch()
        self.assertEqual(self.u.store.all()[0].state, "Needs Reconciliation")
        self.assertEqual(self.u.store.all()[0].remote_id, REMOTE_ID)

    def test_processing_not_complete(self):
        job = self.active()
        self.due(job)
        self.u.http.processing = "uploaded"
        self.dispatch()
        self.assertEqual(self.u.store.all()[0].state, "Processing")

    def test_limit_bounds_and_persistence(self):
        for limit in (0, 101):
            with self.assertRaises(ScheduleError):
                self.a.control(True, limit)
        # Up to YouTube's own 100 uploads a day; the database accepts it since schema 14.
        for limit in (15, 100, 2):
            self.a.control(True, limit)
            self.assertEqual(self.a.status()["daily_limit"], limit)

    def test_legacy_migration_preserves_every_column(self):
        target = self.u.root / "legacy.sqlite3"
        shutil.copy2("tests/fixtures/library-v6.sqlite3", target)
        with sqlite3.connect(target) as db:
            names = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
            original = {n: db.execute("select * from " + n).fetchall() for n in names}
        lib = Library(target)
        lib.initialize()
        with closing(lib.connect()) as db:
            for name, values in original.items():
                self.assertEqual(
                    [tuple(r) for r in db.execute("select * from " + name)], values, name
                )
            self.assertEqual(db.execute("pragma user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(
                db.execute("select sum(execution_enabled) from scheduled_jobs").fetchone()[0], 0
            )
            self.assertEqual(db.execute("select paused from publishing_control").fetchone()[0], 1)
            self.assertFalse(db.execute("pragma foreign_key_check").fetchall())

    def test_migration_rollback(self):
        target = self.u.root / "rollback.sqlite3"
        shutil.copy2("tests/fixtures/library-v6.sqlite3", target)
        with sqlite3.connect(target) as db:
            db.execute("create table publishing_control (id integer)")
        with self.assertRaises(sqlite3.Error):
            Library(target).initialize()
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute("pragma user_version").fetchone()[0], 6)
            self.assertIn(
                "CHECK(execution_enabled=0)",
                db.execute("select sql from sqlite_master where name='scheduled_jobs'").fetchone()[
                    0
                ],
            )

    def test_reconciled_attempt_can_close_plan_but_never_reuse_job(self):
        job = self.active()
        self.due(job)
        self.u.http.initiation_reply = UploadReply(503)
        self.dispatch()
        attempt = self.u.store.all()[0]
        self.u.run_action("release", attempt.id)
        closed = self.s.cancel(job.id, self.s.get(job.id).revision)
        self.assertEqual(closed.state, "Cancelled")
        self.assertEqual(len(self.a.status()["executions"]), 1)
        self.assertEqual(self.u.http.initiations, 1)
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)

    def test_claim_then_restart_cannot_initiate(self):
        job = self.active()
        self.due(job)
        identity = self.a.claim()
        self.a.startup()
        result = self.u.run_action("scheduled", identity)
        self.assertIn("error", result)
        self.assertEqual(self.u.http.initiations, 0)

    def test_upload_permission_missing_blocks(self):
        from shortspublisher.auth import SCOPE

        job = self.active()
        self.u.credentials.values["test-key"].scopes = frozenset((SCOPE,))
        self.due(job)
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 0)
        self.assertEqual(self.s.get(job.id).state, "Needs Attention")

    def test_unexpected_live_channel_blocks(self):
        job = self.active()
        self.u.youtube.items = [replace(self.u.youtube.items[0], id="UC_wrong")]
        self.due(job)
        self.dispatch()
        self.assertGreater(self.u.youtube.calls, 0)
        self.assertEqual(self.u.http.initiations, 0)

    def test_expired_token_refreshes(self):
        job = self.active()
        self.u.credentials.values["test-key"].expires_at = 0
        self.due(job)
        self.dispatch()
        self.assertEqual(self.u.oauth.refresh_count, 1)
        self.assertEqual(self.u.store.all()[0].state, "Public Verified")

    def test_revoked_refresh_blocks(self):
        from shortspublisher.auth import AccountError

        job = self.active()
        self.u.credentials.values["test-key"].expires_at = 0
        self.u.oauth.error = AccountError("Reconnect required", "reconnect")
        self.due(job)
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 0)

    def test_source_rehash_at_execution_boundary(self):
        job = self.active()
        self.due(job)
        identity = self.a.claim()
        with self.u.path.open("r+b") as file:
            file.write(b"changed bytes")
        self.assertIn("error", self.u.run_action("scheduled", identity))
        self.assertEqual(self.u.http.initiations, 0)

    def test_session_write_failure_never_reinitiates(self):
        job = self.active()
        self.due(job)
        self.u.sessions.fail = True
        self.dispatch()
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.assertFalse(self.u.http.received)
        self.assertEqual(self.u.store.all()[0].state, "Needs Reconciliation")

    def test_expired_session_preserved(self):
        job = self.active()
        self.due(job)
        cancel = Event()
        self.u.http.after_chunk = cancel.set
        self.dispatch(cancel)
        self.u.http.after_chunk = None
        self.u.http.query_replies = [UploadReply(410)]
        self.u.run_action("resume", self.u.store.all()[0].id)
        self.assertEqual(self.u.store.all()[0].state, "Needs Reconciliation")
        self.assertEqual(self.u.http.initiations, 1)

    def test_lost_final_response_reuses_session(self):
        job = self.active()
        self.due(job)
        self.u.http.lost_final = True
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.assertEqual(self.u.store.all()[0].state, "Public Verified")

    def test_long_retry_after_requires_explicit_resume(self):
        job = self.active()
        self.due(job)
        self.u.http.query_replies = [UploadReply(429, headers={"retry-after": "120"})]
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.assertEqual(self.u.store.all()[0].state, "Interrupted")
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)

    def test_failed_verification_preserves_id_and_no_reupload(self):
        job = self.active()
        self.due(job)
        self.u.http.verify_reply = UploadReply(503)
        self.dispatch()
        attempt = self.u.store.all()[0]
        self.assertEqual(attempt.remote_id, REMOTE_ID)
        self.assertEqual(attempt.state, "Transfer Complete")
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)

    def test_processing_failure_distinct_from_public_success(self):
        job = self.active()
        self.due(job)
        self.u.http.processing = "rejected"
        self.dispatch()
        self.assertEqual(self.u.store.all()[0].state, "Failed")
        self.assertEqual(self.u.store.all()[0].privacy, "public")

    def second_job(self):
        from support import make_video

        from shortspublisher.media import Inspector

        path = self.u.root / "second.mp4"
        make_video(path, duration=1)
        video, _ = self.u.library.save(str(path), Inspector().inspect(path, Event()))
        p = self.u.preps.save(video.id, self.u.preps.get(self.u.video.id).metadata, 0)
        self.u.preps.complete(video.id, p.revision, video.inspection_revision)
        proposal = self.f.preview(video_id=video.id, local="2030-01-02T12:05:00")
        job = self.s.commit(proposal, approve=True)
        self.a.authorize(job.id, job.revision, Event())
        return job

    def test_new_claim_blocked_by_daily_budget(self):
        first = self.active()
        second = self.second_job()
        self.a.control(False, 1)
        self.due(first)
        self.dispatch()
        self.due(second)
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.assertIn("daily automatic attempt limit", self.s.get(second.id).reason)

    def test_known_quota_blocks_other_job(self):
        first = self.active()
        second = self.second_job()
        self.due(first)
        self.u.http.initiation_reply = UploadReply(
            403, {"error": {"errors": [{"reason": "quotaExceeded"}]}}
        )
        self.dispatch()
        # UploadStore timestamps use the actual wall clock; place this controlled quota
        # observation on the injected scheduler's current date.
        with closing(self.u.library.connect()) as db, db:
            db.execute("UPDATE upload_attempts SET updated_at=?", (self.f.now.isoformat(),))
        self.due(second)
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.assertIn("quota or channel limit", self.s.get(second.id).reason)

    def test_distinct_future_jobs_execute_deterministically(self):
        first = self.active()
        second = self.second_job()
        self.due(first)
        self.dispatch()
        self.u.http.received.clear()
        self.due(second)
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 2)
        self.assertEqual(len(self.a.status()["executions"]), 2)
        self.assertTrue(all(a.state == "Public Verified" for a in self.u.store.all()))

    def test_separate_process_claims_unique(self):
        import subprocess
        import sys

        job = self.active()
        self.due(job)
        script = """
import sys
from datetime import datetime
from types import SimpleNamespace
from shortspublisher.library import Library, SCHEMA_VERSION
from shortspublisher.scheduling import ScheduleService
from shortspublisher.execution import AutomaticPublisher
from shortspublisher.upload_store import UploadStore
library=Library(__import__('pathlib').Path(sys.argv[1]))
s=ScheduleService(library, lambda: datetime.fromisoformat(sys.argv[2]))
a=AutomaticPublisher(s, SimpleNamespace(store=UploadStore(library),chunk_size=262144))
print(a.claim())
"""
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(self.u.library.path), self.f.now.isoformat()],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        results = [p.communicate(timeout=10) for p in processes]
        self.assertTrue(all(p.returncode == 0 for p in processes), results)
        self.assertEqual(sum(out.strip() != "None" for out, _ in results), 1)
        self.assertEqual(len(self.u.store.all()), 1)

    def test_manual_lock_blocks_claim_without_late_catchup(self):
        from filelock import FileLock

        job = self.active()
        self.due(job)
        with FileLock(str(self.u.service.lock.lock_file)):
            result = self.dispatch()
        self.assertIn("Another upload", result["error"])
        self.assertFalse(self.u.store.all())
        self.f.now += timedelta(seconds=60)
        self.dispatch()
        self.assertFalse(self.u.store.all())

    def test_pre_cancelled_dispatch_no_claim(self):
        job = self.active()
        self.due(job)
        cancel = Event()
        cancel.set()
        with self.assertRaises(ScheduleError):
            self.dispatch(cancel)
        self.assertFalse(self.u.store.all())

    def test_draft_cannot_activate(self):
        job = self.s.commit(self.f.preview(), approve=False)
        with self.assertRaises(ScheduleError):
            self.a.authorize(job.id, job.revision, Event())
        self.assertEqual(self.s.get(job.id).execution_enabled, 0)

    def test_stale_activation_cannot_restore_cancelled_job(self):
        job = self.f.approve()
        self.s.cancel(job.id, job.revision)
        with self.assertRaises(ScheduleError):
            self.a.authorize(job.id, job.revision, Event())
        self.assertEqual(self.s.get(job.id).state, "Cancelled")

    def test_database_failure_before_post_never_retries_new_session(self):
        job = self.active()
        self.due(job)
        original = self.u.store.update

        def update(identity, state, **values):
            if state == "Preparing":
                raise sqlite3.OperationalError("controlled write failure")
            return original(identity, state, **values)

        with patch.object(self.u.store, "update", side_effect=update):
            self.dispatch()
        self.a.startup()
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 0)
        self.assertEqual(len(self.u.store.all()), 1)
        self.assertEqual(self.u.store.all()[0].state, "Interrupted")
        self.assertIn(
            "error", self.u.run_action("scheduled", self.a.status()["executions"][0]["id"])
        )

    def test_source_change_during_transfer_stops_further_chunks(self):
        job = self.active()
        self.due(job)

        def change():
            self.u.http.after_chunk = None
            with self.u.path.open("ab") as file:
                file.write(b"changed")

        self.u.http.after_chunk = change
        self.dispatch()
        self.assertEqual(len(self.u.http.offsets), 1)
        self.assertEqual(self.u.store.all()[0].state, "Needs Reconciliation")
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
