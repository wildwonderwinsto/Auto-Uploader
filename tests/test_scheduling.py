"""Local scheduling tests: real SQLite/media, injectable clocks, no publishing calls."""

import json
import os
import shutil
import sqlite3
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import patch

import test_uploads as fixtures
from account_support import CHANNEL

from shortspublisher.library import Library
from shortspublisher.scheduling import (
    ScheduleError,
    ScheduleService,
    due_state,
    local_choices,
    resolve_time,
)

NOW = datetime(2030, 1, 1, tzinfo=UTC)


class TimeTests(unittest.TestCase):
    def test_utc(self):
        self.assertEqual(resolve_time("2030-01-02T12:00:00", "UTC", None, NOW).hour, 12)

    def test_chicago(self):
        self.assertEqual(resolve_time("2030-01-02T12:00:00", "America/Chicago", None, NOW).hour, 18)

    def test_half_hour(self):
        self.assertEqual(
            resolve_time("2030-01-02T12:00:00", "Asia/Kolkata", None, NOW).strftime("%H:%M"),
            "06:30",
        )

    def test_quarter_hour(self):
        self.assertEqual(
            resolve_time("2030-01-02T12:00:00", "Asia/Kathmandu", None, NOW).strftime("%H:%M"),
            "06:15",
        )

    def test_nonexistent(self):
        with self.assertRaisesRegex(ScheduleError, "does not exist"):
            local_choices("2030-03-10T02:30:00", "America/Chicago")

    def test_ambiguous_requires_choice(self):
        with self.assertRaisesRegex(ScheduleError, "twice"):
            resolve_time("2030-11-03T01:30:00", "America/Chicago", None, NOW)

    def test_ambiguous_occurrences(self):
        first = resolve_time("2030-11-03T01:30:00", "America/Chicago", 0, NOW)
        second = resolve_time("2030-11-03T01:30:00", "America/Chicago", 1, NOW)
        self.assertEqual(second - first, timedelta(hours=1))

    def test_invalid_zone(self):
        with self.assertRaises(ScheduleError):
            local_choices("2030-01-02T12:00:00", "Invalid/Zone")

    def test_invalid_date(self):
        with self.assertRaises(ScheduleError):
            local_choices("2030-02-30T12:00:00", "UTC")

    def test_aware_wall_rejected(self):
        with self.assertRaises(ScheduleError):
            local_choices("2030-01-02T12:00:00+01:00", "UTC")

    def test_past_and_exact_now(self):
        for value in ("2029-12-31T23:59:59", "2030-01-01T00:00:00"):
            with self.assertRaises(ScheduleError):
                resolve_time(value, "UTC", None, NOW)

    def test_exact_due_boundary(self):
        self.assertEqual(due_state(NOW, NOW - timedelta(microseconds=1)), "Scheduled")
        self.assertEqual(due_state(NOW, NOW), "Due")
        self.assertEqual(due_state(NOW, NOW + timedelta(seconds=59)), "Due")
        self.assertEqual(due_state(NOW, NOW + timedelta(seconds=60)), "Missed")

    def test_system_zone_does_not_change_instant(self):
        with patch.dict(os.environ, {"TZ": "Asia/Tokyo"}):
            a = resolve_time("2030-01-02T12:00:00", "America/Chicago", None, NOW)
        with patch.dict(os.environ, {"TZ": "Pacific/Honolulu"}):
            b = resolve_time("2030-01-02T12:00:00", "America/Chicago", None, NOW)
        self.assertEqual(a, b)

    def test_far_future(self):
        self.assertEqual(resolve_time("9998-01-01T12:00:00", "UTC", None, NOW).year, 9998)


