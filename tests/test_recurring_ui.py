"""Finite consent and human-facing recurring queue projections in real Qt."""

import unittest
from unittest.mock import patch

import test_recurring as fixtures
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QPlainTextEdit
from support import settle

from shortspublisher.app import build
from shortspublisher.ui.recurring import BatchConfirmation, PickVideo, RecurringEditor, outcome


class RecurringUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.RecurringTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.RecurringTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.RecurringTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.app, self.w = build(
            self.f.u.root, account_service=self.f.u.accounts, upload_service=self.f.u.service
        )
        self.w.recurring.service = self.f.r
        self.w.show()
        self.w.recurring.activate()
        settle(self.app, self.w.recurring)
        self.c = self.w.recurring
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.c.editor = None
        for controller in (
            self.c,
            self.w.watching,
            self.w.automatic,
            self.w.background,
            *self.w.socials,
            self.w.dashboard,
        ):
            controller.stop_for_quit()
        self.w.close()
        for controller in (self.w.videos, self.w.accounts, self.w.uploads, self.w.schedules):
            settle(self.app, controller)
        self.w.deleteLater()
        self.app.processEvents()

    def editor(self):
        editor = RecurringEditor(self.c)
        self.addCleanup(editor.preview.release)
        self.addCleanup(editor.deleteLater)
        return editor

    def test_no_implicit_plans_or_upload(self):
        self.assertFalse(self.c.data["plans"])
        self.assertEqual(self.f.u.http.initiations, 0)

    def test_consent_requires_both_unchecked_inputs(self):
        d = BatchConfirmation(self.f.proposal(), self.w)
        self.addCleanup(d.deleteLater)
        self.assertFalse(d.consent.isChecked())
        self.assertFalse(d.restrictions.isChecked())
        self.assertFalse(d.approve.isEnabled())
        d.consent.setChecked(True)
        self.assertFalse(d.approve.isEnabled())
        d.restrictions.setChecked(True)
        self.assertTrue(d.approve.isEnabled())

    def test_confirmation_contains_exact_metadata_channel_rule(self):
        p = self.f.proposal()
        d = BatchConfirmation(p, self.w)
        self.addCleanup(d.deleteLater)
        text = "\n".join(w.toPlainText() for w in d.findChildren(QPlainTextEdit))
        for value in (
            p["channel_id"],
            "PUBLIC",
            p["items"][0]["snapshot"]["sha256"],
            p["items"][0]["snapshot"]["metadata"]["title"],
            "09:00",
            "UTC",
        ):
            self.assertIn(value, text)
        self.assertEqual(self.f.u.http.initiations, 0)

    def test_options_and_default_background(self):
        e = self.editor()
        self.assertFalse(e.background.isChecked())
        # No daily limit field: only YouTube's own 100 a day applies.
        self.assertEqual(e.limit, 100)
        self.assertEqual(e.values()["limit"], 100)
        self.assertEqual(
            {e.frequency.itemData(i) for i in range(e.frequency.count())}, {0, 1, 2, 3, 4, -1}
        )
        self.assertEqual(e.hours.minimum(), 1)
        self.assertEqual(e.hours.maximum(), 24)
        self.assertEqual(e.videos.count(), 1)

    def test_videos_are_ticked_counted_and_cleared(self):
        e = self.editor()
        self.assertFalse(e.dirty)  # opening the window is not an edit
        self.assertEqual(e.chosen.text(), "None chosen")
        item = e.videos.item(0)
        self.assertTrue(item.flags() & Qt.ItemFlag.ItemIsUserCheckable, item.text())
        item.setCheckState(Qt.CheckState.Checked)
        self.assertEqual(e.chosen.text(), "1 chosen")
        self.assertEqual(e.values()["videos"], [item.data(Qt.ItemDataRole.UserRole)])
        self.assertTrue(e.dirty)
        e.tick(False)
        self.assertEqual(e.values()["videos"], [])
        e.tick(True)
        self.assertEqual(e.chosen.text(), "1 chosen")
        # Steps like a new auto upload; the name sits with the times, and the only channel is
        # named under "Where", not in a dropdown.
        self.assertEqual([b.text() for b in e.step_buttons], ["1  Videos", "2  Where", "3  When"])
        self.assertFalse(e.review.isVisibleTo(e))
        e.go(2)
        self.assertTrue(e.review.isVisibleTo(e))
        self.assertTrue(e.name.isVisibleTo(e))
        self.assertFalse(e.channel.isVisibleTo(e))

    def test_upload_one_video_opens_its_upload_step(self):
        picked = []

        def choose(dialog):
            self.assertEqual(dialog.videos.count(), 1)
            dialog.open()
            picked.append(dialog.choice)
            return dialog.result()

        with (
            patch.object(PickVideo, "exec", choose),
            patch.object(self.w.videos, "open_upload") as opened,
        ):
            self.c.single.click()
        opened.assert_called_once_with(picked[0])
        self.assertIs(self.w.stack.currentWidget(), self.w.videos)

    def test_day_presets(self):
        e = self.editor()
        e.days_preset.setCurrentIndex(1)
        self.assertEqual([d.isChecked() for d in e.days], [True] * 5 + [False] * 2)
        e.days_preset.setCurrentIndex(2)
        self.assertEqual([d.isChecked() for d in e.days], [False] * 5 + [True] * 2)

    def test_unsaved_configuration_cancel_is_preserved(self):
        e = self.editor()
        e.name.setText("Unapproved edits")
        e.show()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            e.reject()
        self.assertTrue(e.isVisible())
        self.assertEqual(e.name.text(), "Unapproved edits")
        self.assertFalse(self.f.r.state()["plans"])
        e.dirty = False
        e.reject()

    def test_queue_calendar_and_dashboard_use_same_plan(self):
        self.f.approve()
        self.c.refresh()
        settle(self.app, self.c)
        self.assertEqual(self.c.items.count(), 1)
        self.assertEqual(self.c.opportunities.count(), 8)
        self.assertIn("Paused", self.c.status.text())
        self.assertIn("1 videos waiting", self.c.summary.text())
        self.assertIn("Approved and queued", self.c.items.item(0).text())

    def test_paused_append_rule_is_read_only(self):
        self.f.approve()
        self.c.refresh()
        settle(self.app, self.c)
        e = RecurringEditor(self.c, self.c.current())
        self.addCleanup(e.deleteLater)
        self.addCleanup(e.preview.release)
        self.assertFalse(e.frequency.isEnabled())
        self.assertFalse(e.zone.isEnabled())
        self.assertFalse(e.background.isEnabled())
        self.assertFalse(e.videos.item(0).flags() & Qt.ItemFlag.ItemIsUserCheckable)

    def test_verified_outcome_is_conservative(self):
        self.assertEqual(outcome({"attempt_state": "Needs Reconciliation"}), "Needs reconciliation")
        self.assertNotEqual(
            outcome({"attempt_state": "Public Verified", "processing": "processing"}),
            "Published and verified",
        )
        self.assertEqual(
            outcome({"attempt_state": "Public Verified", "processing": "succeeded"}),
            "Published and verified",
        )

    def test_worker_approval_and_control(self):
        self.c.start("approve", self.f.proposal())
        settle(self.app, self.c)
        self.assertEqual(len(self.c.data["plans"]), 1, self.c.feedback.text())
        self.assertFalse(self.c.current()["enabled"])
        self.c.toggle()
        settle(self.app, self.c)
        self.assertTrue(self.c.current()["enabled"])
        self.assertEqual(self.f.u.http.initiations, 0)


class PostingTimesTests(unittest.TestCase):
    def test_posting_times_respect_the_daily_limit(self):
        from datetime import UTC, datetime

        from shortspublisher.cadence import Rule
        from shortspublisher.ui.recurring import posting_times

        rule = Rule("UTC", (0, 1, 2, 3, 4, 5, 6), "09:00", "21:00", 3)
        times = posting_times(rule, datetime(2030, 1, 1, 8, tzinfo=UTC), 4, 2)
        self.assertEqual(
            [t.strftime("%d %H:%M") for t in times],
            ["01 09:00", "01 12:00", "02 09:00", "02 12:00"],
        )
