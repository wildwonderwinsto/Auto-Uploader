"""Library discovery and selection with real Qt, SQLite and inspected media."""

import unittest
from dataclasses import replace
from threading import Event
from unittest.mock import patch

import test_uploads_ui as fixtures
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from support import settle
from test_library import MEDIA

from shortspublisher.library import Video
from shortspublisher.library_view import LibraryEntry, browse, read_entries
from shortspublisher.preparation import Preparation, YouTubeMetadata


class BrowseRulesTests(unittest.TestCase):
    def setUp(self):
        self.entries = {}
        for identity, name, title, duration in (
            (1, "Zebra.mp4", "Café sunrise", 8),
            (2, "alpha.mp4", "STRASSE", 2),
            (3, "alpha.mp4", "雪", 2),
        ):
            video = Video(
                identity,
                "/original/" + name,
                f"2026-01-0{identity}T00:00:00+00:00",
                "",
                replace(MEDIA, duration=duration),
            )
            self.entries[identity] = LibraryEntry(
                video, Preparation(identity, YouTubeMetadata(title=title))
            )

    def ids(self, query="", category="all", order="newest"):
        return [e.video.id for e in browse(self.entries, query, category, order)]

    def test_filename_search(self):
        self.assertEqual(self.ids("zebra.mp4"), [1])

    def test_saved_title_search(self):
        self.assertEqual(self.ids("sunrise"), [1])

    def test_casefold_search(self):
        self.assertEqual(self.ids("straße"), [2])

    def test_unicode_normalization(self):
        self.assertEqual(self.ids("Cafe\u0301"), [1])
        self.assertEqual(self.ids("雪"), [3])

    def test_location_search(self):
        self.assertEqual(self.ids("/original/"), [3, 2, 1])

    def test_no_results(self):
        self.assertEqual(self.ids("absent"), [])

    def test_all_sort_orders_and_ties(self):
        expected = {
            "newest": [3, 2, 1],
            "oldest": [1, 2, 3],
            "name_az": [2, 3, 1],
            "name_za": [1, 3, 2],
            "shortest": [2, 3, 1],
            "longest": [1, 3, 2],
        }
        for order, ids in expected.items():
            with self.subTest(order=order):
                self.assertEqual(self.ids(order=order), ids)
                self.entries = dict(reversed(list(self.entries.items())))
                self.assertEqual(self.ids(order=order), ids)

    def test_unknown_duration_is_last(self):
        self.entries[2] = replace(
            self.entries[2],
            video=replace(self.entries[2].video, media=replace(MEDIA, duration=float("nan"))),
        )
        self.assertEqual(self.ids(order="shortest")[-1], 2)
        self.assertEqual(self.ids(order="longest")[-1], 2)

    def test_saved_review_requires_valid_metadata(self):
        e = self.entries[1]
        data = replace(e.preparation.metadata, made_for_kids=False, synthetic=False)
        self.entries[1] = replace(
            e, preparation=Preparation(1, data, 2, reviewed_revision=2, reviewed_at="2026-01-01")
        )
        self.assertEqual(self.ids(category="reviewed"), [1])
        self.assertEqual(self.ids(category="needs_review"), [3, 2])
        self.entries[1] = replace(self.entries[1], video=replace(e.video, error="failed"))
        self.assertEqual(self.ids(category="reviewed"), [])
        self.assertEqual(self.ids(category="inspection"), [1])

    def test_plan_is_not_upload_and_results_are_not_completion(self):
        self.entries[1] = replace(self.entries[1], plans=1, attempts=2)
        self.entries[2] = replace(self.entries[2], results=2, attempts=3)
        self.assertEqual(self.ids(category="scheduled"), [1])
        self.assertEqual(self.ids(category="uploaded"), [2])
        self.assertIn("2 YouTube result", self.entries[2].label)
        self.assertNotIn("Completed", self.entries[2].label)


class BrowseUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UploadUiTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.UploadUiTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.UploadUiTests()
        self.f.setUp()
        self.app, self.w = self.f.app, self.f.window
        self.p = self.w.videos
        settle(self.app, self.p)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.p.editor.preparation:
            self.p.editor.load(self.p.editor.preparation)
        self.f.tearDown()

    def refresh(self):
        self.p.reload_button.click()
        settle(self.app, self.p)

    def test_reload_keeps_selected_video_and_step(self):
        identity = self.p.selected.id
        self.p.tabs.setCurrentIndex(1)
        self.refresh()
        self.assertEqual(self.p.selected.id, identity)
        self.assertEqual(self.p.tabs.currentIndex(), 1)

    def test_filter_hides_row_without_changing_editor(self):
        identity = self.p.selected.id
        self.p.search.setText("no matches")
        self.assertEqual(self.p.list.count(), 0)
        self.assertIn("No matches", self.p.library_count.text())
        self.assertIn("editor stays open", self.p.library_count.text())
        self.assertEqual(self.p.selected.id, identity)
        self.p.search.clear()
        self.assertEqual(self.p.list.currentItem().data(Qt.ItemDataRole.UserRole), identity)

    def test_sort_retains_selection(self):
        identity = self.p.selected.id
        for index in range(self.p.sort.count()):
            self.p.sort.setCurrentIndex(index)
            self.assertEqual(self.p.list.currentItem().data(Qt.ItemDataRole.UserRole), identity)

    def test_save_filtered_editor_updates_search_and_review(self):
        self.p.search.setText("new title")
        self.p.editor.title.setText("New title")
        self.p.save_metadata()
        settle(self.app, self.p)
        self.assertEqual(self.p.list.count(), 1)
        self.assertFalse(self.p.entries[self.p.selected.id].reviewed)
        self.p.complete_review()
        settle(self.app, self.p)
        self.assertTrue(self.p.entries[self.p.selected.id].reviewed)

    def test_cancel_reload_preserves_unsaved_edit(self):
        self.p.editor.title.setText("Unsaved")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.refresh()
        self.assertEqual(self.p.editor.title.text(), "Unsaved")
        self.assertTrue(self.p.editor.dirty)

    def test_navigation_retains_selection_and_search(self):
        identity = self.p.selected.id
        self.p.search.setText("video")
        self.w.navigate("dashboard")
        self.w.navigate("videos")
        settle(self.app, self.p)
        self.assertEqual(self.p.selected.id, identity)
        self.assertEqual(self.p.search.text(), "video")

    def test_keyboard_search_and_filter(self):
        self.p.show_library()
        settle(self.app, self.p)
        self.p.search.setFocus()
        QTest.keyClicks(self.p.search, "absent")
        self.assertEqual(self.p.list.count(), 0)
        self.p.search.clear()
        self.p.filter.setFocus()
        QTest.keyClick(self.p.filter, Qt.Key.Key_Down)
        self.assertEqual(self.p.list.count(), 0)  # fixture is already reviewed

    def test_read_projection_is_read_only_and_has_no_upload(self):
        f = self.f.fixture
        before = f.preps.get(f.video.id)
        entries = read_entries(f.library, Event())
        self.assertTrue(entries[f.video.id].reviewed)
        self.assertEqual(before, f.preps.get(f.video.id))
        self.assertEqual(f.http.initiations, 0)

    def test_preview_new_source_clears_frame_time_and_audio(self):
        preview = self.p.preview
        preview.toggle_mute()
        preview.time.setText("0:10 / 0:30")
        preview.seek.setRange(0, 30000)
        preview.set_source("/missing/new.mp4")
        self.assertTrue(preview.audio.isMuted())
        self.assertTrue(preview.player.source().isEmpty())
        self.assertEqual(preview.time.text(), "0:00 / 0:00")
        self.assertEqual(preview.seek.maximum(), 0)
        self.assertFalse(preview.seek.isEnabled())

    def test_missing_preview_has_actionable_feedback(self):
        self.p.preview.set_source("/missing/stage12.mp4")
        self.p.preview.toggle()
        self.assertIn("File not found", self.p.preview.status.text())
        self.assertTrue(self.p.preview.player.source().isEmpty())

    def test_unsupported_preview_clears_stale_frame(self):
        self.p.preview.error(QMediaPlayer.Error.FormatError, "ignored backend detail")
        self.assertIn("unsupported format", self.p.preview.status.text())
        self.assertTrue(self.p.preview.video.frame.isNull())
        self.assertFalse(self.p.preview.seek.isEnabled())

    def test_collapsing_library_does_not_start_work(self):
        self.p.library_section.set_expanded(True)
        settle(self.app, self.p)
        self.p.library_section.set_expanded(False)
        self.assertIsNone(self.p.worker)

    def test_removed_selection_does_not_keep_review_summary(self):
        self.p.start("remove", self.p.selected.id)
        settle(self.app, self.p)
        self.assertIsNone(self.p.selected)
        self.assertIn("Select a video", self.p.step_status.text())
        self.assertNotIn("Review complete", self.p.step_status.text())

    def test_changed_source_clears_saved_review_filter_on_selection(self):
        self.f.fixture.path.write_bytes(b"changed controlled test source")
        self.p.start("select", self.p.selected.id)
        settle(self.app, self.p)
        self.p.filter.setCurrentIndex(self.p.filter.findData("reviewed"))
        self.assertEqual(self.p.list.count(), 0)
        self.assertIn("Source changed", self.p.availability)

    def test_loading_and_end_feedback(self):
        preview = self.p.preview
        from PySide6.QtCore import QUrl

        preview.player.setSource(QUrl.fromLocalFile(str(self.f.fixture.path)))
        preview.media_status(QMediaPlayer.MediaStatus.LoadingMedia)
        self.assertIn("Loading", preview.status.text())
        preview.media_status(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertIn("finished", preview.status.text())
        preview.release()


class BrowsePersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_scheduling as scheduling

        cls.fixtures = scheduling
        scheduling.SchedulingTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.SchedulingTests.tearDownClass()

    def setUp(self):
        self.f = self.fixtures.SchedulingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_future_plan_count_does_not_imply_result(self):
        job = self.f.approve()
        entry = read_entries(self.f.f.library, Event())[job.video_id]
        self.assertEqual(entry.plans, 1)
        self.assertEqual(entry.results, 0)
        self.assertEqual(self.f.f.http.initiations, 0)

    def test_cancelled_plan_not_scheduled(self):
        job = self.f.approve()
        self.f.s.cancel(job.id, job.revision)
        self.assertEqual(read_entries(self.f.f.library, Event())[job.video_id].plans, 0)

    def test_draft_not_scheduled(self):
        job = self.f.s.commit(self.f.preview(), approve=False)
        self.assertEqual(read_entries(self.f.f.library, Event())[job.video_id].plans, 0)

    def test_multiple_results_and_unfinished_attempt_count(self):
        f = self.f.f
        attempt = f.prepare()
        f.service.store.update(
            attempt.id, remote_id="TestVid123_", state="Public Verified", released=True
        )
        second = f.service.store.create(f.video.id, attempt.channel_id, attempt.snapshot)
        f.service.store.update(second.id, remote_id="OtherVid12_", state="Needs Reconciliation")
        entry = read_entries(f.library, Event())[f.video.id]
        self.assertEqual(entry.results, 2)
        self.assertEqual(entry.attempts, 2)
        self.assertEqual(entry.plans, 0)
