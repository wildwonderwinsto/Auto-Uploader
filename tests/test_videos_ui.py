import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox
from support import make_video, settle

from shortspublisher.app import build
from shortspublisher.media import Inspector, checkpoint


class SlowInspector(Inspector):
    """Deterministic latency for responsiveness and cancellation tests only."""

    def inspect(self, path, cancel):
        for _ in range(50):
            checkpoint(cancel)
            time.sleep(0.01)
        return super().inspect(path, cancel)


class VideosUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        cls.source = Path(cls.fixtures.name) / "UI example 雪.mp4"
        make_video(cls.source)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app, self.window = build(Path(self.temp.name))
        self.window.show()
        self.window.navigate("videos")
        self.page = self.window.videos
        settle(self.app, self.page)

    def tearDown(self):
        self.window.close()
        settle(self.app, self.page)
        self.window.deleteLater()
        self.app.processEvents()

    def import_through_picker(self):
        with patch.object(
            QFileDialog, "getOpenFileNames", return_value=([str(self.source)], "Videos")
        ):
            QTest.mouseClick(self.page.import_button, Qt.MouseButton.LeftButton)
        settle(self.app, self.page)
        self.assertEqual(len(self.page.records), 1, self.page.feedback.text())

    def test_import_details_selection_and_removal(self):
        self.assertTrue(self.page.empty.isVisible())
        self.import_through_picker()
        self.assertIn("160 × 240", self.page.details.toPlainText())
        self.assertIn("h264", self.page.details.toPlainText())
        self.assertIn("aac", self.page.details.toPlainText())
        self.assertEqual(self.page.readiness.text(), "Passes implemented checks")
        self.assertFalse(self.page.empty.isVisible())
        self.assertEqual(self.page.thread(), QThread.currentThread())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            QTest.mouseClick(self.page.remove_button, Qt.MouseButton.LeftButton)
        settle(self.app, self.page)
        self.assertEqual(len(self.page.records), 0)
        self.assertTrue(self.source.exists())
        self.assertTrue(self.page.empty.isVisible())

    def test_multiple_videos_keep_independent_selection(self):
        self.import_through_picker()
        original_id = self.page.selected.id
        second = Path(self.temp.name) / "second.mp4"
        second.write_bytes(self.source.read_bytes())
        self.page.start("import", str(second))
        settle(self.app, self.page)
        self.assertEqual(self.page.list.count(), 2)
        self.assertNotEqual(self.page.selected.id, original_id)
        self.page.list.setCurrentRow(1)
        settle(self.app, self.page)
        self.assertEqual(self.page.selected.id, original_id)
        self.assertIn(str(self.source), self.page.details.toPlainText())

    def test_cancel_picker_and_removal(self):
        with patch.object(QFileDialog, "getOpenFileNames", return_value=([], "")):
            self.page.choose_file()
        self.assertIsNone(self.page.worker)
        self.assertEqual(self.page.records, {})
        self.import_through_picker()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.page.confirm_remove()
        self.assertEqual(len(self.page.records), 1)

    def test_responsiveness_and_conflicting_actions(self):
        self.page.service.inspector = SlowInspector()
        ticks = []
        timer = QTimer(self.window)
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start()
        self.page.start("import", str(self.source))
        self.assertFalse(self.page.import_button.isEnabled())
        self.assertFalse(self.page.reload_button.isEnabled())
        self.assertFalse(self.page.list.isEnabled())
        self.assertTrue(self.page.progress.isVisible())
        worker = self.page.worker
        self.page.start("import", str(self.source))
        self.assertIs(self.page.worker, worker)
        QTest.mouseClick(self.window.buttons["accounts"], Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.stack.currentWidget().objectName(), "accounts")
        settle(self.app, self.page)
        timer.stop()
        self.assertGreater(len(ticks), 10)
        self.assertTrue(self.page.import_button.isEnabled())
        self.assertEqual(len(self.page.records), 1)

    def test_close_during_inspection_cancels_without_partial_record(self):
        self.page.service.inspector = SlowInspector()
        self.page.start("import", str(self.source))
        self.window.close()
        self.assertTrue(self.page.closing)
        settle(self.app, self.page)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.page.service.library.all(), [])

    def test_application_level_quit_stops_worker(self):
        self.page.service.inspector = SlowInspector()
        self.page.start("import", str(self.source))
        self.page.stop_for_quit()
        self.assertFalse(self.page.worker.isRunning())
        settle(self.app, self.page)
        self.assertEqual(self.page.records, {})

    def test_failure_then_success(self):
        broken = Path(self.temp.name) / "invalid.mp4"
        broken.write_bytes(b"not a video")
        self.page.start("import", str(broken))
        settle(self.app, self.page)
        self.assertIn("Operation failed", self.page.feedback.text())
        self.assertEqual(self.page.records, {})
        self.import_through_picker()
        self.assertIn("saved", self.page.feedback.text())

    def test_reopen_and_missing_file(self):
        self.import_through_picker()
        video_id = self.page.selected.id
        self.window.close()
        app, reopened = build(Path(self.temp.name))
        reopened.show()
        try:
            reopened.navigate("videos")
            settle(app, reopened.videos)
            self.assertEqual(len(reopened.videos.records), 1)
            with patch("shortspublisher.library.source_status", return_value="Source missing."):
                reopened.videos.start("select", video_id)
                settle(app, reopened.videos)
            self.assertEqual(reopened.videos.readiness.text(), "Not currently verified")
            self.assertIn("Source missing", reopened.videos.warnings.text())
        finally:
            reopened.close()
            settle(app, reopened.videos)
            reopened.deleteLater()

    def test_refresh_error_and_recovery(self):
        self.import_through_picker()
        self.page.service.inspector = Inspector("/missing/ffprobe")
        self.page.refresh()
        settle(self.app, self.page)
        self.assertEqual(self.page.readiness.text(), "Not currently verified")
        self.assertIn("could not start", self.page.warnings.text())
        self.page.service.inspector = Inspector()
        self.page.refresh()
        settle(self.app, self.page)
        self.assertEqual(self.page.readiness.text(), "Passes implemented checks")

    def test_small_layout_long_filename(self):
        source = Path(self.temp.name) / ("雪" * 70 + ".mp4")
        source.write_bytes(self.source.read_bytes())
        self.page.start("import", str(source))
        settle(self.app, self.page)
        for size in ((760, 560), (1180, 780)):
            self.window.resize(*size)
            self.app.processEvents()
            self.assertEqual(self.page.horizontalScrollBar().maximum(), 0)
            self.assertFalse(self.window.grab().isNull())
            self.assertGreaterEqual(
                self.page.remove_button.width(), self.page.remove_button.sizeHint().width()
            )

    def test_database_failure_is_visible_and_retry_recovers(self):
        self.window.close()
        path = Path(self.temp.name) / "library.sqlite3"
        original = path.read_bytes()
        path.write_bytes(b"bad database")
        self.page.start("load")
        settle(self.app, self.page)
        self.assertIn("not reset", self.page.feedback.text())
        self.assertEqual(path.read_bytes(), b"bad database")
        path.write_bytes(original)
        self.page.start("load")
        settle(self.app, self.page)
        self.assertNotIn("failed", self.page.feedback.text())
