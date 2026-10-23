import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event

from support import make_video

from shortspublisher.library import (
    SCHEMA_VERSION,
    Library,
    LibraryError,
    LibraryService,
    source_status,
)
from shortspublisher.media import Inspector
from shortspublisher.preparation import (
    PreparationStore,
    YouTubeMetadata,
    metadata_errors,
    review_checks,
    tag_length,
    unique_tags,
)

VALID = YouTubeMetadata(
    "A real Short",
    "Description\n雪 🎬\nhttps://example.com",
    ("short video", "雪"),
    False,
    False,
    "private",
)


class MetadataRulesTests(unittest.TestCase):
    def test_title_limits_and_empty(self):
        self.assertFalse(metadata_errors(replace(VALID, title="雪" * 100)))
        self.assertIn("100 characters", str(metadata_errors(replace(VALID, title="x" * 101))))
        for title in ("", "  \t"):
            self.assertIn("Enter a title", str(metadata_errors(replace(VALID, title=title))))

    def test_description_bytes_and_unicode(self):
        self.assertFalse(metadata_errors(replace(VALID, description="🎬" * 1250)))
        self.assertIn("5,000", str(metadata_errors(replace(VALID, description="🎬" * 1250 + "a"))))
        self.assertFalse(metadata_errors(replace(VALID, description="")))

    def test_angle_brackets_are_prohibited(self):
        for field in ("title", "description"):
            self.assertIn(
                "cannot contain", str(metadata_errors(replace(VALID, **{field: "<value>"})))
            )

    def test_tag_counting_examples_boundaries(self):
        self.assertEqual(tag_length(("Foo-Baz",)), 7)
        self.assertEqual(tag_length(("Foo Baz",)), 9)
        self.assertEqual(tag_length(("Foo Baz", "雪")), 11)
        self.assertEqual(tag_length(()), 0)
        self.assertFalse(metadata_errors(replace(VALID, tags=("x" * 500,))))
        self.assertIn("Tags exceed", str(metadata_errors(replace(VALID, tags=("x" * 500, "y")))))

    def test_duplicate_normalization(self):
        self.assertEqual(
            unique_tags((" Tag ", "tag", "é", "e\u0301", "雪", "")), ("Tag", "é", "雪")
        )
        self.assertIn("duplicate", str(metadata_errors(replace(VALID, tags=("same", "SAME")))))

    def test_declarations_must_be_explicit(self):
        self.assertIn("Made for Kids", str(metadata_errors(replace(VALID, made_for_kids=None))))
        self.assertIn("disclosure", str(metadata_errors(replace(VALID, synthetic=None))))
        self.assertFalse(metadata_errors(replace(VALID, made_for_kids=True, synthetic=True)))

    def test_privacy_validation(self):
        for privacy in ("private", "unlisted", "public"):
            self.assertFalse(metadata_errors(replace(VALID, privacy=privacy)))
        self.assertIn("future upload", str(metadata_errors(replace(VALID, privacy="invalid"))))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "library.sqlite3"
        shutil.copyfile(Path(__file__).parent / "fixtures/library-v1.sqlite3", self.path)

    def test_genuine_version_one_preserved(self):
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            original = db.execute("SELECT * FROM videos").fetchall()
        library = Library(self.path)
        library.initialize()
        library.initialize()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual([row[:-1] for row in db.execute("SELECT * FROM videos")], original)
        video = library.get(17)
        self.assertEqual(video.media.frames, 48)
        self.assertEqual(PreparationStore(library).get(17).metadata, YouTubeMetadata())
        saved = PreparationStore(library).save(17, VALID, 0)
        self.assertEqual(saved.revision, 1)
        self.assertEqual(library.get(17), video)

    def test_failed_migration_rolls_back_table_rebuild(self):
        # A conflicting table forces failure after the video-table rebuild, proving DDL rollback.
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE preparations (sentinel TEXT)")
            before = db.execute("SELECT * FROM videos").fetchall()
        with self.assertRaises(sqlite3.Error):
            Library(self.path).initialize()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertNotIn(
                "inspection_revision", [r[1] for r in db.execute("PRAGMA table_info(videos)")]
            )
            self.assertEqual(db.execute("SELECT * FROM videos").fetchall(), before)
            db.execute("DROP TABLE preparations")
        Library(self.path).initialize()
        self.assertEqual(Library(self.path).get(17).media.frames, 48)


class PreparationStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        cls.fixture = Path(cls.fixtures.name) / "real.mp4"
        make_video(cls.fixture)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        shutil.copyfile(self.fixture, self.source)
        self.original = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.library = Library(self.root / "library.sqlite3")
        self.service = LibraryService(self.library, Inspector())
        self.store = self.service.preparations
        self.video = self.service.execute("import", str(self.source), Event())["selected"]

    def save(self, data=VALID):
        old = self.store.get(self.video.id)
        return self.store.save(self.video.id, data, old.revision)

    def review(self):
        preparation = self.store.get(self.video.id)
        video = self.library.get(self.video.id)
        return self.store.complete(video.id, preparation.revision, video.inspection_revision)

    def test_incomplete_invalid_drafts_can_be_saved(self):
        data = replace(YouTubeMetadata(), title="x" * 101, description="🎬" * 1251)
        saved = self.save(data)
        self.assertEqual(saved.metadata, data)
        self.assertTrue(metadata_errors(saved.metadata))
        with self.assertRaisesRegex(LibraryError, "Review blocked"):
            self.review()

    def test_exact_unicode_fields_and_restart(self):
        data = replace(VALID, privacy="unlisted", made_for_kids=True, synthetic=True)
        saved = self.save(data)
        reopened = PreparationStore(Library(self.library.path))
        self.assertEqual(reopened.get(self.video.id), saved)
        self.assertEqual(saved.metadata.description.encode(), data.description.encode())
        self.assertTrue(saved.modified_at)

    def test_complete_review_persists(self):
        saved = self.save()
        reviewed = self.review()
        self.assertEqual(reviewed.reviewed_revision, saved.revision)
        self.assertTrue(reviewed.reviewed_media)
        self.assertTrue(reviewed.reviewed_at)
        reopened = LibraryService(Library(self.library.path), Inspector())
        result = reopened.execute("select", self.video.id, Event())
        self.assertEqual(
            review_checks(result["selected"], result["preparation"], result["source_status"]).state,
            "Review Complete",
        )

    def test_each_field_change_invalidates(self):
        for field, value in (
            ("title", "Changed"),
            ("description", "New description"),
            ("tags", ("different",)),
            ("made_for_kids", True),
            ("synthetic", True),
            ("privacy", "public"),
        ):
            with self.subTest(field=field):
                self.save(VALID)
                self.review()
                updated = self.save(replace(VALID, **{field: value}))
                self.assertEqual(updated.reviewed_at, "")
                self.assertIsNone(updated.reviewed_revision)
                self.assertEqual(review_checks(self.video, updated, "").state, "Draft")

    def test_same_value_save_does_not_destroy_review(self):
        self.save()
        reviewed = self.review()
        self.assertEqual(self.save(), reviewed)

    def test_missing_source_invalidates_durably(self):
        self.save()
        self.review()
        data = self.source.read_bytes()
        self.source.unlink()
        result = self.service.execute("check", self.video.id, Event())
        self.assertEqual(result["preparation"].reviewed_at, "")
        self.assertIn("missing", result["source_status"])
        with self.assertRaises(LibraryError):
            self.review()
        self.source.write_bytes(data)
        self.assertEqual(self.store.get(self.video.id).reviewed_at, "")

    def test_changed_source_requires_refresh(self):
        self.save()
        self.review()
        self.source.write_bytes(self.source.read_bytes() + b"changed")
        result = self.service.execute("check", self.video.id, Event())
        self.assertIn("changed", result["source_status"])
        self.assertEqual(result["preparation"].reviewed_at, "")
        with self.assertRaises(LibraryError):
            self.review()
        shutil.copyfile(self.fixture, self.source)
        refreshed = self.service.execute("refresh", self.video.id, Event())["selected"]
        self.assertGreater(refreshed.inspection_revision, self.video.inspection_revision)
        self.assertTrue(self.review().reviewed_at)

    def test_refresh_even_same_media_invalidates(self):
        self.save()
        self.review()
        self.service.execute("refresh", self.video.id, Event())
        self.assertEqual(self.store.get(self.video.id).reviewed_at, "")

    def test_stale_metadata_or_inspection_cannot_be_reviewed(self):
        old = self.save()
        self.save(replace(VALID, title="New"))
        with self.assertRaisesRegex(LibraryError, "changed"):
            self.store.complete(self.video.id, old.revision, self.video.inspection_revision)
        current = self.store.get(self.video.id)
        self.service.execute("refresh", self.video.id, Event())
        with self.assertRaisesRegex(LibraryError, "changed"):
            self.store.complete(self.video.id, current.revision, self.video.inspection_revision)

    def test_two_concurrent_saves_do_not_lose_updates(self):
        old = self.save()

        def attempt(title):
            try:
                return self.store.save(self.video.id, replace(VALID, title=title), old.revision)
            except LibraryError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("One", "Two")))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.store.get(self.video.id).revision, 2)

    def test_save_failure_rolls_back_and_recovers(self):
        original = self.save()
        with sqlite3.connect(self.library.path) as db:
            db.execute(
                "CREATE TRIGGER fail_save BEFORE UPDATE ON preparations BEGIN SELECT RAISE(ABORT, 'test disk error'); END"
            )
        with self.assertRaises(LibraryError):
            self.service.execute(
                "save_metadata",
                (self.video.id, replace(VALID, title="Lost?"), original.revision),
                Event(),
            )
        self.assertEqual(self.store.get(self.video.id), original)
        with sqlite3.connect(self.library.path) as db:
            db.execute("DROP TRIGGER fail_save")
        self.assertEqual(self.save(replace(VALID, title="Recovered")).metadata.title, "Recovered")

    def test_silent_warning_allowed_but_landscape_blocks(self):
        self.save()
        self.library.update(self.video.id, replace(self.video.media, audio_codecs=()))
        reviewed = self.review()
        video = self.library.get(self.video.id)
        result = review_checks(video, reviewed, source_status(video))
        self.assertEqual(result.state, "Review Complete")
        self.assertIn("No audio", str(result.warnings))
        self.library.update(self.video.id, replace(video.media, aspect=1.5))
        with self.assertRaisesRegex(LibraryError, "square or vertical"):
            self.review()

    def test_remove_cascades_and_preserves_original(self):
        self.save()
        self.review()
        self.service.execute("remove", self.video.id, Event())
        with sqlite3.connect(self.library.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM preparations").fetchone()[0], 0)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original)

    def test_duplicate_import_keeps_metadata(self):
        self.save()
        reviewed = self.review()
        result = self.service.execute("import", str(self.source), Event())
        self.assertEqual(result["preparation"], reviewed)

    def test_removed_video_cannot_receive_late_save(self):
        self.library.remove(self.video.id)
        with self.assertRaisesRegex(LibraryError, "removed"):
            self.store.save(self.video.id, VALID, 0)

    def test_deleted_ids_are_not_reused_by_new_imports(self):
        self.library.remove(self.video.id)
        imported = self.service.execute("import", str(self.source), Event())["selected"]
        self.assertGreater(imported.id, self.video.id)
        with self.assertRaisesRegex(LibraryError, "removed"):
            self.store.save(self.video.id, VALID, 0)
        self.assertEqual(self.store.get(imported.id).metadata, YouTubeMetadata())
