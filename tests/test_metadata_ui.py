import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QMessageBox
from support import make_video, settle

from shortspublisher.app import build
from shortspublisher.library import LibraryService
from shortspublisher.preparation import YouTubeMetadata

SAVE = QMessageBox.StandardButton.Save
DISCARD = QMessageBox.StandardButton.Discard
CANCEL = QMessageBox.StandardButton.Cancel


class MetadataUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        cls.source = Path(cls.fixtures.name) / "Metadata fixture 雪.mp4"
        make_video(cls.source)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app, self.window = build(Path(self.temp.name))
        self.window.show()
        self.window.navigate("videos")
        self.page = self.window.videos
        settle(self.app, self.page)
        self.page.start("import", str(self.source))
        settle(self.app, self.page)
        self.editor = self.page.editor
        self.page.tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.video_id = self.page.selected.id

    def tearDown(self):
        settle(self.app, self.page)
        with patch.object(QMessageBox, "question", return_value=DISCARD):
            self.window.close()
        settle(self.app, self.page)
        self.window.deleteLater()
        self.app.processEvents()

    def fill(self):
        self.editor.title.setText("Prepared video 🎬")
        self.editor.description.setPlainText("雪 and emoji 🎬\n\nhttps://example.com/watch")
        for tag in ("first tag", "雪"):
            self.editor.tag_input.setText(tag)
            self.editor.add_tag_button.click()
        self.editor.kids.setCurrentIndex(2)
        self.editor.synthetic.setCurrentIndex(1)
        self.editor.privacy.setCurrentIndex(1)

    def save(self):
        self.editor.save_button.click()
        settle(self.app, self.page)

    def review(self):
        self.page.tabs.setCurrentIndex(2)
        settle(self.app, self.page)
        self.page.review_button.click()
        settle(self.app, self.page)

    def test_all_fields_counters_save_and_reload(self):
        self.fill()
        data = self.editor.value()
        self.assertIn(str(len(data.title)), self.editor.title_counter.text())
        self.assertIn(str(len(data.description.encode())), self.editor.description_counter.text())
        self.assertIn("Unsaved", self.editor.save_state.text())
        self.save()
        self.assertFalse(self.editor.dirty)
        self.assertEqual(self.page.service.preparations.get(self.video_id).metadata, data)
        self.page.start("select", self.video_id)
        settle(self.app, self.page)
        self.assertEqual(self.editor.value(), data)
        self.assertIn("Saved on this Mac", self.editor.save_state.text())

    def test_incomplete_draft_errors_and_review_disabled(self):
        self.editor.title.setText("x" * 101)
        self.editor.description.setPlainText("🎬" * 1251)
        self.assertIn("100 characters", self.editor.errors.text())
        self.assertIn("5,004", self.editor.description_counter.text())
        self.save()
        self.page.tabs.setCurrentIndex(2)
        settle(self.app, self.page)
        self.assertIn("Needs Attention", self.page.review_state.text())
        self.assertFalse(self.page.review_button.isEnabled())
        self.assertIn("Made for Kids", self.page.review_errors.text())

    def test_tag_add_duplicate_remove_and_pending_save(self):
        self.editor.tag_input.setText("Some Tag")
        self.editor.add_tag_button.click()
        self.editor.tag_input.setText("some tag")
        self.editor.add_tag_button.click()
        self.assertEqual(self.editor.tags, ("Some Tag",))
        self.assertIn("already present", self.editor.tag_counter.text())
        self.editor.tag_list.setCurrentRow(0)
        self.editor.remove_tag_button.click()
        self.assertEqual(self.editor.tags, ())
        self.editor.tag_input.setText("Pending tag")
        self.save()
        self.assertEqual(
            self.page.service.preparations.get(self.video_id).metadata.tags, ("Pending tag",)
        )
        self.assertEqual(self.editor.tag_input.text(), "")

    def test_editor_review_button_checks_without_discarding(self):
        self.fill()
        self.editor.review_link.click()
        settle(self.app, self.page)
        self.assertEqual(self.page.tabs.currentIndex(), 2)
        self.assertFalse(self.editor.dirty)
        self.assertTrue(self.page.review_button.isEnabled())
        self.assertIn("Made for Kids: No", self.page.review_summary.toPlainText())
        self.assertIn("Synthetic disclosure: Yes", self.page.review_summary.toPlainText())

    def test_review_and_unsaved_then_saved_invalidation(self):
        self.fill()
        self.save()
        self.review()
        self.assertIn("Review Complete", self.page.review_state.text())
        self.page.tabs.setCurrentIndex(1)
        self.editor.title.setText("A changed title")
        self.assertIn("Draft", self.page.review_state.text())
        self.assertFalse(self.page.review_button.isEnabled())
        self.save()
        self.assertEqual(self.page.service.preparations.get(self.video_id).reviewed_at, "")
        self.review()
        self.assertIn("Review Complete", self.page.review_state.text())

    def test_switch_video_cancel_then_save_targets_original(self):
        second = Path(self.temp.name) / "second.mp4"
        second.write_bytes(self.source.read_bytes())
        self.page.start("import", str(second))
        settle(self.app, self.page)
        second_id = self.page.selected.id
        self.page.start("select", self.video_id)
        settle(self.app, self.page)
        self.editor.title.setText("Only the original")
        with patch.object(QMessageBox, "question", return_value=CANCEL):
            self.page.list.setCurrentRow(0)
        self.assertEqual(self.page.selected.id, self.video_id)
        self.assertTrue(self.editor.dirty)
        with patch.object(QMessageBox, "question", return_value=SAVE):
            self.page.list.setCurrentRow(0)
        settle(self.app, self.page)
        self.assertEqual(self.page.selected.id, second_id)
        self.assertEqual(
            self.page.service.preparations.get(self.video_id).metadata.title, "Only the original"
        )
        self.assertEqual(self.page.service.preparations.get(second_id).metadata, YouTubeMetadata())

    def test_navigation_cancel_discard_save(self):
        self.editor.title.setText("Don't lose me")
        with patch.object(QMessageBox, "question", return_value=CANCEL):
            self.window.navigate("accounts")
        self.assertIs(self.window.stack.currentWidget(), self.page)
        self.assertTrue(self.window.buttons["videos"].isChecked())
        with patch.object(QMessageBox, "question", return_value=DISCARD):
            self.window.navigate("accounts")
        self.assertEqual(self.window.stack.currentWidget().objectName(), "accounts")
        self.window.navigate("videos")
        settle(self.app, self.page)
        self.assertEqual(self.editor.title.text(), "")
        self.editor.title.setText("Keep me")
        with patch.object(QMessageBox, "question", return_value=SAVE):
            self.window.navigate("calendar")
        settle(self.app, self.page)
        self.assertEqual(self.window.stack.currentWidget().objectName(), "calendar")
        self.assertEqual(
            self.page.service.preparations.get(self.video_id).metadata.title, "Keep me"
        )

    def test_close_cancel_then_save(self):
        self.editor.title.setText("Saved on close")
        with patch.object(QMessageBox, "question", return_value=CANCEL):
            self.assertFalse(self.window.close())
        self.assertTrue(self.window.isVisible())
        with patch.object(QMessageBox, "question", return_value=SAVE):
            self.window.close()
        settle(self.app, self.page)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(
            self.page.service.preparations.get(self.video_id).metadata.title, "Saved on close"
        )

    def test_application_quit_cancel_preserves_edits(self):
        self.editor.title.setText("Unsaved at Quit")
        with patch.object(QMessageBox, "question", return_value=CANCEL):
            QCoreApplication.sendEvent(self.app, QEvent(QEvent.Type.Quit))
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.editor.dirty)

    def test_refresh_and_reload_guard(self):
        self.editor.title.setText("Keep draft")
        with patch.object(QMessageBox, "question", return_value=CANCEL):
            self.page.refresh()
            self.page.start("load")
        self.assertTrue(self.editor.dirty)
        self.assertIsNone(self.page.worker)
        with patch.object(QMessageBox, "question", return_value=SAVE):
            self.page.refresh()
        settle(self.app, self.page)
        self.assertEqual(self.editor.title.text(), "Keep draft")
        self.assertGreater(self.page.selected.inspection_revision, 0)

    def test_failed_save_does_not_navigate_or_clear_edits(self):
        self.editor.title.setText("Retain after failure")
        with sqlite3.connect(self.page.service.library.path) as db:
            db.execute(
                "CREATE TRIGGER fail_insert BEFORE INSERT ON preparations BEGIN SELECT RAISE(ABORT, 'test failure'); END"
            )
        with patch.object(QMessageBox, "question", return_value=SAVE):
            self.window.navigate("accounts")
        settle(self.app, self.page)
        self.assertIs(self.window.stack.currentWidget(), self.page)
        self.assertTrue(self.editor.dirty)
        self.assertIn("Operation failed", self.page.feedback.text())
        with sqlite3.connect(self.page.service.library.path) as db:
            db.execute("DROP TRIGGER fail_insert")
        self.save()
        self.assertFalse(self.editor.dirty)

    def test_remove_guard_cancel_and_cascade(self):
        self.editor.title.setText("Pending removal")
        with patch.object(
            QMessageBox, "question", side_effect=[QMessageBox.StandardButton.Yes, CANCEL]
        ):
            self.page.confirm_remove()
        self.assertIn(self.video_id, self.page.records)
        with patch.object(
            QMessageBox, "question", side_effect=[QMessageBox.StandardButton.Yes, SAVE]
        ):
            self.page.confirm_remove()
        settle(self.app, self.page)
        self.assertEqual(self.page.records, {})
        self.assertTrue(self.source.exists())
        with sqlite3.connect(self.page.service.library.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM preparations").fetchone()[0], 0)

    def test_check_does_not_overwrite_unsaved_editor(self):
        self.fill()
        expected = self.editor.value()
        self.page.tabs.setCurrentIndex(2)
        settle(self.app, self.page)
        self.assertEqual(self.editor.value(), expected)
        self.assertTrue(self.editor.dirty)
        self.assertFalse(self.page.review_button.isEnabled())

    def test_review_tab_does_not_keep_editor_empty_height(self):
        self.fill()
        self.save()
        self.page.tabs.setCurrentIndex(1)
        self.app.processEvents()
        editor_height = self.page.tabs.height()
        self.page.tabs.setCurrentIndex(2)
        settle(self.app, self.page)
        self.assertLess(self.page.tabs.height(), editor_height)

    def test_layout_all_tabs_both_sizes(self):
        self.fill()
        for size in ((760, 560), (1180, 780)):
            self.window.resize(*size)
            for tab in range(3):
                self.page.tabs.setCurrentIndex(tab)
                settle(self.app, self.page)
                self.app.processEvents()
                self.assertEqual(self.page.horizontalScrollBar().maximum(), 0)
                self.assertFalse(self.window.grab().isNull())

    def test_recheck_does_not_adopt_new_revision_for_stale_edits(self):
        self.fill()
        self.save()
        original_revision = self.editor.preparation.revision
        self.editor.title.setText("Local unsaved edit")
        self.page.service.preparations.save(
            self.video_id, YouTubeMetadata(title="Other window"), original_revision
        )
        self.page.start("check", self.video_id)
        settle(self.app, self.page)
        self.assertEqual(self.editor.preparation.revision, original_revision)
        self.save()
        self.assertTrue(self.editor.dirty)
        self.assertEqual(
            self.page.service.preparations.get(self.video_id).metadata.title, "Other window"
        )

    def test_late_old_revision_cannot_overwrite_another_window(self):
        self.fill()
        self.save()
        revision = self.editor.preparation.revision
        other = LibraryService(self.page.service.library, self.page.service.inspector)
        other.preparations.save(
            self.video_id, YouTubeMetadata(title="External newer draft"), revision
        )
        self.editor.title.setText("My stale edit")
        self.save()
        self.assertTrue(self.editor.dirty)
        self.assertIn("another window", self.page.feedback.text())
        self.assertEqual(
            other.preparations.get(self.video_id).metadata.title, "External newer draft"
        )
