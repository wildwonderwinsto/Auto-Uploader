"""Consent, notification reconciliation and editor-preserving local import integration."""

import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog
from support import make_video, settle

from shortspublisher.library import Library, LibraryService
from shortspublisher.media import Inspector
from shortspublisher.ui.videos import VideosPage
from shortspublisher.ui.watching import FolderConsent, WatchPanel
from shortspublisher.watching import WatchService


class WatchingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.folder = self.root / "incoming"
        self.folder.mkdir()
        self.library = Library(self.root / "library.sqlite3")
        self.library.initialize()
        self.imports = LibraryService(self.library, Inspector())
        self.tick = 100
        self.service = WatchService(self.imports, lambda: self.tick)
        self.panel = WatchPanel(self.service)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.panel.request_close()
        settle(self.app, self.panel)
        self.panel.close()
        self.temp.cleanup()

    def add(self):
        return self.service.add(
            self.service.preview(str(self.folder), Event()), consent=True, existing=True
        )

    def test_consent_requires_both_choices(self):
        d = FolderConsent(self.service.preview(str(self.folder), Event()), self.panel)
        self.assertFalse(d.approve.isEnabled())
        d.consent.setChecked(True)
        self.assertFalse(d.approve.isEnabled())
        d.existing.setCurrentIndex(2)
        self.assertTrue(d.approve.isEnabled())
        self.assertFalse(d.background.isChecked())
        d.close()

    def test_picker_cancel_does_not_configure(self):
        with patch.object(QFileDialog, "getExistingDirectory", return_value=""):
            self.panel.choose.click()
        self.assertFalse(self.service.folders())

    def test_supported_picker_and_explicit_dialog(self):
        def approve(dialog):
            dialog.consent.setChecked(True)
            dialog.existing.setCurrentIndex(1)
            self.panel.start("scan")
            self.assertIsNone(self.panel.worker)
            return QDialog.DialogCode.Accepted

        with (
            patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.folder)),
            patch.object(FolderConsent, "exec", approve),
        ):
            self.panel.choose.click()
            settle(self.app, self.panel)
        self.assertEqual(len(self.service.folders()), 1)

    def test_reconciliation_without_notification(self):
        self.add()
        self.panel.activate()
        settle(self.app, self.panel)
        self.panel.notifications.blockSignals(True)
        make_video(self.folder / "new.mp4")
        self.panel.timer.timeout.emit()
        settle(self.app, self.panel)
        self.tick += 16
        self.panel.timer.timeout.emit()
        settle(self.app, self.panel)
        self.assertEqual(len(self.library.all()), 1)
        self.assertIn("Imported: 1", self.panel.status.text())
        self.assertIn("Imported successfully", self.panel.activity.item(0).text())

    def test_events_debounce(self):
        for _ in range(30):
            self.panel.changed_directory(str(self.folder))
        self.assertTrue(self.panel.debounce.isSingleShot())
        self.assertGreater(self.panel.debounce.remainingTime(), 500)

    def test_unavailable_folder_reattaches(self):
        self.add()
        moved = self.root / "away"
        self.folder.rename(moved)
        self.panel.activate()
        settle(self.app, self.panel)
        self.assertIn("Needs attention", self.panel.status.text())
        moved.rename(self.folder)
        self.panel.start("scan")
        settle(self.app, self.panel)
        self.assertIn(str(self.folder), self.panel.notifications.directories())

    def test_slow_hash_does_not_block_event_loop(self):
        import time

        self.add()
        make_video(self.folder / "large.mp4")
        self.service.scan(Event())
        self.tick += 16
        original = self.service.hash_file

        def slow(*args):
            time.sleep(0.15)
            return original(*args)

        pulses = []
        with patch.object(self.service, "hash_file", side_effect=slow):
            self.panel.start("scan")
            QTimer.singleShot(10, lambda: pulses.append(bool(self.panel.worker)))
            settle(self.app, self.panel)
        self.assertEqual(pulses, [True])

    def test_import_refresh_preserves_editor_buffer(self):
        make_video(self.root / "manual.mp4")
        page = VideosPage(self.imports)
        page.start("import", str(self.root / "manual.mp4"))
        settle(self.app, page)
        self.panel.library_changed.connect(page.discovery_changed)
        page.editor.title.setText("Unsaved human title")
        selected = page.selected.id
        self.add()
        make_video(self.folder / "different.mp4", duration=3)
        self.panel.start("scan")
        settle(self.app, self.panel)
        self.tick += 16
        self.panel.start("scan")
        settle(self.app, self.panel)
        settle(self.app, page)
        self.assertEqual(page.selected.id, selected)
        self.assertEqual(page.editor.title.text(), "Unsaved human title")
        self.assertTrue(page.editor.dirty)
        self.assertEqual(len(page.entries), 2)
        self.assertEqual(page.mode, "editor")
        page.editor.load(page.editor.preparation)
        page.request_close()
        settle(self.app, page)
        page.preview.release()
        page.close()
        QTest.qWait(10)

    def test_invalid_result_clears_transient_progress(self):
        self.add()
        (self.folder / "bad.mp4").write_bytes(b"invalid")
        self.panel.start("scan")
        settle(self.app, self.panel)
        self.tick += 16
        self.panel.start("scan")
        settle(self.app, self.panel)
        self.assertIn("Scan complete", self.panel.feedback.text())
        self.assertIn("Unsupported or invalid", self.panel.activity.item(0).text())
        self.assertNotIn("moov atom", self.panel.activity.item(0).text())
        self.assertIn("moov atom", self.panel.diagnostics.toPlainText())
