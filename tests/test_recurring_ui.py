"""Finite consent and human-facing recurring queue projections in real Qt."""

import unittest
from unittest.mock import patch

import test_recurring as fixtures
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPlainTextEdit
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

    def test_one_video_from_mac_waits_for_a_busy_videos_page(self):
        videos = self.w.videos
        dialog = PickVideo(self.c)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.preview.release)
        with (
            patch.object(QFileDialog, "getOpenFileNames", return_value=(["/v/new.mp4"], "")),
            patch.object(videos, "start") as start,
        ):
            videos.worker = object()  # a refresh is running
            try:
                dialog.add()
                start.assert_not_called()
            finally:
                videos.worker = None
            videos.idle.emit()  # it ends: the import runs instead of being dropped
        start.assert_called_once_with("import_many", ["/v/new.mp4"])
        self.assertIs(self.w.stack.currentWidget(), videos)

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

    def test_queue_card_shows_its_videos(self):
        self.f.approve()
        self.c.refresh()
        settle(self.app, self.c)
        [view] = self.c.views  # one card per queue
        self.assertEqual(view.platforms, ("youtube",))
        self.assertEqual(view.state, "Paused")
        self.assertTrue(view.lines[0][1].startswith("1 waiting"))
        self.assertIn("YouTube: Waiting", view.videos[0])
        self.assertIn("1 videos waiting", self.c.summary.text())

    def test_edit_opens_with_the_queue_and_everything_can_change(self):
        self.f.approve()
        self.c.refresh()
        settle(self.app, self.c)
        queue = self.c.queue_of(self.c.views[0].key)
        e = RecurringEditor(self.c, queue)
        self.addCleanup(e.deleteLater)
        self.addCleanup(e.preview.release)
        self.assertEqual(e.windowTitle(), "Edit Queue")
        for field in (e.frequency, e.zone, e.background, e.name, e.targets["youtube"]):
            self.assertTrue(field.isEnabled())
        self.assertTrue(e.targets["youtube"].isChecked())
        item = e.videos.item(0)
        self.assertTrue(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)  # its own video
        self.assertEqual(item.checkState(), Qt.CheckState.Checked)
        values = e.values()
        self.assertTrue(values["replace"])
        self.assertEqual(values["plan_id"], queue.plan["id"])

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
        [view] = self.c.views
        self.assertFalse(view.enabled)
        self.c.act("resume", view.key)
        settle(self.app, self.c)
        self.assertTrue(self.c.data["plans"][0]["enabled"])
        self.assertEqual(self.f.u.http.initiations, 0)

    def test_saved_queue_is_on_and_delete_leaves_nothing_waiting(self):
        from shortspublisher.cadence import Rule

        plan = self.f.approve()
        rule = Rule("UTC", tuple(range(7)), "09:00", "21:00", 2)
        self.c.start(
            "queue_save",
            dict(
                identity="",
                revision=0,
                name="My queue",
                rule=rule,
                videos={},
                plan_id=plan,
                background=False,
                clear_plan=None,
            ),
        )
        settle(self.app, self.c)
        [view] = self.c.views  # the queue row and its YouTube plan: still one card
        self.assertEqual((view.platforms, view.enabled), (("youtube",), True))
        self.assertTrue(self.c.data["plans"][0]["enabled"])  # approved and saved: it's on
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.c.act("delete", view.key)
        settle(self.app, self.c)
        self.assertEqual(self.c.views, [])
        self.assertEqual({i["state"] for i in self.f.r.state()["items"]}, {"Removed"})
        self.assertFalse(self.f.r.state()["plans"][0]["enabled"])
        self.assertFalse(self.c.empty.isHidden())
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
