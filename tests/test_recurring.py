"""Real SQLite and media with controlled Public transport and an injected clock."""

import shutil
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import patch

import test_execution as fixtures
from account_support import CHANNEL
from support import make_video
from test_preparation import VALID

from shortspublisher.auth import AccountError
from shortspublisher.cadence import Rule
from shortspublisher.execution import AutomaticPublisher
from shortspublisher.media import Cancelled, Inspector
from shortspublisher.recurring import RecurringService
from shortspublisher.scheduling import ScheduleError


class RecurringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ExecutionTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ExecutionTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.ExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.a, self.u, self.s = self.f.a, self.f.u, self.f.s
        self.r = RecurringService(self.s)
        original_request = self.u.http.request

        def request(method, *args, **kwargs):
            if method == "POST":
                self.u.http.received.clear()  # A fresh controlled session for each distinct upload.
            return original_request(method, *args, **kwargs)

        self.u.http.request = request
        self.rule = Rule("UTC", tuple(range(7)), "09:00", "21:00", 2)

    def proposal(self, videos=None, **kw):
        return self.r.preview(
            videos or [self.u.video.id], CHANNEL.id, self.rule, "My queue", Event(), **kw
        )

    def approve(self, videos=None, **kw):
        return self.r.approve(
            self.proposal(videos, **kw), Event(), consent=True, restrictions_resolved=True
        )

    def active(self, videos=None, **kw):
        identity = self.approve(videos, **kw)
        self.r.control(identity, 1, "resume")
        self.a.control(False)
        return identity

    def due(self):
        self.f.f.now = datetime.fromisoformat(self.r.state()["plans"][0]["next_slot"])

    def dispatch(self):
        return self.a.dispatch(Event(), lambda _: None)

    def extra(self, review=True, same=False):
        path = self.u.root / "second.mp4"
        if same:
            shutil.copyfile(self.u.path, path)
        else:
            make_video(path, duration=4)
        video, _ = self.u.library.save(str(path), Inspector().inspect(path, Event()))
        if review:
            prep = self.u.preps.save(
                video.id, replace(VALID, privacy="public", title="Second video"), 0
            )
            self.u.preps.complete(video.id, prep.revision, video.inspection_revision)
        return video

    def test_preview_creates_no_plan(self):
        self.proposal()
        self.assertFalse(self.r.state()["plans"])
        self.assertFalse(self.u.store.all())

    def test_explicit_consent_required(self):
        p = self.proposal()
        for consent, restrictions in ((False, True), (True, False)):
            with self.assertRaises(ScheduleError):
                self.r.approve(p, Event(), consent=consent, restrictions_resolved=restrictions)
        self.assertFalse(self.r.state()["plans"])

    def test_approval_is_paused_and_public(self):
        self.approve()
        state = self.r.state()
        self.assertFalse(state["plans"][0]["enabled"])
        self.assertEqual(state["items"][0]["snapshot"]["privacy"], "public")
        self.assertFalse(self.s.all())
        self.assertFalse(self.u.store.all())

    def test_batch_order_persists(self):
        second = self.extra()
        self.approve([second.id, self.u.video.id])
        self.assertEqual(
            [i["video_id"] for i in RecurringService(self.s).state()["items"]],
            [second.id, self.u.video.id],
        )

    def test_unreviewed_rejected(self):
        second = self.extra(review=False)
        with self.assertRaises(ScheduleError):
            self.proposal([second.id])

    def test_duplicate_batch_bytes_rejected(self):
        second = self.extra(same=True)
        with self.assertRaises(ScheduleError):
            self.proposal([self.u.video.id, second.id])

    def test_other_queue_same_content_blocked(self):
        self.approve()
        second = self.extra(same=True)
        with self.assertRaises(ScheduleError):
            self.proposal([second.id])

    def test_existing_individual_schedule_blocked(self):
        self.f.f.approve()
        with self.assertRaises(ScheduleError):
            self.proposal()

    def test_manual_upload_after_queue_is_blocked(self):
        self.approve()
        result = self.u.run_action("prepare_public", (self.u.video.id, 1, CHANNEL.id))
        self.assertIn("recurring", result["error"])
        self.assertFalse(self.u.store.all())

    def test_individual_schedule_after_queue_is_blocked(self):
        self.approve()
        with self.assertRaises(ScheduleError):
            self.f.f.preview()

    def test_prior_manual_result_blocks(self):
        self.u.confirm()
        with self.assertRaises(ScheduleError):
            self.proposal()

    def test_unknown_result_blocks(self):
        attempt = self.u.prepare()
        self.u.store.update(attempt.id, "Needs Reconciliation")
        with self.assertRaises(ScheduleError):
            self.proposal()

    def test_changed_metadata_stales_confirmation(self):
        p = self.proposal()
        prep = self.u.preps.get(self.u.video.id)
        self.u.preps.save(self.u.video.id, replace(prep.metadata, title="Changed"), prep.revision)
        with self.assertRaises(ScheduleError):
            self.r.approve(p, Event(), consent=True, restrictions_resolved=True)
        self.assertFalse(self.r.state()["plans"])

    def test_reorder_and_remove(self):
        second = self.extra()
        identity = self.approve([self.u.video.id, second.id])
        items = self.r.state()["items"]
        self.r.control(identity, 1, "up", items[1]["id"])
        self.assertEqual(self.r.state()["items"][0]["video_id"], second.id)
        self.r.control(identity, 2, "remove", items[1]["id"])
        self.assertEqual(self.r.state()["items"][0]["state"], "Removed")
        self.assertEqual(len(self.u.library.all()), 2)

    def test_stale_reorder_rejected(self):
        identity = self.approve()
        self.r.control(identity, 1, "resume")
        with self.assertRaises(ScheduleError):
            self.r.control(identity, 1, "pause")

    def test_append_requires_new_approval(self):
        identity = self.approve()
        second = self.extra()
        p = self.proposal([second.id], plan_id=identity)
        self.assertEqual(len(self.r.state()["items"]), 1)
        self.r.approve(p, Event(), consent=True, restrictions_resolved=True)
        self.assertEqual(len(self.r.state()["items"]), 2)

    def test_rule_change_rejected(self):
        identity = self.approve()
        second = self.extra()
        self.rule = replace(self.rule, hours=3)
        with self.assertRaises(ScheduleError):
            self.proposal([second.id], plan_id=identity)

    def test_pause_does_not_consume(self):
        self.approve()
        self.a.control(False)
        self.due()
        self.dispatch()
        self.assertFalse(self.u.store.all())
        self.assertEqual(self.r.state()["items"][0]["state"], "Queued")
        self.assertEqual(self.r.state()["slots"][0]["state"], "Skipped")

    def test_global_pause_does_not_consume(self):
        identity = self.approve()
        self.r.control(identity, 1, "resume")
        self.due()
        self.dispatch()
        self.assertFalse(self.u.store.all())
        self.assertEqual(self.r.state()["items"][0]["state"], "Queued")

    def test_one_slot_uses_real_public_engine(self):
        self.active()
        self.due()
        self.dispatch()
        attempts = self.u.store.all()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].state, "Public Verified")
        self.assertEqual(attempts[0].snapshot["payload"]["status"]["privacyStatus"], "public")
        self.assertNotIn("publishAt", attempts[0].snapshot["payload"]["status"])
        self.assertEqual(bytes(self.u.http.received), self.u.path.read_bytes())

    def test_two_slots_different_videos(self):
        second = self.extra()
        self.active([self.u.video.id, second.id])
        self.due()
        self.dispatch()
        self.dispatch()
        self.assertEqual(len(self.u.store.all()), 1)
        self.due()
        self.dispatch()
        self.assertEqual({a.video_id for a in self.u.store.all()}, {self.u.video.id, second.id})
        self.assertTrue(all(a.state == "Public Verified" for a in self.u.store.all()))

    def test_empty_queue_skips(self):
        identity = self.active()
        item = self.r.state()["items"][0]
        self.r.control(identity, 2, "remove", item["id"])
        self.due()
        self.dispatch()
        self.assertFalse(self.u.store.all())
        self.assertIn("No eligible", self.r.state()["slots"][0]["reason"])

    def test_missed_slots_no_burst(self):
        self.active()
        self.f.f.now += timedelta(days=4000)
        self.dispatch()
        self.assertFalse(self.u.store.all())
        self.assertEqual(len(self.r.state()["slots"]), 1)
        self.assertIn("Missed", self.r.state()["slots"][0]["reason"])

    def test_startup_skips_due(self):
        self.active()
        self.due()
        self.a.startup()
        self.dispatch()
        self.assertFalse(self.u.store.all())

    def test_clock_rollback_never_replays(self):
        self.active()
        self.due()
        slot = self.f.f.now
        self.dispatch()
        self.f.f.now = slot - timedelta(hours=1)
        self.dispatch()
        self.f.f.now = slot
        self.dispatch()
        self.assertEqual(len(self.u.store.all()), 1)

    def test_changed_source_needs_attention(self):
        self.active()
        self.u.path.write_bytes(self.u.path.read_bytes() + b"changed")
        self.due()
        self.dispatch()
        self.assertFalse(self.u.store.all())
        self.assertEqual(self.r.state()["items"][0]["state"], "Needs Attention")

    def test_changed_review_skips_to_next_valid_item(self):
        second = self.extra()
        self.active([self.u.video.id, second.id])
        prep = self.u.preps.get(self.u.video.id)
        self.u.preps.save(self.u.video.id, replace(prep.metadata, title="Changed"), prep.revision)
        self.due()
        self.dispatch()
        self.assertEqual([a.video_id for a in self.u.store.all()], [second.id])

    def test_budget_retains_queue_item(self):
        second = self.extra()
        self.a.control(True, 1)
        self.active([self.u.video.id, second.id])
        self.due()
        self.dispatch()
        self.due()
        self.dispatch()
        self.assertEqual(len(self.u.store.all()), 1)
        self.assertEqual(self.r.state()["items"][1]["state"], "Queued")
        self.assertIn("budget", self.r.state()["slots"][0]["reason"])

    def test_started_item_cannot_reorder_or_remove(self):
        identity = self.active()
        self.due()
        self.dispatch()
        item = self.r.state()["items"][0]
        for action in ("up", "remove"):
            with self.assertRaises(ScheduleError):
                self.r.control(identity, 2, action, item["id"])

    def test_two_claimers_one_reservation(self):
        self.active()
        self.due()
        other = AutomaticPublisher(self.s, self.u.service)
        with ThreadPoolExecutor(2) as pool:
            ids = list(pool.map(lambda a: a.claim(), [self.a, other]))
        self.assertEqual(sum(i is not None for i in ids), 1)
        self.assertEqual(len(self.u.store.all()), 1)

    def test_claim_failure_rolls_back_slot_and_job(self):
        self.active()
        self.due()
        with patch(
            "shortspublisher.execution.upload_snapshot",
            side_effect=sqlite3.OperationalError("disk full"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.a.claim()
        self.assertFalse(self.u.store.all())
        self.assertFalse(self.s.all())
        self.assertFalse(self.r.state()["slots"])
        self.assertEqual(self.r.state()["items"][0]["state"], "Queued")

    def test_migration_preserves_schema9_data(self):
        before = self.u.library.all()
        with closing(self.u.library.connect()) as db, db:
            for table in (
                "automation_events",
                "automation_decisions",
                "automation_profiles",
                "recurring_events",
                "recurring_slots",
                "recurring_items",
                "recurring_plans",
            ):
                db.execute("DROP TABLE " + table)
            db.execute("PRAGMA user_version=9")
        self.u.library.initialize()
        self.assertEqual(self.u.library.all(), before)
        self.assertFalse(self.r.state()["plans"])
        self.assertTrue(self.r.state()["control"]["paused"])

    def test_migration_rollback(self):
        from shortspublisher import recurring_schema

        with closing(self.u.library.connect()) as db, db:
            for table in (
                "automation_events",
                "automation_decisions",
                "automation_profiles",
                "recurring_events",
                "recurring_slots",
                "recurring_items",
                "recurring_plans",
            ):
                db.execute("DROP TABLE " + table)
            db.execute("PRAGMA user_version=9")
        original = recurring_schema.migrate

        def fail(db):
            original(db)
            raise sqlite3.OperationalError("disk full")

        with (
            patch.object(recurring_schema, "migrate", side_effect=fail),
            self.assertRaises(sqlite3.OperationalError),
        ):
            self.u.library.initialize()
        with closing(self.u.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 9)

    def test_wrong_channel_rejected(self):
        with self.assertRaises(ScheduleError):
            self.r.preview([self.u.video.id], "wrong", self.rule, "Name", Event())

    def test_immutable_snapshot_after_metadata_change(self):
        self.approve()
        before = self.r.state()["items"][0]["snapshot"]
        prep = self.u.preps.get(self.u.video.id)
        self.u.preps.save(
            self.u.video.id, replace(prep.metadata, title="Later title"), prep.revision
        )
        self.assertEqual(self.r.state()["items"][0]["snapshot"], before)

    def test_slot_uniqueness(self):
        self.active()
        self.due()
        self.dispatch()
        with closing(self.u.library.connect()) as db, db:
            row = db.execute("SELECT * FROM recurring_slots").fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO recurring_slots VALUES(?,?,?,?,?,?,?)", tuple(row))

    def test_queue_does_not_raise_global_budget(self):
        self.approve()
        self.assertEqual(self.r.state()["control"]["daily_limit"], 3)
        self.assertEqual(self.r.state()["plans"][0]["approved_limit"], 3)

    def test_removed_item_may_be_explicitly_reapproved(self):
        identity = self.approve()
        item = self.r.state()["items"][0]
        self.r.control(identity, 1, "remove", item["id"])
        self.r.approve(
            self.proposal(plan_id=identity), Event(), consent=True, restrictions_resolved=True
        )
        self.assertEqual([i["state"] for i in self.r.state()["items"]], ["Removed", "Queued"])

    def test_approved_cap_does_not_expand_with_global_limit(self):
        second = self.extra()
        self.a.control(True, 1)
        self.active([self.u.video.id, second.id])
        self.a.control(False, 10)
        self.due()
        self.dispatch()
        self.due()
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.assertEqual(self.r.state()["items"][1]["state"], "Queued")

    def test_background_requires_separate_approval(self):
        self.active()
        self.due()
        with closing(self.u.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.r.reserve(db, "background")
        self.assertFalse(self.s.all())
        self.assertEqual(self.r.state()["items"][0]["state"], "Queued")
        self.assertIn("foreground-only", self.r.state()["slots"][0]["reason"].lower())

    def test_background_approved_slot_has_bound_consent(self):
        self.active(background=True)
        self.due()
        with closing(self.u.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self.r.reserve(db, "background")
            self.assertEqual(db.execute("SELECT count(*) FROM background_consent").fetchone()[0], 1)
        self.assertEqual(len(self.s.all()), 1)

    def test_pause_after_claim_blocks_new_session(self):
        plan = self.active()
        self.due()
        identity = self.a.claim()
        self.r.control(plan, 2, "pause")
        self.u.service.execute("scheduled", identity, Event(), lambda _: None)
        self.assertEqual(self.u.http.initiations, 0)

    def test_sixty_second_boundary_skips_without_consuming(self):
        self.active()
        self.due()
        self.f.f.now += timedelta(seconds=60)
        self.dispatch()
        self.assertFalse(self.u.store.all())
        self.assertEqual(self.r.state()["items"][0]["state"], "Queued")

    def test_fifty_nine_seconds_can_claim(self):
        self.active()
        self.due()
        self.f.f.now += timedelta(seconds=59)
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)

    def test_unavailable_source_never_posts(self):
        self.active()
        self.u.path.unlink()
        self.due()
        self.dispatch()
        self.assertFalse(self.u.store.all())
        self.assertEqual(self.r.state()["items"][0]["state"], "Needs Attention")

    def test_failed_transfer_is_not_requeued(self):
        from shortspublisher.upload_http import UploadReply

        self.active()
        self.u.http.initiation_reply = UploadReply(
            403, {"error": {"errors": [{"reason": "forbidden"}]}}
        )
        self.due()
        self.dispatch()
        self.due()
        self.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.assertEqual(len(self.u.store.all()), 1)
        self.assertEqual(self.r.state()["items"][0]["state"], "Reserved")

    def test_approval_transaction_failure_leaves_no_partial_plan(self):
        with patch.object(self.r, "event", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.approve()
        self.assertFalse(self.r.state()["plans"])
        self.assertFalse(self.r.state()["items"])

    def test_cancelled_confirmation_creates_nothing(self):
        cancellation = Event()
        cancellation.set()
        with self.assertRaises(AccountError):
            self.r.approve(self.proposal(), cancellation, consent=True, restrictions_resolved=True)
        self.assertFalse(self.r.state()["plans"])

    def test_cancellation_before_commit_rolls_back_entire_batch(self):
        cancellation = Event()
        original = self.r.event

        def cancel_after_event(*args):
            original(*args)
            cancellation.set()

        with patch.object(self.r, "event", side_effect=cancel_after_event):
            with self.assertRaises(Cancelled):
                self.r.approve(
                    self.proposal(), cancellation, consent=True, restrictions_resolved=True
                )
        self.assertFalse(self.r.state()["plans"])
        self.assertFalse(self.r.state()["items"])

    def test_new_library_arrival_does_not_join_approved_batch(self):
        self.approve()
        second = self.extra()
        self.assertNotIn(second.id, [i["video_id"] for i in self.r.state()["items"]])
        self.assertEqual(len(self.r.state()["items"]), 1)
