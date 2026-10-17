"""Real media/SQLite discovery; deterministic time, no uploader or credential access."""

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Event
from unittest.mock import patch

from support import make_video

from shortspublisher.library import Library, LibraryService
from shortspublisher.media import Inspector
from shortspublisher.watching import WatchError, WatchService


class WatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp_media.cleanup)
        cls.source = Path(cls.temp_media.name) / "source.mp4"
        make_video(cls.source)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root / "watch"
        self.folder.mkdir()
        self.library = Library(self.root / "data/library.sqlite3")
        self.library.initialize()
        self.imports = LibraryService(self.library, Inspector())
        self.tick = 100.0
        self.service = WatchService(self.imports, lambda: self.tick)
        self.cancel = Event()

    def add(self, existing=True, background=False, folder=None):
        proposal = self.service.preview(str(folder or self.folder), self.cancel)
        return self.service.add(proposal, consent=True, existing=existing, background=background)

    def copy(self, name="one.mp4", folder=None):
        path = (folder or self.folder) / name
        shutil.copyfile(self.source, path)
        return path

    def scan(self, elapsed=0, background=False):
        self.tick += elapsed
        return self.service.scan(self.cancel, background=background)

    def finish(self):
        self.scan()
        self.scan(16)

    def test_consent_required(self):
        with self.assertRaises(WatchError):
            self.service.add(
                self.service.preview(str(self.folder), self.cancel), consent=False, existing=True
            )
        self.assertFalse(self.service.folders())

    def test_add_persists_options(self):
        identity = self.add(background=True)
        row = WatchService(self.imports).folders()[0]
        self.assertEqual(row["id"], identity)
        self.assertEqual(row["path"], str(self.folder))
        self.assertTrue(row["consent_at"])
        self.assertEqual(row["background"], 1)

    def test_two_observations_and_quiet_period(self):
        self.add()
        self.copy()
        self.scan()
        self.assertFalse(self.library.all())
        self.scan(14)
        self.assertFalse(self.library.all())
        self.scan(1)
        self.assertEqual(len(self.library.all()), 1)

    def test_slow_copy_resets_quiet_period(self):
        self.add()
        p = self.folder / "slow.mp4"
        raw = self.source.read_bytes()
        p.write_bytes(raw[:100])
        self.scan()
        p.write_bytes(raw[:1000])
        self.scan(20)
        self.assertFalse(self.library.all())
        p.write_bytes(raw)
        self.scan(20)
        self.assertFalse(self.library.all())
        self.scan(16)
        self.assertEqual(len(self.library.all()), 1)

    def test_zero_length_waits(self):
        identity = self.add()
        (self.folder / "empty.mp4").touch()
        self.finish()
        self.assertFalse(self.library.all())
        self.assertEqual(self.service.files(identity)[0]["state"], "Waiting for file to finish")

    def test_temporary_and_unknown_extensions_ignored(self):
        self.add()
        self.copy("video.mp4.part")
        self.copy("video.txt")
        self.copy(".hidden.mp4")
        self.finish()
        self.assertFalse(self.library.all())

    def test_nonrecursive(self):
        self.add()
        child = self.folder / "child"
        child.mkdir()
        self.copy(folder=child)
        self.finish()
        self.assertFalse(self.library.all())

    def test_symlink_file_not_followed(self):
        identity = self.add()
        (self.folder / "link.mp4").symlink_to(self.source)
        self.finish()
        self.assertFalse(self.library.all())
        self.assertIn("symbolic", self.service.files(identity)[0]["message"])

    def test_symlink_folder_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.folder)
        with self.assertRaises(WatchError):
            self.service.preview(str(alias), self.cancel)

    def test_invalid_folder_rejected(self):
        with self.assertRaises(OSError):
            self.service.preview(str(self.root / "absent"), self.cancel)
        with self.assertRaises(WatchError):
            self.service.preview("/", self.cancel)

    def test_initial_new_only_persists(self):
        self.copy()
        identity = self.add(existing=False)
        self.finish()
        self.service = WatchService(self.imports, lambda: self.tick)
        self.scan(100)
        self.assertFalse(self.library.all())
        self.assertEqual(self.service.files(identity)[0]["state"], "Ignored existing file")

    def test_ignored_rename_remains_ignored(self):
        p = self.copy()
        self.add(existing=False)
        p.rename(self.folder / "renamed.mp4")
        self.finish()
        self.assertFalse(self.library.all())

    def test_new_after_initial_exclusion(self):
        self.copy()
        self.add(existing=False)
        self.copy("new.mp4")
        self.finish()
        self.assertEqual(len(self.library.all()), 1)

    def test_pause_resume(self):
        identity = self.add()
        self.service.control(identity, 1, "pause")
        self.copy()
        self.finish()
        self.assertFalse(self.library.all())
        self.service.control(identity, 2, "resume")
        self.finish()
        self.assertEqual(len(self.library.all()), 1)

    def test_stale_control_rejected(self):
        identity = self.add()
        self.service.control(identity, 1, "pause")
        with self.assertRaises(WatchError):
            self.service.control(identity, 1, "resume")

    def test_remove_readd_preserves_registry_sources(self):
        identity = self.add()
        p = self.copy()
        before = hashlib.sha256(p.read_bytes()).hexdigest()
        self.finish()
        self.service.control(identity, 1, "remove")
        self.assertFalse(self.service.folders())
        self.add()
        self.copy("copy.mp4")
        self.finish()
        self.assertEqual(len(self.library.all()), 1)
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(), before)

    def test_repeated_scan_does_not_rehash(self):
        self.add()
        self.copy()
        self.finish()
        with patch.object(self.service, "hash_file", side_effect=AssertionError("rehash")):
            self.scan(20)

    def test_same_bytes_other_name(self):
        identity = self.add()
        self.copy()
        self.copy("two.mp4")
        self.finish()
        self.assertEqual(len(self.library.all()), 1)
        self.assertEqual(
            {r["state"] for r in self.service.files(identity)},
            {"Imported successfully", "Duplicate content"},
        )

    def test_other_folder_dedup(self):
        self.add()
        second = self.root / "second"
        second.mkdir()
        self.add(folder=second)
        self.copy()
        self.copy(folder=second)
        self.finish()
        self.assertEqual(len(self.library.all()), 1)

    def test_existing_manual_library(self):
        path = self.copy()
        video = self.imports.execute("import", str(path), self.cancel)["selected"]
        self.add()
        self.copy("copy.mp4")
        self.finish()
        self.assertEqual([v.id for v in self.library.all()], [video.id])

    def test_legacy_duplicate_ids_preserved(self):
        p = self.copy()
        q = self.copy("two.mp4")
        self.imports.execute("import", str(p), self.cancel)
        self.imports.execute("import", str(q), self.cancel)
        before = self.library.all()
        self.add()
        self.copy("third.mp4")
        self.finish()
        self.assertEqual(self.library.all(), before)

    def test_rename_after_import(self):
        identity = self.add()
        p = self.copy()
        self.finish()
        p.rename(self.folder / "renamed.mp4")
        self.finish()
        self.assertEqual(len(self.library.all()), 1)
        self.assertIn("Duplicate content", {r["state"] for r in self.service.files(identity)})

    def test_invalid_media_retries_after_change(self):
        identity = self.add()
        p = self.folder / "bad.mp4"
        p.write_bytes(b"not a video")
        self.finish()
        self.assertEqual(self.service.files(identity)[0]["state"], "Unsupported or invalid video")
        shutil.copyfile(self.source, p)
        self.finish()
        self.assertEqual(len(self.library.all()), 1)

    def test_unchanged_failure_backoff(self):
        identity = self.add()
        p = self.folder / "bad.mp4"
        p.write_bytes(b"not a video")
        self.finish()
        failures = self.service.files(identity)[0]["failures"]
        self.scan(1)
        self.assertEqual(self.service.files(identity)[0]["failures"], failures)

    def test_folder_disappearance_and_return(self):
        self.add()
        moved = self.root / "moved"
        self.folder.rename(moved)
        self.scan()
        self.assertTrue(self.service.folders()[0]["error"])
        moved.rename(self.folder)
        self.copy()
        self.finish()
        self.assertFalse(self.service.folders()[0]["error"])
        self.assertEqual(len(self.library.all()), 1)

    def test_replacement_folder_not_implicitly_authorized(self):
        self.add()
        self.folder.rename(self.root / "old")
        self.folder.mkdir()
        self.copy()
        self.finish()
        self.assertFalse(self.library.all())
        self.assertIn("identity changed", self.service.folders()[0]["error"])

    def test_hash_change_blocks_commit(self):
        self.add()
        p = self.copy()
        self.scan()
        original = self.service.hash_file

        def change(*args):
            value = original(*args)
            p.write_bytes(p.read_bytes() + b"changed")
            return value

        with patch.object(self.service, "hash_file", side_effect=change):
            self.scan(16)
        self.assertFalse(self.library.all())

    def test_pause_during_inspection_blocks_commit(self):
        identity = self.add()
        self.copy()
        self.scan()
        original = self.imports.inspector.inspect

        def pause(*args):
            media = original(*args)
            self.service.control(identity, 1, "pause")
            return media

        with patch.object(self.imports.inspector, "inspect", side_effect=pause):
            self.scan(16)
        self.assertFalse(self.library.all())

    def test_cancel_before_scan(self):
        from shortspublisher.media import Cancelled

        self.add()
        self.copy()
        self.cancel.set()
        with self.assertRaises(Cancelled):
            self.scan()
        self.assertFalse(self.library.all())

    def test_concurrent_scanners(self):
        self.add()
        self.copy()
        self.scan()
        self.tick += 16
        other = WatchService(self.imports, lambda: self.tick)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda s: s.scan(Event()), [self.service, other]))
        self.assertEqual(sum(r["imported"] for r in results), 1)
        self.assertEqual(len(self.library.all()), 1)

    def test_background_requires_folder_permission(self):
        self.add()
        self.copy()
        self.scan(background=True)
        self.scan(16, background=True)
        self.assertFalse(self.library.all())

    def test_background_authorized_local_import(self):
        self.add(background=True)
        self.copy()
        self.scan(background=True)
        self.scan(16, background=True)
        self.assertEqual(len(self.library.all()), 1)
        with closing(self.library.connect()) as db:
            for table in ("upload_attempts", "scheduled_jobs", "preparations"):
                self.assertEqual(db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)

    def test_registry_uniqueness(self):
        self.add()
        self.copy()
        self.finish()
        with closing(self.library.connect()) as db, db:
            row = db.execute("SELECT * FROM content_registry").fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO content_registry VALUES(?,?,?)", tuple(row))

    def test_historical_channel_evidence(self):
        from shortspublisher.upload_store import UploadStore

        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        store = UploadStore(self.library)
        for channel in ("first", "second"):
            attempt = store.create(
                123,
                channel,
                {
                    "sha256": digest,
                    "requested_privacy": "public",
                    "size": self.source.stat().st_size,
                },
            )
            store.update(
                attempt.id, "Public Verified", remote_id="controlled", processing="processed"
            )
        evidence = self.service.evidence(digest)
        self.assertEqual({r["channel"] for r in evidence}, {"first", "second"})
        self.add()
        self.copy()
        self.finish()
        self.assertFalse(self.library.all())

    def test_migration_preserves_v8_rows(self):
        with closing(self.library.connect()) as db, db:
            for table in (
                "automation_events",
                "automation_decisions",
                "automation_profiles",
                "recurring_events",
                "recurring_slots",
                "recurring_items",
                "recurring_plans",
                "watch_files",
                "watch_folders",
                "content_videos",
                "content_registry",
            ):
                db.execute("DROP TABLE " + table)
            db.execute("PRAGMA user_version=8")
        p = self.copy()
        self.library.save(str(p), Inspector().inspect(p, self.cancel))
        before = self.library.all()
        self.library.initialize()
        self.assertEqual(self.library.all(), before)
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 11)

    def test_new_only_readd_excludes_previously_waiting(self):
        identity = self.add()
        self.copy()
        self.scan()
        self.service.control(identity, 1, "remove")
        self.add(existing=False)
        self.finish()
        self.assertFalse(self.library.all())

    def test_failed_commit_rolls_back_and_can_retry(self):
        self.add()
        self.copy()
        self.scan()
        original = self.library.save_in_transaction

        def fail(*args):
            original(*args)
            raise sqlite3.OperationalError("database or disk is full")

        with patch.object(self.library, "save_in_transaction", side_effect=fail):
            with self.assertRaises(sqlite3.OperationalError):
                self.scan(16)
        self.assertFalse(self.library.all())
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM content_registry").fetchone()[0], 0)
        self.scan(16)
        self.assertEqual(len(self.library.all()), 1)

    def test_migration_failure_rolls_back(self):
        import shortspublisher.watch_schema as schema

        with closing(self.library.connect()) as db, db:
            for table in (
                "automation_events",
                "automation_decisions",
                "automation_profiles",
                "recurring_events",
                "recurring_slots",
                "recurring_items",
                "recurring_plans",
                "watch_files",
                "watch_folders",
                "content_videos",
                "content_registry",
            ):
                db.execute("DROP TABLE " + table)
            db.execute("PRAGMA user_version=8")
        original = schema.migrate

        def fail(db):
            original(db)
            raise sqlite3.OperationalError("disk full")

        with patch.object(schema, "migrate", side_effect=fail):
            with self.assertRaises(sqlite3.OperationalError):
                self.library.initialize()
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
            self.assertIsNone(
                db.execute("SELECT name FROM sqlite_master WHERE name='watch_folders'").fetchone()
            )
        self.library.initialize()

    def test_cross_process_concurrent_import(self):
        import subprocess
        import sys

        self.add()
        self.copy()
        self.scan()
        script = """import sys
from threading import Event
from pathlib import Path
from shortspublisher.library import Library, LibraryService
from shortspublisher.media import Inspector
from shortspublisher.watching import WatchService
s=WatchService(LibraryService(Library(Path(sys.argv[1])),Inspector()),lambda:120)
s.scan(Event())
"""
        workers = [
            subprocess.Popen([sys.executable, "-c", script, str(self.library.path)])
            for _ in range(2)
        ]
        for worker in workers:
            self.assertEqual(worker.wait(timeout=15), 0)
        self.assertEqual(len(self.library.all()), 1)
        self.scan(30)
        self.assertEqual(len(self.library.all()), 1)

    def test_uncertain_history_blocks_import(self):
        from shortspublisher.upload_store import UploadStore

        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        store = UploadStore(self.library)
        attempt = store.create(
            123,
            "uncertain-channel",
            {"sha256": digest, "requested_privacy": "private", "size": self.source.stat().st_size},
        )
        store.update(attempt.id, "Needs Reconciliation")
        self.add()
        self.copy()
        self.finish()
        self.assertFalse(self.library.all())
        self.assertEqual(self.service.evidence(digest)[0]["state"], "Needs Reconciliation")

    def test_missing_file_during_inspection(self):
        self.add()
        path = self.copy()
        self.scan()
        original = self.imports.inspector.inspect

        def remove(*args):
            media = original(*args)
            path.unlink()
            return media

        with patch.object(self.imports.inspector, "inspect", side_effect=remove):
            self.scan(16)
        self.assertFalse(self.library.all())
        self.copy()
        self.finish()
        self.assertEqual(len(self.library.all()), 1)

    def test_scheduled_content_is_channel_specific_evidence(self):
        import test_scheduling as fixtures

        fixtures.SchedulingTests.setUpClass()
        fixture = fixtures.SchedulingTests()
        fixture.setUp()
        try:
            job = fixture.approve()
            service = WatchService(LibraryService(fixture.f.library, Inspector()))
            records = service.evidence(job.source_hash)
            self.assertTrue(
                any(
                    r["kind"] == "plan"
                    and r["id"] == job.id
                    and r["channel"] == job.channel_id
                    and r["blocked"]
                    for r in records
                )
            )
            folder = Path(fixture.f.root).resolve() / "watched"
            folder.mkdir()
            shutil.copyfile(fixture.f.path, folder / "copy.mp4")
            service.add(service.preview(str(folder), Event()), consent=True, existing=True, quiet=1)
            service.clock = lambda: 100
            service.scan(Event())
            service.clock = lambda: 102
            service.scan(Event())
            self.assertEqual(len(fixture.f.library.all()), 1)
            self.assertEqual(fixture.s.get(job.id), job)
            self.assertEqual(fixture.f.http.initiations, 0)
        finally:
            fixture.doCleanups()
            fixtures.SchedulingTests.tearDownClass()

    def test_permission_failure_retries_safely(self):
        identity = self.add()
        self.copy()
        self.scan()
        with patch.object(
            self.service, "hash_file", side_effect=PermissionError("Permission denied")
        ):
            self.scan(16)
        self.assertFalse(self.library.all())
        self.assertIn("Permission denied", self.service.files(identity)[0]["message"])
        self.scan(31)
        self.assertEqual(len(self.library.all()), 1)

    def test_cancel_during_hash_no_commit(self):
        from shortspublisher.media import Cancelled

        self.add()
        self.copy()
        self.scan()
        original = self.service.hash_file

        def cancel(*args):
            result = original(*args)
            self.cancel.set()
            return result

        with patch.object(self.service, "hash_file", side_effect=cancel):
            with self.assertRaises(Cancelled):
                self.scan(16)
        self.assertFalse(self.library.all())

    def test_readd_can_explicitly_include_ignored_files(self):
        self.copy()
        identity = self.add(existing=False)
        self.finish()
        self.service.control(identity, 1, "remove")
        self.add(existing=True)
        self.finish()
        self.assertEqual(len(self.library.all()), 1)
