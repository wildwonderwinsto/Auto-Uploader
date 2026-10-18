import time
import unittest
from datetime import datetime
from unittest.mock import patch

import test_scheduling_ui as fixtures
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialog, QMessageBox, QPlainTextEdit
from support import settle

from shortspublisher.ui.execution import ActivationDialog
from shortspublisher.ui.formatting import timestamp


class ExecutionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.SchedulingUiTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.SchedulingUiTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.SchedulingUiTests()
        self.f.setUp()
        self.app, self.window = self.f.ui.app, self.f.ui.window
        self.c = self.window.automatic
        self.now = self.f.c.service.clock()
        self.f.c.service.clock = lambda: self.now
        self.f.ui.fixture.http.privacy = "public"
        self.addCleanup(self.cleanup)
        self.c.activate()
        self.wait()

    def cleanup(self):
        self.c.stop_for_quit()
        self.wait()
        self.f.doCleanups()

    def wait(self):
        end = time.monotonic() + 10
        while self.c.worker or self.c.control_worker:
            self.app.processEvents()
            time.sleep(0.005)
            if time.monotonic() > end:
                raise AssertionError("Execution worker timeout")
        self.app.processEvents()

    def approve(self):
        job = self.f.approve()
        self.c.receive(self.c.service.status())
        return job

    def active(self):
        job = self.approve()
        with patch.object(ActivationDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            self.window.schedules.queue.activate_job.click()
        self.wait()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.c.button.click()
        self.wait()
        return self.f.c.service.get(job.id)

    def test_default_global_pause_visible(self):
        self.assertIn("Automatic publishing is paused", self.c.summary.text())
        self.assertIn("Paused", self.c.state.text())
        self.assertIn("Turn On", self.c.button.text())

    def test_plan_alone_disabled(self):
        job = self.approve()
        self.assertIn("Disabled", self.window.schedules.queue.details.toPlainText())
        self.assertFalse(job.execution_enabled)

    def test_activation_dialog_exact_intent(self):
        job = self.approve()
        dialog = ActivationDialog(job, self.window)
        text = "\n".join(box.toPlainText() for box in dialog.findChildren(QPlainTextEdit))
        for value in (
            job.channel_id,
            timestamp(job.scheduled_utc, job.timezone, weekday=True),
            job.source_hash,
            job.snapshot["path"],
            "No second confirmation",
            "PUBLIC",
            "Category: 22",
        ):
            self.assertIn(value, text)
        dialog.deleteLater()

    def test_cancel_activation_no_consent(self):
        self.approve()
        with patch.object(ActivationDialog, "exec", return_value=QDialog.DialogCode.Rejected):
            self.window.schedules.queue.activate_job.click()
        self.wait()
        self.assertFalse(self.f.c.service.all()[0].execution_enabled)
        self.assertFalse(self.c.data["attempts"])

    def test_activation_and_enable_are_distinct(self):
        job = self.approve()
        with patch.object(ActivationDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            self.c.authorize(job)
        self.wait()
        self.assertTrue(self.f.c.service.get(job.id).execution_enabled)
        self.assertEqual(self.c.data["paused"], 1)

    def test_enable_cancel_preserves_pause(self):
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.c.button.click()
        self.assertEqual(self.c.data["paused"], 1)

    def test_due_worker_no_second_dialog_independent_navigation(self):
        job = self.active()
        self.window.navigate("settings")
        self.now = datetime.fromisoformat(job.scheduled_utc)
        with patch.object(
            QMessageBox, "question", side_effect=AssertionError("No due-time dialog")
        ):
            self.c.tick()
            self.wait()
        self.assertEqual(self.c.data["attempts"][0].state, "Public Verified")
        self.assertIn("Video processed", self.c.notification.text())
        self.assertEqual(self.f.ui.fixture.http.initiations, 1)
        self.assertEqual(self.window.stack.currentWidget().objectName(), "settings")

    def test_queue_and_calendar_show_linked_result(self):
        job = self.active()
        self.now = datetime.fromisoformat(job.scheduled_utc)
        self.c.tick()
        self.wait()
        self.window.schedules.calendar.calendar.setSelectedDate(
            QDate.fromString(job.local_time[:10], "yyyy-MM-dd")
        )
        for page in (self.window.schedules.queue, self.window.schedules.calendar):
            self.assertIn("Public verified", page.list.item(0).text())
            self.assertIn("Video ID:", page.details.toPlainText())
            self.assertFalse(page.edit.isEnabled())
            self.assertFalse(page.cancel.isEnabled())
            self.assertTrue(page.history.isEnabled())

    def test_history_action_selects_attempt(self):
        job = self.active()
        self.now = datetime.fromisoformat(job.scheduled_utc)
        self.c.tick()
        self.wait()
        self.window.schedules.queue.history.click()
        settle(self.app, self.window.uploads)
        self.assertEqual(self.window.uploads.selected().id, self.c.data["attempts"][0].id)

    def test_paused_past_job_never_uploads(self):
        job = self.active()
        self.c.button.click()
        self.wait()
        self.now = datetime.fromisoformat(job.scheduled_utc)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.c.button.click()
        self.wait()
        self.c.tick()
        self.wait()
        self.assertEqual(self.f.ui.fixture.http.initiations, 0)
        self.assertIn("Missed", self.window.schedules.queue.list.item(0).text())

    def test_limit_saved_through_worker(self):
        self.c.limit.setValue(2)
        self.c.save_limit.click()
        self.wait()
        self.assertEqual(self.c.data["daily_limit"], 2)

    def test_precise_wakeup_is_bounded_by_safety_interval(self):
        job = self.active()
        from datetime import timedelta

        self.now = datetime.fromisoformat(job.scheduled_utc) - timedelta(seconds=2)
        self.c.tick()
        self.wait()
        self.assertLessEqual(self.c.timer.interval(), 2000)
        self.assertGreater(self.c.timer.interval(), 0)

    def test_shutdown_cancels_active_request_and_no_reupload(self):
        job = self.active()
        self.now = datetime.fromisoformat(job.scheduled_utc)
        self.f.ui.fixture.http.delay = 0.08
        self.c.tick()
        end = time.monotonic() + 3
        while not self.f.ui.fixture.http.initiations and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertFalse(self.c.request_close())
        self.wait()
        self.assertFalse(self.c.timer.isActive())
        self.assertEqual(self.f.ui.fixture.http.initiations, 1)

    def test_completed_filter_uses_verified_processing(self):
        job = self.active()
        self.now = datetime.fromisoformat(job.scheduled_utc)
        self.c.tick()
        self.wait()
        page = self.window.schedules.queue
        page.filter.setCurrentText("Completed")
        self.assertEqual(page.list.count(), 1)
        page.filter.setCurrentText("Uploading")
        self.assertEqual(page.list.count(), 0)

    def test_queue_stop_button_cancels_current_worker(self):
        job = self.active()
        self.now = datetime.fromisoformat(job.scheduled_utc)
        self.f.ui.fixture.http.delay = 0.1
        self.c.tick()
        end = time.monotonic() + 3
        page = self.window.schedules.queue
        while not page.stop_upload.isEnabled() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(page.stop_upload.isEnabled())
        page.stop_upload.click()
        self.wait()
        self.assertTrue(
            self.c.data["attempts"][0].state in ("Paused", "Interrupted", "Public Verified")
        )
