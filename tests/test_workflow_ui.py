"""Stage 13 navigation intent and shared human-readable presentation regressions."""

import unittest
from pathlib import Path
from unittest.mock import patch

import test_uploads_ui as fixtures
from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox
from support import settle

from shortspublisher.ui.formatting import duration, file_size, timestamp, upload_status


class FormattingTests(unittest.TestCase):
    def test_selected_zone(self):
        self.assertEqual(
            timestamp("2026-10-17T20:30:00Z", "America/Chicago", locale=QLocale("en_US")),
            "Oct 17, 2026 · 3:30 PM CDT",
        )

    def test_24_hour_locale(self):
        self.assertIn(
            "15:30", timestamp("2026-10-17T20:30:00Z", "America/Chicago", locale=QLocale("en_GB"))
        )

    def test_repeated_hour_is_distinguishable(self):
        for instant, abbreviation in (
            ("2026-11-01T06:30:00Z", "CDT"),
            ("2026-11-01T07:30:00Z", "CST"),
        ):
            self.assertIn(
                "1:30 AM " + abbreviation,
                timestamp(instant, "America/Chicago", locale=QLocale("en_US")),
            )

    def test_spring_transition(self):
        self.assertIn(
            "1:59 AM CST",
            timestamp("2026-03-08T07:59:00Z", "America/Chicago", locale=QLocale("en_US")),
        )
        self.assertIn(
            "3:00 AM CDT",
            timestamp("2026-03-08T08:00:00Z", "America/Chicago", locale=QLocale("en_US")),
        )

    def test_year_boundary(self):
        self.assertIn(
            "Dec 31, 2025",
            timestamp("2026-01-01T01:00:00Z", "America/Chicago", locale=QLocale("en_US")),
        )

    def test_half_hour_zone(self):
        self.assertIn(
            "5:30 AM", timestamp("2026-01-01T00:00:00Z", "Asia/Kolkata", locale=QLocale("en_US"))
        )

    def test_seconds_preserved(self):
        self.assertIn(
            "12:00:05 AM", timestamp("2026-01-01T00:00:05Z", "UTC", locale=QLocale("en_US"))
        )

    def test_invalid_and_naive_times(self):
        self.assertEqual(timestamp(""), "Not available")
        self.assertEqual(timestamp("bad"), "Time unavailable")
        self.assertEqual(timestamp("2026-01-01"), "Time zone unavailable")

    def test_duration_and_size(self):
        self.assertEqual(duration(3661), "1h 1m 1s")
        self.assertEqual(duration(float("nan")), "Duration unknown")
        self.assertEqual(file_size(1024**2), "1.0 MiB")

    def test_processing_requires_verified_evidence(self):
        self.assertNotIn("Video processed", upload_status("Needs Reconciliation", "succeeded"))
        self.assertNotIn("Video processed", upload_status("Public Verified", "processing"))
        self.assertIn("Video processed", upload_status("Public Verified", "succeeded"))
        self.assertEqual(upload_status("Needs Reconciliation"), "Check upload result")


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UploadUiTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.UploadUiTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.UploadUiTests()
        self.f.setUp()
        self.w, self.app = self.f.window, self.f.app
        self.p = self.w.videos
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.p.editor.preparation:
            self.p.editor.load(self.p.editor.preparation)
        self.f.tearDown()

    def landing(self):
        self.w.navigate("videos")
        settle(self.app, self.p)
        self.assertEqual(self.p.mode, "library")
        self.assertFalse(self.p.card.isVisible())
        self.assertTrue(self.p.import_button.isVisible())

    def new_source(self, name):
        path = Path(self.f.fixture.root) / name
        path.write_bytes(self.f.fixture.path.read_bytes())
        return str(path.resolve())

    def test_navigation_lands_in_library_from_every_route(self):
        identity = self.p.selected.id
        for route in ("dashboard", "queue", "calendar", "accounts", "settings"):
            self.w.navigate(route)
            for controller in (self.w.schedules, self.w.accounts, self.w.background):
                settle(self.app, controller)
            self.landing()
            self.assertEqual(self.p.selected.id, identity)
            self.assertTrue(self.p.continue_button.isVisible())

    def test_continue_retains_step_and_checks_source(self):
        self.p.tabs.setCurrentIndex(2)
        settle(self.app, self.p)
        self.landing()
        self.p.continue_button.click()
        settle(self.app, self.p)
        self.assertEqual(self.p.mode, "editor")
        self.assertEqual(self.p.tabs.currentIndex(), 2)
        self.assertTrue(self.p.import_button.isVisible())

    def test_import_selects_new_video_at_details(self):
        old = self.p.selected.id
        self.p.start("import", self.new_source("new.mp4"))
        settle(self.app, self.p)
        self.assertNotEqual(self.p.selected.id, old)
        self.assertEqual(self.p.selected.id, self.p.editor.preparation.video_id)
        self.assertEqual(self.p.tabs.currentIndex(), 1)
        self.assertEqual(self.p.mode, "editor")

    def test_multiple_imports_select_first_success(self):
        paths = [self.new_source("first.mp4"), self.new_source("second.mp4")]
        with patch.object(QFileDialog, "getOpenFileNames", return_value=(paths, "")):
            self.p.choose_file()
        settle(self.app, self.p)
        self.assertEqual(self.p.selected.path, paths[0])
        self.assertEqual(len(self.p.records), 3)
        self.assertEqual(self.f.fixture.http.initiations, 0)

    def test_partial_batch_reports_failure_and_keeps_success(self):
        path = self.new_source("valid.mp4")
        self.p.start("import_many", ["/missing/file.mp4", path])
        settle(self.app, self.p)
        self.assertEqual(self.p.selected.path, path)
        self.assertIn("could not", self.p.feedback.text())
        self.assertIn("file.mp4", self.p.feedback.toolTip())

    def test_navigation_during_import_does_not_reopen_editor(self):
        self.p.start("import", self.new_source("later.mp4"))
        self.w.navigate("dashboard")
        self.w.navigate("videos")
        settle(self.app, self.p)
        self.assertEqual(self.p.mode, "library")
        self.assertFalse(self.p.card.isVisible())

    def test_history_refresh_cannot_reopen_editor(self):
        self.landing()
        self.w.uploads.receive({"attempts": []})
        self.w.update_upload_context()
        self.assertFalse(self.p.card.isVisible())

    def test_back_cancel_preserves_edits(self):
        self.p.editor.title.setText("Keep me")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.p.back_button.click()
        self.assertEqual(self.p.mode, "editor")
        self.assertTrue(self.p.editor.dirty)

    def test_back_save_preserves_original_identity(self):
        identity = self.p.selected.id
        self.p.editor.title.setText("Saved before leaving")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save):
            self.p.back_button.click()
        settle(self.app, self.p)
        self.assertEqual(self.p.mode, "library")
        self.assertEqual(
            self.p.service.preparations.get(identity).metadata.title, "Saved before leaving"
        )

    def test_repeated_videos_preserves_unsaved_buffer(self):
        self.p.editor.title.setText("Keep buffer")
        self.landing()
        self.p.continue_button.click()
        self.assertEqual(self.p.editor.title.text(), "Keep buffer")
        self.assertTrue(self.p.editor.dirty)

    def test_removed_continue_returns_to_library(self):
        identity = self.p.selected.id
        self.landing()
        self.p.service.library.remove(identity)
        self.p.continue_button.click()
        settle(self.app, self.p)
        self.assertIsNone(self.p.selected)
        self.assertFalse(self.p.card.isVisible())
        self.assertFalse(self.p.continue_button.isVisible())

    def test_missing_source_does_not_claim_review(self):
        self.f.fixture.path.unlink()
        self.landing()
        self.p.continue_button.click()
        settle(self.app, self.p)
        self.assertFalse(self.p.publish_next.isEnabled())
        self.assertTrue(self.p.availability)

    def test_save_and_review(self):
        self.p.editor.title.setText("Save then review")
        self.p.save_and_review()
        settle(self.app, self.p)
        self.assertFalse(self.p.editor.dirty)
        self.assertEqual(self.p.tabs.currentIndex(), 2)
        self.assertEqual(
            self.p.service.preparations.get(self.p.selected.id).metadata.title, "Save then review"
        )

    def test_explicit_history_has_no_implicit_upload_target(self):
        self.w.show_upload_history()
        settle(self.app, self.p)
        self.assertEqual(self.p.mode, "history")
        self.assertIsNone(self.w.uploads.video_id)
        self.assertFalse(self.w.uploads.upload_button.isEnabled())

    def test_channel_id_is_disclosed_not_repeated(self):
        page = self.w.accounts
        identity = page.view.connection.channel.id
        self.assertNotIn(identity, page.details.text())
        self.assertIn(identity, page.technical.text())
        self.assertFalse(page.diagnostics.toggle.isChecked())

    def test_selection_from_library_reopens_same_video(self):
        self.landing()
        self.p.open_current_item(self.p.list.item(0))
        settle(self.app, self.p)
        self.assertEqual(self.p.mode, "editor")
        self.assertEqual(
            self.p.selected.id, self.p.list.currentItem().data(Qt.ItemDataRole.UserRole)
        )

    def test_initial_history_survives_library_load(self):
        self.p.loaded = False
        self.p.selected = None
        self.w.show_upload_history()
        settle(self.app, self.p)
        self.assertEqual(self.p.mode, "history")
        self.assertTrue(self.p.card.isVisible())
        self.assertIsNone(self.w.uploads.video_id)

    def test_import_from_history_opens_details(self):
        self.w.show_upload_history()
        settle(self.app, self.p)
        self.p.start("import", self.new_source("from-history.mp4"))
        settle(self.app, self.p)
        self.assertEqual(self.p.mode, "editor")
        self.assertEqual(self.p.tabs.currentIndex(), 1)

    def test_import_cancel_keeps_dirty_original(self):
        identity = self.p.selected.id
        self.p.editor.title.setText("Do not discard")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            self.p.start("import_many", [self.new_source("cancelled.mp4")])
        self.assertEqual(self.p.selected.id, identity)
        self.assertEqual(len(self.p.records), 1)
        self.assertTrue(self.p.editor.dirty)

    def test_import_errors_expand_with_keyboard(self):
        from PySide6.QtTest import QTest

        self.p.start("import_many", ["/missing/video.mp4"])
        settle(self.app, self.p)
        self.assertTrue(self.p.import_issues.isVisible())
        self.p.import_issues.toggle.setFocus()
        QTest.keyClick(self.p.import_issues.toggle, Qt.Key.Key_Space)
        self.assertTrue(self.p.import_error_text.isVisible())
        self.assertIn("video.mp4", self.p.import_error_text.text())

    def test_confirmation_keeps_description_lines_in_main_summary(self):
        from copy import deepcopy
        from dataclasses import replace

        from PySide6.QtWidgets import QPlainTextEdit

        from shortspublisher.ui.uploads import UploadConfirmation

        attempt = self.f.fixture.prepare()
        snapshot = deepcopy(attempt.snapshot)
        description = "Source: my inspiration\nSource SHA-256: a literal caption\nSize: a story"
        snapshot["payload"]["snippet"]["description"] = description
        dialog = UploadConfirmation(replace(attempt, snapshot=snapshot), self.w)
        summary = dialog.findChild(QPlainTextEdit, "confirmation_summary").toPlainText()
        self.assertIn(description, summary)
        self.assertIn(attempt.channel_id, summary)
        self.assertNotIn(snapshot["sha256"], summary)
        dialog.deleteLater()
