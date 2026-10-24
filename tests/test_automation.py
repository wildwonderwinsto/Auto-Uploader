"""Standing authorization is distinct from human review; real SQLite, FFprobe and uploader."""

import shutil
import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import patch

import test_recurring as fixtures
from account_support import CHANNEL
from support import make_video

from shortspublisher.automation import AutomationService, title_for
from shortspublisher.library import LibraryService
from shortspublisher.media import Cancelled, Inspector
from shortspublisher.preparation import PreparationStore
from shortspublisher.scheduling import ScheduleError
from shortspublisher.watching import WatchService


class AutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.RecurringTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.RecurringTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.RecurringTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.u, self.s, self.a = self.f.u, self.f.s, self.f.a
        self.service = AutomationService(self.s)
        self.root = self.u.root.resolve() / "watched"
        self.root.mkdir()
        self.scanner = WatchService(
            LibraryService(self.u.library, Inspector()), lambda: self.s.clock().timestamp()
        )
        self.folder = self.scanner.add(
            self.scanner.preview(str(self.root), Event()),
            consent=True,
            existing=False,
            quiet=1,
            background=True,
        )
        self.settings = dict(
            mode="automatic",
            title_template="{filename} #Shorts",
            description="User supplied",
            tags=["short"],
            made_for_kids=False,
            synthetic=False,
            rule=self.f.rule.encode(),
            limit=3,
            background=False,
            pattern="*.mp4",
            max_seconds=180,
            max_bytes=20 * 1024**3,
        )

    def proposal(self):
        return self.service.preview(self.folder, CHANNEL.id, "My automation", self.settings)

    def approve(self, **changes):
        self.settings.update(changes)
        return self.service.approve(
            self.proposal(), Event(), consent=True, declarations=True, restrictions=True
        )

    def arrival(self, name="First_title.mp4", duration=4):
        path = self.root / name
        make_video(path, duration=duration)
        return path

    def scan(self):
        self.scanner.scan(Event())
        self.f.f.f.now += timedelta(seconds=2)
        self.scanner.scan(Event())

    def state(self):
        return self.service.state()

    def due(self):
        self.f.f.f.now = datetime.fromisoformat(self.f.r.state()["plans"][0]["next_slot"])

    def test_default_migration_creates_no_authority(self):
        self.assertFalse(self.state()["profiles"])
        self.assertFalse(self.f.r.state()["plans"])

    def test_profile_approval_bound_scope(self):
        identity = self.approve()
        p = self.state()["profiles"][0]
        self.assertEqual(p["id"], identity)
        self.assertEqual(p["folder_binding"]["path"], str(self.root))
        self.assertEqual(p["channel_id"], CHANNEL.id)
        self.assertTrue(p["enabled"])
        self.assertTrue(self.a.status()["paused"])

    def test_all_consent_inputs_required(self):
        for missing in ("consent", "declarations", "restrictions"):
            flags = dict(consent=True, declarations=True, restrictions=True)
            flags[missing] = False
            with self.assertRaises(ScheduleError):
                self.service.approve(self.proposal(), Event(), **flags)
        self.assertFalse(self.state()["profiles"])

    def test_stable_file_enrolled_once_no_human_review(self):
        self.approve()
        self.arrival()
        self.scan()
        items = self.f.r.state()["items"]
        self.assertEqual(len(items), 1, self.state())
        prep = PreparationStore(self.u.library).get(items[0]["video_id"])
        self.assertIsNone(prep.reviewed_revision)
        self.assertFalse(prep.reviewed_at)
        self.assertEqual(prep.metadata.title, "First title #Shorts")
        self.assertIn("automation", items[0]["snapshot"])
        self.assertFalse(self.u.store.all())
        self.scan()
        self.assertEqual(len(self.f.r.state()["items"]), 1)

    def test_partial_file_waits(self):
        self.approve()
        path = self.arrival()
        self.scanner.scan(Event())
        path.write_bytes(path.read_bytes() + b"\0")
        self.f.f.f.now += timedelta(seconds=2)
        self.scanner.scan(Event())
        self.assertFalse(self.f.r.state()["items"])

    def test_review_mode_holds_then_enrolls_human_review(self):
        self.approve(mode="review")
        self.arrival()
        self.scan()
        self.assertFalse(self.f.r.state()["items"])
        decision = self.state()["decisions"][0]
        prep = PreparationStore(self.u.library).get(decision["video_id"])
        self.assertEqual(prep.metadata.title, "First title #Shorts")
        video = self.u.library.get(decision["video_id"])
        self.u.preps.complete(video.id, prep.revision, video.inspection_revision)
        self.scan()
        self.assertEqual(len(self.f.r.state()["items"]), 1, self.state())

    def test_two_slots_use_existing_real_uploader(self):
        self.approve()
        first = self.arrival()
        self.arrival("Second.mp4", 5)
        self.scan()
        self.assertEqual(len(self.f.r.state()["items"]), 2, self.state())
        self.a.control(False)
        self.due()
        self.f.dispatch()
        self.assertEqual(self.u.store.all()[0].state, "Public Verified")
        self.assertEqual(bytes(self.u.http.received), first.read_bytes())
        self.due()
        self.f.dispatch()
        self.assertEqual(len(self.u.store.all()), 2)
        self.assertTrue(all(a.state == "Public Verified" for a in self.u.store.all()))

    def test_duplicate_copy_skipped(self):
        self.approve()
        path = self.arrival()
        self.scan()
        shutil.copyfile(path, self.root / "renamed.mp4")
        self.scan()
        self.assertEqual(len(self.f.r.state()["items"]), 1)
        self.assertIn("Duplicate — skipped", [d["state"] for d in self.state()["decisions"]])

    def test_pause_blocks_new_enrollment(self):
        identity = self.approve()
        self.service.control(identity, 1, "pause")
        self.arrival()
        self.scan()
        self.assertFalse(self.f.r.state()["items"])
        self.service.control(identity, 1, "resume")
        self.scan()
        self.assertEqual(len(self.f.r.state()["items"]), 1)

    def test_disable_prevents_claim(self):
        identity = self.approve()
        self.arrival()
        self.scan()
        self.a.control(False)
        self.service.control(identity, 1, "disable")
        self.due()
        self.f.dispatch()
        self.assertEqual(self.u.http.initiations, 0)
        with self.assertRaises(ScheduleError):
            self.service.control(identity, 1, "resume")

    def test_revocation_after_claim_blocks_initiation(self):
        identity = self.approve()
        self.arrival()
        self.scan()
        self.a.control(False)
        self.due()
        claim = self.a.claim()
        self.assertIsNotNone(claim)
        self.service.control(identity, 1, "disable")
        self.u.service.execute("scheduled", claim, Event(), lambda _: None)
        self.assertEqual(self.u.http.initiations, 0)

    def test_manual_publish_still_requires_human_review(self):
        self.approve()
        self.arrival()
        self.scan()
        video = self.f.r.state()["items"][0]["video_id"]
        with self.assertRaises(Exception) as caught:
            self.u.service.reviewed(video)
        self.assertIn("review", str(caught.exception))

    def test_existing_files_excluded(self):
        self.arrival()
        self.approve()
        self.scan()
        self.assertFalse(self.f.r.state()["items"])
        self.assertEqual(self.state()["decisions"][0]["state"], "Excluded existing")

    def test_invalid_title_held_without_truncation(self):
        self.approve()
        self.arrival("a" * 110 + ".mp4")
        self.scan()
        self.assertFalse(self.f.r.state()["items"])
        self.assertIn("100", self.state()["decisions"][0]["reason"])

    def test_wrong_channel_rejected(self):
        with self.assertRaises(ScheduleError):
            self.service.preview(self.folder, "wrong", "Name", self.settings)

    def test_declarations_not_inferred(self):
        for field in ("made_for_kids", "synthetic"):
            old = self.settings[field]
            self.settings[field] = None
            with self.assertRaises(ScheduleError):
                self.proposal()
            self.settings[field] = old

    def test_metadata_rules(self):
        for key, value in (
            ("description", "x" * 5001),
            ("tags", ["x" * 501]),
            ("title_template", "{filename.__class__}"),
        ):
            old = self.settings[key]
            self.settings[key] = value
            with self.assertRaises(ScheduleError):
                self.proposal()
            self.settings[key] = old

    def test_filename_rules(self):
        self.assertEqual(
            title_for(" Café__雪.mp4", "Prefix {filename} #Shorts"), "Prefix Café 雪 #Shorts"
        )
        for name in ("___.mp4", "<bad>.mp4"):
            with self.assertRaises(ScheduleError):
                title_for(name, "{filename}")

    def test_source_mutation_blocks_upload(self):
        self.approve()
        path = self.arrival()
        self.scan()
        path.write_bytes(path.read_bytes() + b"changed")
        self.a.control(False)
        self.due()
        self.f.dispatch()
        self.assertFalse(self.u.store.all())

    def test_profile_edit_needs_new_consent_and_applies_to_waiting_videos(self):
        self.arrival("Old_file.mp4")  # present at authorization: never uploaded, even after edits
        identity = self.approve()
        self.arrival()
        self.scan()
        [queued] = [i for i in self.f.r.state()["items"] if i["state"] == "Queued"]
        plan = self.state()["profiles"][0]["plan_id"]
        self.settings["title_template"] = "New {filename}"
        with self.assertRaises(ScheduleError):  # an out-of-date edit is refused
            self.service.preview(
                self.folder, CHANNEL.id, "Edited", self.settings, identity=identity, revision=2
            )
        # No need to turn it off first; approving the edit replaces it in one step.
        proposal = self.service.preview(
            self.folder, CHANNEL.id, "Edited", self.settings, identity=identity, revision=1
        )
        self.service.approve(proposal, Event(), consent=True, declarations=True, restrictions=True)
        profile = self.state()["profiles"][0]
        self.assertEqual((profile["revision"], profile["plan_id"]), (2, plan))
        self.assertEqual(len(self.f.r.state()["plans"]), 1)  # the same plan, new times
        old = next(i for i in self.f.r.state()["items"] if i["id"] == queued["id"])
        self.assertEqual(old["state"], "Removed")
        self.scan()
        items = [i for i in self.f.r.state()["items"] if i["state"] == "Queued"]
        self.assertEqual([i["video_id"] for i in items], [queued["video_id"]])
        self.assertEqual(items[0]["snapshot"]["metadata"]["title"], "New First title")
        prep = PreparationStore(self.u.library).get(queued["video_id"])
        self.assertEqual(prep.metadata.title, "New First title")

    def test_edit_keeps_details_written_by_hand(self):
        identity = self.approve()
        self.arrival()
        self.scan()
        [queued] = self.f.r.state()["items"]
        store = PreparationStore(self.u.library)
        prep = store.get(queued["video_id"])
        store.save(prep.video_id, replace(prep.metadata, title="My own title"), prep.revision)
        self.settings["title_template"] = "New {filename}"
        self.service.approve(
            self.service.preview(
                self.folder, CHANNEL.id, "Edited", self.settings, identity=identity, revision=1
            ),
            Event(),
            consent=True,
            declarations=True,
            restrictions=True,
        )
        self.scan()
        self.assertEqual(store.get(queued["video_id"]).metadata.title, "My own title")
        self.assertFalse([i for i in self.f.r.state()["items"] if i["state"] == "Queued"])
        decisions = [d for d in self.state()["decisions"] if d["profile_revision"] == 2]
        self.assertEqual(
            [d["state"] for d in decisions if d["name"] == "First_title.mp4"], ["Needs review"]
        )

    def test_profile_approval_rollback(self):
        with patch.object(self.service, "event", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.approve()
        self.assertFalse(self.state()["profiles"])
        self.assertFalse(self.f.r.state()["plans"])

    def test_cancel_approval(self):
        cancel = Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            self.service.approve(
                self.proposal(), cancel, consent=True, declarations=True, restrictions=True
            )
        self.assertFalse(self.state()["profiles"])

    def test_background_permission_separate(self):
        self.approve()
        self.arrival()
        self.scanner.scan(Event(), background=True)
        self.f.f.f.now += timedelta(seconds=2)
        self.scanner.scan(Event(), background=True)
        self.assertFalse(self.f.r.state()["items"])

    def test_missing_folder_blocks_approval(self):
        self.root.rmdir()
        with self.assertRaises(OSError):
            self.proposal()

    def test_identical_generated_titles_hold_second(self):
        self.approve(title_template="Same title")
        self.arrival()
        self.arrival("Second.mp4", 5)
        self.scan()
        self.assertEqual(len(self.f.r.state()["items"]), 1)
        self.assertTrue(any("ambig" in d["reason"] for d in self.state()["decisions"]))

    def test_changed_metadata_never_overwritten_or_published(self):
        self.approve()
        self.arrival()
        self.scan()
        video = self.f.r.state()["items"][0]["video_id"]
        prep = self.u.preps.get(video)
        self.u.preps.save(video, replace(prep.metadata, title="Manual edit"), prep.revision)
        self.a.control(False)
        self.due()
        self.f.dispatch()
        self.assertFalse(self.u.store.all())
        self.assertEqual(self.u.preps.get(video).metadata.title, "Manual edit")

    def test_migration_preserves_finite_queue(self):
        from contextlib import closing

        self.f.approve()
        before = self.f.r.state()
        with closing(self.u.library.connect()) as db, db:
            for table in ("automation_events", "automation_decisions", "automation_profiles"):
                db.execute("DROP TABLE " + table)
            db.execute("PRAGMA user_version=10")
        self.u.library.initialize()
        self.assertEqual(self.f.r.state(), before)
        self.assertFalse(self.state()["profiles"])

    def test_migration_rollback(self):
        from contextlib import closing

        from shortspublisher import automation_schema

        with closing(self.u.library.connect()) as db, db:
            for table in ("automation_events", "automation_decisions", "automation_profiles"):
                db.execute("DROP TABLE " + table)
            db.execute("PRAGMA user_version=10")
        original = automation_schema.migrate

        def fail(db):
            original(db)
            raise sqlite3.OperationalError("disk full")

        with patch.object(automation_schema, "migrate", side_effect=fail):
            with self.assertRaises(sqlite3.OperationalError):
                self.u.library.initialize()
        with closing(self.u.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 10)
            self.assertIsNone(
                db.execute(
                    "SELECT name FROM sqlite_master WHERE name='automation_profiles'"
                ).fetchone()
            )
        self.u.library.initialize()

    def test_concurrent_enrollment_one_item(self):
        from concurrent.futures import ThreadPoolExecutor

        self.approve()
        self.arrival()
        # Import using the same scanner, temporarily suppressing only profile reconciliation.
        with patch.object(AutomationService, "reconcile"):
            self.scan()
        profile = self.state()["profiles"][0]
        file = self.scanner.files(self.folder)[0]
        with ThreadPoolExecutor(2) as pool:
            futures = [
                pool.submit(
                    self.service.enroll, profile, file, self.scanner, Event(), background=False
                )
                for _ in range(2)
            ]
            for future in futures:
                future.result()
        self.assertEqual(len(self.f.r.state()["items"]), 1)

    def test_second_profile_same_content_never_competes(self):
        self.approve()
        self.approve()
        self.arrival()
        self.scan()
        self.assertEqual(len(self.f.r.state()["items"]), 1)

    def test_previous_manual_publication_blocks_copy(self):
        self.f.active()
        self.f.due()
        self.f.dispatch()
        self.approve()
        shutil.copyfile(self.u.path, self.root / "copy.mp4")
        self.scan()
        self.assertEqual(len(self.f.r.state()["items"]), 1)  # original finite item only
        self.assertIn("Duplicate — skipped", [d["state"] for d in self.state()["decisions"]])

    def test_background_approved_enrollment(self):
        self.approve(background=True)
        self.arrival()
        self.scanner.scan(Event(), background=True)
        self.f.f.f.now += timedelta(seconds=2)
        self.scanner.scan(Event(), background=True)
        self.assertEqual(len(self.f.r.state()["items"]), 1)

    def test_pattern_exclusion(self):
        self.approve(pattern="approved_*.mp4")
        self.arrival()
        self.scan()
        self.assertFalse(self.f.r.state()["items"])
        self.assertIn("pattern", self.state()["decisions"][0]["reason"])

    def test_duration_restriction(self):
        self.approve(max_seconds=2)
        self.arrival()
        self.scan()
        self.assertFalse(self.f.r.state()["items"])

    def test_size_restriction(self):
        self.approve(max_bytes=1)
        self.arrival()
        self.scan()
        self.assertFalse(self.f.r.state()["items"])

    def test_symlink_never_enrolls(self):
        self.approve()
        (self.root / "link.mp4").symlink_to(self.u.path)
        self.scan()
        self.assertFalse(self.f.r.state()["items"])

    def test_replaced_folder_blocks_queued_item(self):
        self.approve()
        self.arrival()
        self.scan()
        self.root.rename(self.root.with_name("old-folder"))
        self.root.mkdir()
        self.a.control(False)
        self.due()
        self.f.dispatch()
        self.assertFalse(self.u.store.all())

    def test_invalid_media_held(self):
        self.approve()
        (self.root / "broken.mp4").write_bytes(b"not a video")
        self.scan()
        self.assertFalse(self.f.r.state()["items"])
        self.assertEqual(self.state()["decisions"][0]["state"], "Needs review")

    def test_remove_keeps_queue_and_history(self):
        identity = self.approve()
        self.arrival()
        self.scan()
        self.service.control(identity, 1, "remove")
        self.assertTrue(self.state()["profiles"][0]["removed"])
        # Kept as history, but nothing is left waiting to upload.
        self.assertEqual([i["state"] for i in self.f.r.state()["items"]], ["Removed"])
        self.assertTrue(self.root.exists())

    def test_global_pause_retains_queue(self):
        self.approve()
        self.arrival()
        self.scan()
        self.due()
        self.f.dispatch()
        self.assertFalse(self.u.store.all())
        self.assertEqual(self.f.r.state()["items"][0]["state"], "Queued")

    def test_missed_opportunities_do_not_burst(self):
        self.approve()
        self.arrival()
        self.scan()
        self.a.control(False)
        self.due()
        self.f.f.f.now += timedelta(days=10)
        self.f.dispatch()
        self.assertFalse(self.u.store.all())
        self.assertEqual(self.f.r.state()["items"][0]["state"], "Queued")

    def test_restart_never_reposts(self):
        self.approve()
        self.arrival()
        self.scan()
        self.a.control(False)
        self.due()
        self.f.dispatch()
        self.a.startup()
        self.scan()
        self.due()
        self.f.dispatch()
        self.assertEqual(self.u.http.initiations, 1)

    def test_source_changed_during_enrollment(self):
        self.approve()
        path = self.arrival()
        original = self.scanner.hash_file

        def changed(*args):
            digest = original(*args)
            path.write_bytes(path.read_bytes() + b"changed")
            return digest

        with patch.object(self.scanner, "hash_file", side_effect=changed):
            self.scan()
        self.assertFalse(self.f.r.state()["items"])

    def test_profile_revoked_during_hash(self):
        identity = self.approve()
        self.arrival()
        original = self.scanner.hash_file

        def revoke(*args):
            digest = original(*args)
            self.service.control(identity, 1, "disable")
            return digest

        with patch.object(self.scanner, "hash_file", side_effect=revoke):
            self.scan()
        self.assertFalse(self.f.r.state()["items"])

    def test_enrollment_disk_error_rolls_back(self):
        self.approve()
        self.arrival()
        with patch.object(
            AutomationService, "event", side_effect=sqlite3.OperationalError("disk full")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.scan()
        self.assertFalse(self.f.r.state()["items"])
        self.scan()
        self.assertEqual(len(self.f.r.state()["items"]), 1)

    def test_unknown_remote_outcome_is_retained(self):
        from shortspublisher.upload_http import UploadReply

        self.approve()
        self.arrival()
        self.scan()
        self.a.control(False)
        self.u.http.initiation_reply = UploadReply(0)
        self.due()
        self.f.dispatch()
        self.assertEqual(self.u.store.all()[0].state, "Needs Reconciliation")
        self.a.startup()
        self.due()
        self.f.dispatch()
        self.assertEqual(self.u.http.initiations, 1)

    def test_renamed_existing_source_does_not_gain_future_permission(self):
        path = self.arrival()
        self.approve()
        path.rename(self.root / "Renamed_after_consent.mp4")
        self.scan()
        self.assertFalse(self.f.r.state()["items"])
        self.assertTrue(
            any("existed at authorization" in d["reason"] for d in self.state()["decisions"])
        )

    def test_waiting_review_avoids_hash_after_service_restart(self):
        self.approve(mode="review")
        self.arrival()
        self.scan()
        restarted = AutomationService(self.s)
        with patch.object(self.scanner, "hash_file", wraps=self.scanner.hash_file) as hashes:
            restarted.reconcile(self.scanner, Event(), background=False)
        self.assertEqual(hashes.call_count, 0)
        self.assertFalse(self.f.r.state()["items"])
        self.assertFalse(self.u.store.all())

    def test_review_completion_always_rehashes_before_enrollment(self):
        self.approve(mode="review")
        self.arrival()
        self.scan()
        d = self.state()["decisions"][0]
        video = self.u.library.get(d["video_id"])
        prep = PreparationStore(self.u.library).get(video.id)
        self.u.preps.complete(video.id, prep.revision, video.inspection_revision)
        with patch.object(self.scanner, "hash_file", return_value="changed") as hashes:
            self.service.reconcile(self.scanner, Event(), background=False)
        self.assertEqual(hashes.call_count, 1)
        self.assertFalse(self.f.r.state()["items"])
        self.assertIn("fingerprint changed", self.state()["decisions"][0]["reason"])

    def test_waiting_review_changed_source_cannot_use_hold_shortcut(self):
        self.approve(mode="review")
        path = self.arrival()
        self.scan()
        path.write_bytes(path.read_bytes() + b"changed")
        with patch.object(self.scanner, "hash_file", wraps=self.scanner.hash_file) as hashes:
            self.service.reconcile(self.scanner, Event(), background=False)
        self.assertEqual(hashes.call_count, 1)
        self.assertFalse(self.f.r.state()["items"])
        self.assertNotEqual(self.state()["decisions"][0]["reason"], "Individual review required")

    def test_waiting_review_metadata_edit_invalidates_hold_shortcut(self):
        self.approve(mode="review")
        self.arrival()
        self.scan()
        d = self.state()["decisions"][0]
        prep = PreparationStore(self.u.library).get(d["video_id"])
        self.u.preps.save(
            d["video_id"], replace(prep.metadata, title="Human changes"), prep.revision
        )
        with patch.object(self.scanner, "hash_file", wraps=self.scanner.hash_file) as hashes:
            self.service.reconcile(self.scanner, Event(), background=False)
        self.assertEqual(hashes.call_count, 1)
        self.assertFalse(self.f.r.state()["items"])

    def test_concurrent_waiting_review_never_authorizes_or_hashes(self):
        from concurrent.futures import ThreadPoolExecutor

        self.approve(mode="review")
        self.arrival()
        self.scan()
        with patch.object(self.scanner, "hash_file", wraps=self.scanner.hash_file) as hashes:
            with ThreadPoolExecutor(max_workers=2) as executor:
                jobs = [
                    executor.submit(
                        AutomationService(self.s).reconcile, self.scanner, Event(), background=False
                    )
                    for _ in range(2)
                ]
                for job in jobs:
                    job.result()
        self.assertEqual(hashes.call_count, 0)
        self.assertFalse(self.f.r.state()["items"])

    def test_exact_filename_scope_bounds_disposable_acceptance(self):
        self.approve(pattern="Unique_acceptance.mp4", limit=1)
        original = self.arrival("Unique_acceptance.mp4")
        self.arrival("Unrelated.mp4", 5)
        self.scan()
        self.assertEqual(len(self.f.r.state()["items"]), 1)
        self.a.control(False)
        self.due()
        self.f.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        shutil.copy2(original, self.root / "Renamed_copy.mp4")
        self.scan()
        self.a.startup()
        self.due()
        self.f.dispatch()
        self.assertEqual(self.u.http.initiations, 1)
        self.assertEqual(len(self.f.r.state()["items"]), 1)
