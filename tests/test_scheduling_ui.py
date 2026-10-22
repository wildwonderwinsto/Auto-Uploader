"""Real Qt scheduling widgets/workers with real isolated SQLite records."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import test_uploads_ui as fixtures
from account_support import CHANNEL
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialog, QMessageBox, QPlainTextEdit
from support import settle
from test_scheduling import NOW

from shortspublisher.ui.scheduling import ScheduleConfirmation, ScheduleEditor


class SchedulingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UploadUiTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.UploadUiTests.tearDownClass()

    def setUp(self):
        self.ui = fixtures.UploadUiTests()
        self.ui.setUp()
        self.c = self.ui.window.schedules
        self.c.service.clock = lambda: NOW
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.c.editor:
            self.c.editor.dirty = False
            self.c.editor.reject()
            self.c.editor.deleteLater()
            self.c.editor = None
        self.c.timer.stop()
        settle(self.ui.app, self.c)
        self.ui.tearDown()

    def editor(self):
        e = ScheduleEditor(self.c, self.ui.fixture.video.id, CHANNEL.id)
        self.c.editor = e
        e.show()
        e.zone.setCurrentText("UTC")
        e.date.setDate(QDate(2030, 1, 2))
        return e

    def approve(self):
        e = self.editor()
        with patch.object(ScheduleConfirmation, "exec", return_value=QDialog.DialogCode.Accepted):
            e.review.click()
            settle(self.ui.app, self.c)
        return self.c.jobs[0]

    def test_schedule_button_public_review(self):
        self.assertTrue(self.ui.window.schedule_button.isEnabled())
        original = self.ui.window.videos.editor.title.text()
        self.ui.window.videos.editor.title.setText("Unsaved edit")
        self.ui.window.update_upload_context()
        self.assertFalse(self.ui.window.schedule_button.isEnabled())
        self.ui.window.videos.editor.title.setText(original)

    def test_empty_queue_and_calendar(self):
        self.c.start("load")
        settle(self.ui.app, self.c)
        self.assertEqual(self.c.queue.list.count(), 0)
        self.assertIn("No scheduled YouTube uploads on this day", self.c.calendar.status.text())
        self.assertIn("No scheduled YouTube uploads.", self.c.queue.status.text())
        self.assertFalse(self.c.queue.list.isVisibleTo(self.c.queue))

    def test_editor_input_and_timezone(self):
        e = self.editor()
        from shortspublisher.ui.formatting import time_pattern

        self.assertEqual(e.time.displayFormat(), time_pattern(seconds=True))
        self.assertIn("UTC", e.feedback.text())
        self.assertTrue(e.date.calendarPopup())

    def test_confirmation_includes_approved_content(self):
        p = self.c.service.preview(
            self.ui.fixture.video.id, CHANNEL.id, "2030-01-02T12:00:00", "UTC"
        )
        d = ScheduleConfirmation(p, self.ui.window)
        body = "\n".join(box.toPlainText() for box in d.findChildren(QPlainTextEdit))
        for value in (
            "PUBLIC",
            CHANNEL.id,
            "UTC",
            "separate automatic Public activation",
            "Source SHA-256",
            p.snapshot["path"],
        ):
            self.assertIn(value, body)
        d.deleteLater()

    def test_cancel_confirmation_creates_no_job(self):
        e = self.editor()
        with patch.object(ScheduleConfirmation, "exec", return_value=QDialog.DialogCode.Rejected):
            e.review.click()
            settle(self.ui.app, self.c)
        self.assertEqual(self.c.service.all(), [])
        self.assertEqual(self.ui.fixture.http.initiations, 0)

    def test_approve_populates_both_pages(self):
        job = self.approve()
        self.assertEqual(self.c.queue.list.count(), 1)
        self.c.calendar.calendar.setSelectedDate(
            QDate.fromString(job.local_time[:10], "yyyy-MM-dd")
        )
        self.assertEqual(self.c.calendar.list.count(), 1)
        self.assertIn("Scheduled", self.c.calendar.details.toPlainText())

    def test_draft_has_no_approval(self):
        e = self.editor()
        e.draft.click()
        settle(self.ui.app, self.c)
        self.assertEqual(self.c.jobs[0].state, "Draft")
        self.assertEqual(self.c.jobs[0].approved_at, "")

    def test_cancel_confirmation_and_filters(self):
        self.approve()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.c.queue.cancel.click()
        self.assertEqual(self.c.service.all()[0].state, "Scheduled")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.c.queue.cancel.click()
            settle(self.ui.app, self.c)
        self.assertEqual(self.c.queue.list.count(), 0)
        self.c.queue.filter.setCurrentText("Cancelled")
        self.assertEqual(self.c.queue.list.count(), 1)

    def test_calendar_navigation_and_day(self):
        job = self.approve()
        cal = self.c.calendar.calendar
        date = QDate.fromString(job.local_time[:10], "yyyy-MM-dd")
        cal.setSelectedDate(date)
        cal.showNextMonth()
        self.assertEqual(cal.monthShown(), 2)
        cal.showPreviousMonth()
        cal.setSelectedDate(date.addDays(1))
        self.assertEqual(self.c.calendar.list.count(), 0)

    def test_invalid_dst_feedback(self):
        e = self.editor()
        e.zone.setCurrentText("America/Chicago")
        e.date.setDate(QDate(2030, 3, 10))
        e.time.setTime(e.time.time().fromString("02:30:00", "HH:mm:ss"))
        self.assertIn("does not exist", e.feedback.text())

    def test_unsaved_guard(self):
        e = self.editor()
        self.assertTrue(e.dirty)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            e.reject()
        self.assertTrue(e.isVisible())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            e.reject()
        self.assertFalse(e.isVisible())

    def test_evaluate_missed_and_sync(self):
        job = self.approve()
        self.c.service.clock = lambda: (
            datetime.fromisoformat(job.scheduled_utc) + timedelta(minutes=2)
        )
        self.c.start("load")
        settle(self.ui.app, self.c)
        self.assertEqual(self.c.jobs[0].state, "Missed")
        self.assertIn("Missed", self.c.queue.details.toPlainText())

    def test_small_window_and_scroll(self):
        self.approve()
        for size in ((760, 560), (1180, 780)):
            self.ui.window.resize(*size)
            for route in ("queue", "calendar"):
                self.ui.window.navigate(route)
                settle(self.ui.app, self.c)
                self.assertEqual(
                    self.ui.window.stack.currentWidget().horizontalScrollBar().maximum(), 0
                )

    def test_storage_error_keeps_visible_history(self):
        self.approve()
        with patch.object(
            self.c.service, "execute", return_value={"error": "Injected database failure"}
        ):
            self.c.start("load")
            settle(self.ui.app, self.c)
        self.assertEqual(self.c.queue.list.count(), 1)
        self.assertIn("failure", self.c.queue.status.text())