class SchedulingTests(unittest.TestCase):
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
        self.now = NOW
        self.s = ScheduleService(self.f.library, lambda: self.now)

    def preview(self, **kwargs):
        args = dict(
            video_id=self.f.video.id, channel_id=CHANNEL.id, local="2030-01-02T12:00:00", zone="UTC"
        )
        args.update(kwargs)
        return self.s.preview(**args)

    def approve(self):
        return self.s.commit(self.preview(), approve=True)

    def test_preview_does_not_save_or_upload(self):
        self.preview()
        self.assertEqual(self.s.all(), [])
        self.assertEqual(self.f.http.initiations, 0)

    def test_draft_and_approval(self):
        draft = self.s.commit(self.preview(), approve=False)
        self.assertEqual(draft.state, "Draft")
        self.assertEqual(draft.approved_at, "")
        approved = self.s.commit(
            self.preview(job_id=draft.id, expected_revision=draft.revision), approve=True
        )
        self.assertEqual(approved.id, draft.id)
        self.assertEqual(approved.state, "Scheduled")
        self.assertTrue(approved.approved_at)

    def test_approved_snapshot_and_execution_disabled(self):
        job = self.approve()
        self.assertEqual(
            job.snapshot["metadata"]["title"], self.f.preps.get(self.f.video.id).metadata.title
        )
        self.assertEqual(job.snapshot["privacy"], "public")
        self.assertEqual(job.execution_enabled, 0)

    def test_persistence_new_service(self):
        job = self.approve()
        self.assertEqual(ScheduleService(self.f.library).get(job.id), job)

    def test_unique_drafts(self):
        a = self.s.commit(self.preview(), approve=False)
        b = self.s.commit(self.preview(), approve=False)
        self.assertNotEqual(a.id, b.id)

    def test_duplicate_schedule(self):
        self.approve()
        with self.assertRaisesRegex(ScheduleError, "active plan"):
            self.preview()

    def test_duplicate_upload_completed(self):
        self.f.confirm()
        with self.assertRaisesRegex(ScheduleError, "Upload history"):
            self.preview()

    def test_duplicate_upload_unresolved(self):
        self.f.prepare()
        with self.assertRaisesRegex(ScheduleError, "Upload history"):
            self.preview()

    def test_manual_upload_blocks_active_plan(self):
        self.approve()
        result = self.f.run_action("prepare", (self.f.video.id, 1, CHANNEL.id))
        self.assertIn("active schedule", result["error"])

    def test_private_review_rejected(self):
        p = self.f.preps.get(self.f.video.id)
        p = self.f.preps.save(self.f.video.id, replace(p.metadata, privacy="private"), p.revision)
        self.f.preps.complete(self.f.video.id, p.revision, self.f.video.inspection_revision)
        with self.assertRaisesRegex(ScheduleError, "Public"):
            self.preview()

    def test_incomplete_review(self):
        p = self.f.preps.get(self.f.video.id)
        self.f.preps.save(self.f.video.id, replace(p.metadata, title="Changed"), p.revision)
        with self.assertRaisesRegex(ScheduleError, "review"):
            self.preview()

    def test_wrong_channel(self):
        with self.assertRaisesRegex(ScheduleError, "channel"):
            self.preview(channel_id="wrong")

    def test_disconnected(self):
        self.f.repo.clear()
        with self.assertRaisesRegex(ScheduleError, "Connect"):
            self.preview()

    def test_missing_source(self):
        self.f.path.unlink()
        with self.assertRaises(ScheduleError):
            self.preview()

    def test_changed_source(self):
        self.f.path.write_bytes(self.f.path.read_bytes() + b"changed")
        with self.assertRaises(ScheduleError):
            self.preview()

    def test_metadata_changed_after_preview(self):
        p = self.preview()
        old = self.f.preps.get(self.f.video.id)
        new = self.f.preps.save(self.f.video.id, replace(old.metadata, title="New"), old.revision)
        self.f.preps.complete(self.f.video.id, new.revision, self.f.video.inspection_revision)
        with self.assertRaisesRegex(ScheduleError, "stale"):
            self.s.commit(p, approve=True)
        self.assertEqual(self.s.all(), [])

    def test_due_and_missed_idempotent(self):
        job = self.approve()
        self.now = datetime.fromisoformat(job.scheduled_utc)
        self.assertEqual(self.s.evaluate()[0].state, "Due")
        a = self.s.get(job.id)
        self.s.evaluate()
        self.assertEqual(self.s.get(job.id), a)
        self.now += timedelta(seconds=60)
        self.assertEqual(self.s.evaluate()[0].state, "Missed")
        self.assertEqual(self.f.http.initiations, 0)

    def test_restart_overdue(self):
        job = self.approve()
        other = ScheduleService(
            self.f.library, lambda: datetime.fromisoformat(job.scheduled_utc) + timedelta(days=2)
        )
        self.assertEqual(other.evaluate()[0].state, "Missed")

    def test_clock_rollback_never_rearms(self):
        job = self.approve()
        self.now = datetime.fromisoformat(job.scheduled_utc) + timedelta(days=1)
        self.s.evaluate()
        self.now = NOW
        self.assertEqual(self.s.evaluate()[0].state, "Missed")

    def test_source_deleted_marks_attention(self):
        self.approve()
        self.f.path.unlink()
        self.assertEqual(self.s.evaluate()[0].state, "Needs Attention")
        self.assertIn("Source requires attention", self.s.all()[0].reason)

    def test_library_deleted_preserves_job(self):
        job = self.approve()
        self.f.library.remove(self.f.video.id)
        actual = self.s.evaluate()[0]
        self.assertEqual(actual.state, "Needs Attention")
        self.assertEqual(actual.snapshot, job.snapshot)

    def test_account_disconnected_preserves_job(self):
        job = self.approve()
        self.f.repo.clear()
        self.assertEqual(self.s.evaluate()[0].state, "Needs Attention")
        self.assertEqual(self.s.get(job.id).snapshot, job.snapshot)

    def test_metadata_change_marks_attention(self):
        job = self.approve()
        p = self.f.preps.get(self.f.video.id)
        self.f.preps.save(self.f.video.id, replace(p.metadata, title="new"), p.revision)
        self.assertEqual(self.s.evaluate()[0].state, "Needs Attention")
        self.assertEqual(self.s.get(job.id).snapshot, job.snapshot)

    def test_reschedule_identity_and_events(self):
        job = self.approve()
        new = self.s.commit(
            self.preview(
                local="2030-01-03T12:00:00", job_id=job.id, expected_revision=job.revision
            ),
            approve=True,
        )
        self.assertEqual(new.id, job.id)
        self.assertGreater(new.revision, job.revision)
        self.assertNotEqual(new.scheduled_utc, job.scheduled_utc)
        with self.f.library.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM schedule_events").fetchone()[0], 2)

    def test_stale_reschedule_rejected(self):
        job = self.approve()
        p = self.preview(job_id=job.id, expected_revision=job.revision)
        self.s.cancel(job.id, job.revision)
        with self.assertRaises(ScheduleError):
            self.s.commit(p, approve=True)

    def test_approved_not_demoted_to_draft(self):
        job = self.approve()
        p = self.preview(job_id=job.id, expected_revision=job.revision)
        with self.assertRaises(ScheduleError):
            self.s.commit(p, approve=False)

    def test_cancel_preserves_snapshot_source(self):
        job = self.approve()
        new = self.s.cancel(job.id, job.revision)
        self.assertEqual(new.snapshot, job.snapshot)
        self.assertEqual(new.state, "Cancelled")
        self.assertTrue(new.cancelled_at)
        self.assertTrue(self.f.path.exists())

    def test_cancel_stale_revision(self):
        job = self.approve()
        with self.assertRaises(ScheduleError):
            self.s.cancel(job.id, 0)

    def test_cancelled_allows_new_plan(self):
        job = self.approve()
        self.s.cancel(job.id, job.revision)
        new = self.approve()
        self.assertNotEqual(new.id, job.id)

    def test_expired_confirmation(self):
        p = self.preview()
        self.now = datetime.fromisoformat(p.scheduled_utc)
        with self.assertRaises(ScheduleError):
            self.s.commit(p, approve=True)

    def test_mutated_proposal_rejected(self):
        p = self.preview()
        p.snapshot["metadata"]["title"] = "tampered"
        with self.assertRaises(ScheduleError):
            self.s.commit(p, approve=True)

    def test_transaction_rollback(self):
        p = self.preview()
        original = self.s._write

        def fail(db, job, **kw):
            original(db, job, **kw)
            raise sqlite3.OperationalError("injected")

        with patch.object(self.s, "_write", side_effect=fail):
            with self.assertRaises(sqlite3.OperationalError):
                self.s.commit(p, approve=True)
        self.assertEqual(self.s.all(), [])

    def test_evaluation_cancel_rolls_back(self):
        job = self.approve()
        self.now = datetime.fromisoformat(job.scheduled_utc)
        event = Event()
        event.set()
        with self.assertRaises(ScheduleError):
            self.s.evaluate(event)
        self.assertEqual(self.s.get(job.id), job)

    def test_no_network_or_credentials(self):
        with (
            patch("requests.Session.request", side_effect=AssertionError("No HTTP")),
            patch.object(self.f.credentials, "get", side_effect=AssertionError("No secrets")),
        ):
            job = self.approve()
            self.now = datetime.fromisoformat(job.scheduled_utc)
            self.s.evaluate()
        self.assertEqual(self.f.http.initiations, 0)

    def test_migration_preserves_genuine_v5(self):
        target = self.f.root / "migrate.sqlite3"
        shutil.copy2(Path(__file__).parent / "fixtures/library-v5.sqlite3", target)
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)
            tables = {
                r[0]: None
                for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            before = {t: db.execute("SELECT * FROM " + t).fetchall() for t in tables}
        Library(target).initialize()
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 11)
            self.assertEqual(
                before, {t: db.execute("SELECT * FROM " + t).fetchall() for t in tables}
            )

    def test_migration_rollback(self):
        target = self.f.root / "rollback.sqlite3"
        shutil.copy2(Path(__file__).parent / "fixtures/library-v5.sqlite3", target)
        library = Library(target)
        original = library.connect

        def connect():
            db = original()
            db.set_authorizer(
                lambda op, a, b, c, d: (
                    sqlite3.SQLITE_DENY if op == sqlite3.SQLITE_CREATE_INDEX else sqlite3.SQLITE_OK
                )
            )
            return db

        with patch.object(library, "connect", side_effect=connect):
            with self.assertRaises(sqlite3.DatabaseError):
                library.initialize()
        with sqlite3.connect(target) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertIsNone(
                db.execute("SELECT name FROM sqlite_master WHERE name='scheduled_jobs'").fetchone()
            )

    def test_two_instances_cannot_approve_same_publication(self):
        first = self.preview()
        other = ScheduleService(self.f.library, lambda: self.now)
        second = other.preview(self.f.video.id, CHANNEL.id, "2030-01-03T12:00:00", "UTC")
        self.s.commit(first, approve=True)
        with self.assertRaises(ScheduleError):
            other.commit(second, approve=True)
        self.assertEqual(len(self.s.all()), 1)

    def test_two_instances_reject_lost_update(self):
        job = self.approve()
        other = ScheduleService(self.f.library, lambda: self.now)
        stale = other.preview(
            self.f.video.id,
            CHANNEL.id,
            "2030-01-03T12:00:00",
            "UTC",
            job_id=job.id,
            expected_revision=job.revision,
        )
        self.s.commit(
            self.preview(
                local="2030-01-04T12:00:00", job_id=job.id, expected_revision=job.revision
            ),
            approve=True,
        )
        with self.assertRaises(ScheduleError):
            other.commit(stale, approve=True)
        self.assertTrue(self.s.get(job.id).local_time.startswith("2030-01-04"))

    def test_reapproval_preserves_original_snapshot_event(self):
        job = self.approve()
        prep = self.f.preps.get(self.f.video.id)
        saved = self.f.preps.save(
            self.f.video.id, replace(prep.metadata, title="Reapproved title"), prep.revision
        )
        old = self.s.evaluate()[0]
        self.f.preps.complete(self.f.video.id, saved.revision, self.f.video.inspection_revision)
        new = self.s.commit(
            self.preview(job_id=old.id, expected_revision=old.revision), approve=True
        )
        self.assertEqual(new.snapshot["metadata"]["title"], "Reapproved title")
        with self.f.library.connect() as db:
            original = json.loads(
                db.execute(
                    "SELECT record FROM schedule_events WHERE job_id=? AND revision=1", (job.id,)
                ).fetchone()[0]
            )
        self.assertEqual(original["snapshot"], job.snapshot)

    def test_database_refuses_execution_activation(self):
        job = self.approve()
        with self.f.library.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE scheduled_jobs SET execution_enabled=1 WHERE id=?", (job.id,))
        self.assertEqual(self.s.get(job.id).execution_enabled, 0)

    def test_distinct_jobs_sort_by_utc(self):
        from support import make_video

        from shortspublisher.media import Inspector

        path = self.f.root / "second.mp4"
        make_video(path, duration=1)
        video, _ = self.f.library.save(str(path), Inspector().inspect(path, Event()))
        original = self.f.preps.get(self.f.video.id)
        saved = self.f.preps.save(video.id, original.metadata, 0)
        self.f.preps.complete(video.id, saved.revision, video.inspection_revision)
        first = self.approve()
        second = self.s.commit(
            self.preview(video_id=video.id, local="2030-01-02T01:00:00"), approve=True
        )
        self.assertEqual([j.id for j in self.s.all()], [second.id, first.id])
        self.s.cancel(first.id, first.revision)
        self.assertEqual(self.s.get(second.id), second)

    def test_same_bytes_different_library_path_blocked(self):
        from shortspublisher.media import Inspector

        path = self.f.root / "copy.mp4"
        shutil.copy2(self.f.path, path)
        video, _ = self.f.library.save(str(path), Inspector().inspect(path, Event()))
        original = self.f.preps.get(self.f.video.id)
        saved = self.f.preps.save(video.id, original.metadata, 0)
        self.f.preps.complete(video.id, saved.revision, video.inspection_revision)
        self.approve()
        with self.assertRaisesRegex(ScheduleError, "identical content"):
            self.preview(video_id=video.id)
