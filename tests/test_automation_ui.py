"""Actual Qt profile controls with isolated local services and controlled Google."""

import unittest
from unittest.mock import patch

import test_automation as fixtures
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPlainTextEdit
from support import settle

from shortspublisher.app import build
from shortspublisher.ui.automation import ProfileConsent, ProfileEditor


class AutomationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AutomationTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.AutomationTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.AutomationTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.app, self.w = build(
            self.f.u.root,
            account_service=self.f.u.accounts,
            upload_service=self.f.u.service,
            schedule_service=self.f.s,
        )
        self.c = self.w.automation
        self.w.show()
        self.c.activate()
        settle(self.app, self.c)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.c.editor = None
        for controller in (
            self.c,
            self.w.recurring,
            self.w.watching,
            self.w.automatic,
            self.w.background,
        ):
            controller.stop_for_quit()
        self.w.close()
        for controller in (self.w.videos, self.w.accounts, self.w.uploads, self.w.schedules):
            settle(self.app, controller)
        self.w.deleteLater()
        self.app.processEvents()

    def editor(self):
        editor = ProfileEditor(self.c)
        self.addCleanup(editor.deleteLater)
        return editor

    def test_review_mode_default_and_no_inferred_declarations(self):
        e = self.editor()
        self.assertEqual(e.mode.currentData(), "review")
        self.assertIsNone(e.kids.currentData())
        self.assertIsNone(e.synthetic.currentData())
        self.assertFalse(e.background.isChecked())
        self.assertFalse(self.f.state()["profiles"])

    def test_exact_consent_all_boxes_unchecked(self):
        d = ProfileConsent(self.f.proposal(), self.w)
        self.addCleanup(d.deleteLater)
        self.assertFalse(d.approve.isEnabled())
        for box in (d.scope, d.declarations, d.restrictions):
            self.assertFalse(box.isChecked())
        d.scope.setChecked(True)
        d.declarations.setChecked(True)
        self.assertFalse(d.approve.isEnabled())
        d.restrictions.setChecked(True)
        self.assertTrue(d.approve.isEnabled())

    def test_summary_exact_scope(self):
        d = ProfileConsent(self.f.proposal(), self.w)
        self.addCleanup(d.deleteLater)
        content = "\n".join(w.toPlainText() for w in d.findChildren(QPlainTextEdit))
        for expected in (
            str(self.f.root),
            "UC_test_channel",
            "PUBLIC",
            "{filename}",
            "User supplied",
            "future",
            "180",
            "UTC",
        ):
            self.assertIn(expected, content)

    def test_native_picker_cancel_boundary(self):
        e = self.editor()
        with patch.object(QFileDialog, "getExistingDirectory", return_value="") as picker:
            e.choose_folder()
        picker.assert_called_once()
        self.assertIsNone(self.c.worker)
        self.assertFalse(self.f.state()["profiles"])

    def test_actual_filename_preview(self):
        e = self.editor()
        path = self.f.arrival("My_雪.mp4")
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            e.choose_sample()
        self.assertEqual(e.preview.text(), "My 雪 #Shorts")
        e.template.setText("{filename.__class__}")
        self.assertIn("Only", e.preview.text())

    def test_unsaved_cancel_retains_changes(self):
        e = self.editor()
        e.name.setText("Edited draft")
        e.show()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            e.reject()
        self.assertTrue(e.isVisible())
        self.assertEqual(e.name.text(), "Edited draft")
        self.assertFalse(self.f.state()["profiles"])
        e.dirty = False
        e.reject()

    def test_day_presets(self):
        e = self.editor()
        e.day_preset.setCurrentIndex(1)
        self.assertEqual([d.isChecked() for d in e.days], [True] * 5 + [False] * 2)
        e.day_preset.setCurrentIndex(2)
        self.assertEqual([d.isChecked() for d in e.days], [False] * 5 + [True] * 2)

    def test_overview_and_queue_use_saved_records(self):
        self.f.approve()
        self.f.arrival()
        self.f.scan()
        self.c.activate()
        settle(self.app, self.c)
        self.assertIn("Queue: 1 waiting to post", self.c.status.text())
        self.assertIn("global publishing paused", self.c.summary.text())
        self.assertEqual(self.c.activity.count(), 1)
        self.w.recurring.activate()
        settle(self.app, self.w.recurring)
        self.assertIn("Automation-approved", self.w.recurring.items.item(0).text())

    def test_pause_control_persists(self):
        self.f.approve()
        self.c.activate()
        settle(self.app, self.c)
        self.c.action("pause")
        settle(self.app, self.c)
        self.assertFalse(self.f.state()["profiles"][0]["enabled"])
        self.assertTrue(self.c.headline.text().startswith("Paused"))
        self.assertTrue(self.c.buttons["resume"].isVisibleTo(self.c.page))
        self.assertFalse(self.c.buttons["pause"].isVisibleTo(self.c.page))

    def test_manual_import_and_navigation_retained(self):
        self.w.navigate("automation")
        settle(self.app, self.c)
        self.assertEqual(self.w.stack.currentWidget().objectName(), "automation")
        self.w.navigate("videos")
        settle(self.app, self.w.videos)
        self.assertTrue(self.w.videos.import_button.isVisible())
        self.assertEqual(self.f.u.http.initiations, 0)

    def test_minimum_size_has_no_horizontal_overflow(self):
        self.w.navigate("automation")
        settle(self.app, self.c)
        self.w.resize(760, 560)
        self.app.processEvents()
        self.assertEqual(self.c.page.horizontalScrollBar().maximum(), 0)

    def test_setup_steps_retain_fields_and_do_not_authorize(self):
        e = self.editor()
        e.show()
        e.name.setText("Draft with steps")
        self.assertEqual(e.steps.count(), 4)
        self.assertFalse(e.back.isEnabled())
        for _ in range(3):
            e.next.click()
        self.assertEqual(e.steps.currentIndex(), 3)
        self.assertFalse(e.next.isEnabled())
        for _ in range(3):
            e.back.click()
        self.assertEqual(e.name.text(), "Draft with steps")
        self.assertIsNone(e.kids.currentData())
        self.assertIsNone(e.synthetic.currentData())
        self.assertFalse(self.f.state()["profiles"])

    def test_setup_steps_keep_actions_visible_at_small_size(self):
        e = self.editor()
        e.resize(590, 560)
        e.show()
        for i in range(e.steps.count()):
            e.steps.setCurrentIndex(i)
            self.app.processEvents()
            self.assertTrue(e.review.isVisible())
            self.assertTrue(e.next.isVisible())
            self.assertEqual(e.steps.widget(i).horizontalScrollBar().maximum(), 0)

    def test_enabled_profile_still_shows_global_pause(self):
        self.f.approve()
        self.c.activate()
        settle(self.app, self.c)
        # An enabled profile must not read as active while the global switch blocks posting.
        self.assertTrue(self.c.headline.text().startswith("Not posting"), self.c.headline.text())
        self.assertIn("paused for the whole app", self.c.headline.text())
        self.assertNotIn("Active", self.c.headline.text())
        self.assertIn("Watching for new videos", self.c.status.text())
        self.assertIn("Background service:", self.c.status.text())
