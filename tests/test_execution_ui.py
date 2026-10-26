import time
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

import test_scheduling_ui as fixtures
from account_support import CHANNEL
from PySide6.QtCore import QDate, QDateTime, QTime
from PySide6.QtWidgets import QDialog, QMessageBox, QPlainTextEdit
from support import settle

from shortspublisher.ui.execution import ActivationDialog
from shortspublisher.ui.formatting import timestamp
from shortspublisher.ui.scheduling import ScheduleConfirmation


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

    def schedule_from_upload_step(self):
        """The Upload step's YouTube schedule: one approval, no editor, no second window."""
        when = datetime(2030, 1, 2, 12, tzinfo=UTC)
        with (
            patch.object(ScheduleConfirmation, "exec", return_value=QDialog.DialogCode.Accepted),
            patch.object(ActivationDialog, "exec", side_effect=AssertionError("Second window")),
        ):
            self.f.c.schedule(self.f.ui.fixture.video.id, CHANNEL.id, when)
            settle(self.app, self.f.c)
            self.wait()
        return self.f.c.service.all()[0]

    def test_upload_step_schedule_needs_one_approval(self):
        job = self.schedule_from_upload_step()
        self.assertEqual(job.state, "Scheduled")
        self.assertTrue(job.execution_enabled)
        self.assertEqual(
            datetime.fromisoformat(job.scheduled_utc), datetime(2030, 1, 2, 12, tzinfo=UTC)
        )
        self.assertEqual(self.c.data["paused"], 1)  # Auto uploading is only turned on if asked

    def test_one_approval_can_also_turn_on_auto_uploading(self):
        with patch.object(ScheduleConfirmation, "exec", return_value=QDialog.DialogCode.Accepted):
            original = ScheduleConfirmation.__init__

            def ticked(dialog, *args, **kwargs):
                original(dialog, *args, **kwargs)
                self.assertFalse(dialog.turn_on.isHidden())  # off now, so it's offered
                self.assertFalse(dialog.turn_on.isChecked())  # never assumed
                dialog.turn_on.setChecked(True)

            with patch.object(ScheduleConfirmation, "__init__", ticked):
                self.f.c.schedule(
                    self.f.ui.fixture.video.id, CHANNEL.id, datetime(2030, 1, 2, 12, tzinfo=UTC)
                )
                settle(self.app, self.f.c)
                self.wait()
        self.assertTrue(self.f.c.service.all()[0].execution_enabled)
        self.assertEqual(self.c.data["paused"], 0)

    def test_schedule_click_during_refresh_waits_instead_of_dropping(self):
        self.f.c.start("load")
        self.assertIsNotNone(self.f.c.worker)
        job = self.schedule_from_upload_step()
        self.assertTrue(job.execution_enabled)

    def test_scheduled_video_cannot_go_to_youtube_twice(self):
        job = self.schedule_from_upload_step()
        step = self.window.upload_all
        ready, words = step.state("youtube")
        self.assertFalse(ready)
        self.assertIn("Scheduled", words)
        self.assertIn(timestamp(job.scheduled_utc, job.timezone, weekday=True), words)

    def test_upload_times_are_whole_minutes(self):
        step = self.window.upload_all
        self.assertEqual(step.when.dateTime().time().second(), 0)
        self.assertEqual(step.when.dateTime().time().msec(), 0)
        for social in self.window.socials:
            self.assertEqual(social.when.dateTime().time().second(), 0)
        # Seconds the picker can't show are never kept: 10:40 means 10:40:00.
        step.when.setDateTime(QDateTime(QDate(2030, 1, 2), QTime(10, 40, 40, 500)))
        step.later.click()
        due = step.due()
        self.assertEqual((due.second, due.microsecond), (0, 0))
        social = self.window.socials[0]
        social.when.setDateTime(QDateTime(QDate(2030, 1, 2), QTime(10, 40, 40, 500)))
        self.assertEqual(social.when_utc().second, 0)
        editor = fixtures.ScheduleEditor(self.f.c, self.f.ui.fixture.video.id, CHANNEL.id)
        self.addCleanup(editor.deleteLater)
        editor.time.setTime(QTime(10, 40, 40))
        self.assertTrue(editor.values()["local"].endswith("T10:40:00"))

    def test_default_global_pause_visible(self):
        self.assertIn("Automatic publishing is paused", self.c.summary.text())
        self.assertEqual(self.c.light.text(), "Off")
        self.assertIn("Nothing uploads automatically", self.c.state.text())
        self.assertIn("Turn On", self.c.button.text())

    def test_plan_alone_disabled(self):
        job = self.approve()
        self.assertIn("Disabled", self.window.schedules.queue.diagnostics)
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
            self.assertEqual(page.list.count(), 1)  # one row: the plan shows its upload
            self.assertIn("Public verified", page.list.item(0).text())
            self.assertIn("Video ID:", page.diagnostics)
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

    def test_no_daily_limit_of_our_own(self):
        # Only YouTube's own 100 uploads a day remains; Settings offers no limit to lower.
        self.assertEqual(self.c.data["daily_limit"], 100)
        self.assertFalse(hasattr(self.c, "limit"))

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

    def test_done_upload_offers_open_on_youtube_only(self):
        job = self.active()
        self.now = datetime.fromisoformat(job.scheduled_utc)
        self.c.tick()
        self.wait()
        page = self.window.schedules.queue
        self.assertTrue(page.open_upload.isEnabled())
        for button in (page.edit, page.cancel, page.activate_job, page.stop_upload):
            self.assertTrue(button.isHidden())

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

    def test_one_auto_upload_on_four_platforms_is_one_watched_folder(self):
        def profile(folder):
            return {"folder_id": folder, "enabled": 1, "revoked": 0, "removed": 0}

        automation = self.window.automation
        saved = (automation.data, [dict(s.data) for s in self.window.socials])
        self.addCleanup(lambda: setattr(automation, "data", saved[0]))
        for social, data in zip(self.window.socials, saved[1], strict=True):
            self.addCleanup(lambda s=social, d=data: setattr(s, "data", d))
        automation.data = {**automation.data, "profiles": [profile("f")]}
        for social in self.window.socials:
            social.data = {**social.data, "profiles": [profile("f")]}
        self.assertIn("1 watched folder", self.c.waiting())
        self.window.socials[0].data["profiles"].append(profile("g"))
        self.assertIn("2 watched folders", self.c.waiting())
