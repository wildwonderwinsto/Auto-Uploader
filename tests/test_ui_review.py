"""UI review regressions: compact actions, honest automation status, readable labels."""

import re
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QAbstractButton, QPushButton
from support import settle

from shortspublisher.app import build
from shortspublisher.ui import automation
from shortspublisher.ui.components import FlowLayout, tidy_buttons
from shortspublisher.ui.window import PAGES

UI = Path(automation.__file__).parent


def profile(**changes):
    value = {
        "revoked": False,
        "enabled": True,
        "settings": {"mode": "automatic", "background": False},
    }
    value.update(changes)
    return value


class HeadlineTests(unittest.TestCase):
    def test_never_active_while_blocked(self):
        blocked = (
            (profile(revoked=True), True, False, "Needs approval again"),
            (profile(enabled=False), True, False, "Paused"),
            (profile(), True, True, "Not posting · automatic publishing is paused"),
            (profile(), False, False, "Not posting · the watched folder"),
        )
        for p, watching, paused, expected in blocked:
            with self.subTest(expected=expected):
                text = automation.headline(p, watching, paused, "Running")
                self.assertTrue(text.startswith(expected), text)
                self.assertNotIn("Active", text)

    def test_active_says_when_it_can_post(self):
        self.assertIn(
            "while ShortsScheduler is open", automation.headline(profile(), True, False, None)
        )
        background = profile(settings={"mode": "automatic", "background": True})
        self.assertIn(
            "while ShortsScheduler is open", automation.headline(background, True, False, "Stopped")
        )
        self.assertIn("including while", automation.headline(background, True, False, "Running"))
        review = profile(settings={"mode": "review", "background": False})
        self.assertIn("wait for your review", automation.headline(review, True, False, None))

    def test_runs_when_always_reports_service(self):
        for service in (None, "Stopped", "Running"):
            for background in (False, True):
                p = profile(settings={"mode": "automatic", "background": background})
                self.assertIn("Background service:", automation.runs_when(p, service))


class LabelTests(unittest.TestCase):
    def test_no_mnemonic_ampersands_in_ui_strings(self):
        # Qt turns "A & B" in button/checkbox text into an underlined shortcut ("A _B").
        pattern = re.compile(r"(QPushButton|QCheckBox|setText)\(\s*\"[^\"]*[^&]&[^&][^\"]*\"")
        for module in UI.glob("*.py"):
            self.assertEqual(pattern.findall(module.read_text("utf-8")), [], module.name)


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app, self.window = build(Path(self.temp.name))
        self.window.show()
        self.addCleanup(self.close)

    def close(self):
        self.window.close()
        for c in (self.window.videos, self.window.accounts, self.window.uploads):
            settle(self.app, c)
        self.window.deleteLater()
        self.app.processEvents()

    def test_actions_are_not_full_width_bars(self):
        self.window.resize(1180, 780)
        for page in PAGES:
            self.window.navigate(page.key)
            self.app.processEvents()
            for button in self.window.stack.currentWidget().findChildren(QPushButton):
                if button.isVisible() and button.objectName() not in ("Nav", "Disclosure"):
                    with self.subTest(page=page.key, button=button.text()):
                        self.assertLess(button.width(), button.sizeHint().width() + 120)

    def test_no_analytics_placeholder_and_no_unimplemented_pages(self):
        self.assertNotIn("analytics", self.window.buttons)
        texts = [b.text() for b in self.window.findChildren(QAbstractButton)]
        self.assertNotIn("Analytics", texts)

    def test_sidebar_shows_global_publishing_state(self):
        self.assertIn("Auto uploading", self.window.automatic.state.text())
        self.assertTrue(self.window.automatic.state.isVisible())

    def test_stop_hidden_without_active_upload(self):
        self.window.navigate("dashboard")
        self.app.processEvents()
        self.assertFalse(self.window.automatic.stop.isVisible())

    def test_editor_header_keeps_only_back_and_import(self):
        v = self.window.videos
        self.window.show_upload_history()
        self.app.processEvents()
        self.assertTrue(v.back_button.isVisible())
        self.assertTrue(v.import_button.isVisible())
        for hidden in (v.reload_button, v.history_button):
            self.assertFalse(hidden.isVisible(), hidden.text())
        self.assertIsNone(v.recurring_button)  # recurring queues are created from Upload Queue
        self.assertLess(v.back_button.y(), v.card.y())
        v.show_library()
        self.app.processEvents()
        self.assertTrue(v.reload_button.isVisible())
        self.assertFalse(v.back_button.isVisible())

    def test_sidebar_never_stuck_checking_after_failed_load(self):
        c = self.window.automatic
        c.data = {}
        c.receive({"error": "The library could not be accessed."})
        self.assertIn("Status unavailable", c.state.text())
        self.assertNotIn("Checking", c.button.text())

    def test_readiness_uses_plain_wording_with_limits_in_tooltip(self):
        from shortspublisher.readiness import LIMITATIONS
        from shortspublisher.ui.videos import READINESS_LABELS

        self.assertEqual(
            READINESS_LABELS["Passes implemented checks"],
            "Ready for YouTube Shorts · local checks passed",
        )
        self.assertTrue(LIMITATIONS)

    def test_tidy_groups_adjacent_buttons_and_keeps_order(self):
        from PySide6.QtWidgets import QVBoxLayout, QWidget

        root = QWidget()
        layout = QVBoxLayout(root)
        buttons = [QPushButton(name) for name in ("One", "Two", "Three")]
        for button in buttons:
            layout.addWidget(button)
        buttons[1].hide()
        tidy_buttons(root)
        row = layout.itemAt(0).widget()
        self.assertIsInstance(row.layout(), FlowLayout)
        self.assertEqual([row.layout().itemAt(i).widget() for i in range(3)], buttons)
        self.assertTrue(buttons[1].isHidden())
        root.deleteLater()


class SetupStepTests(unittest.TestCase):
    def test_step_indicator_and_primary_action(self):
        import test_automation as fixtures

        fixtures.AutomationTests.setUpClass()
        self.addCleanup(fixtures.AutomationTests.tearDownClass)
        f = fixtures.AutomationTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        app, window = build(
            f.u.root, account_service=f.u.accounts, upload_service=f.u.service, schedule_service=f.s
        )
        editor = automation.ProfileEditor(window.automation)
        self.addCleanup(editor.deleteLater)
        self.assertTrue(editor.progress.text().startswith("Step 1 of 4"))
        self.assertIn("Next: Video details", editor.progress.text())
        self.assertEqual(editor.next.objectName(), "Primary")
        self.assertNotEqual(editor.review.objectName(), "Primary")
        row = editor.days[0].parentWidget()
        self.assertIsInstance(row.layout(), FlowLayout)
        self.assertTrue(all(day.parentWidget() is row for day in editor.days))
        self.assertEqual(editor.days[0].text(), "Mon")
        self.assertEqual(editor.days[6].accessibleName(), "Sunday")
        editor.steps.setCurrentIndex(3)
        self.assertIn("Last step", editor.progress.text())
        self.assertEqual(editor.review.objectName(), "Primary")
        self.assertFalse(editor.next.isEnabled())
        editor.dirty = False
        editor.reject()
        for c in (
            window.automation,
            window.recurring,
            window.watching,
            window.automatic,
            window.background,
            window.tiktok,
            window.dashboard,
        ):
            c.stop_for_quit()
        window.close()
        for c in (window.videos, window.accounts, window.uploads, window.schedules):
            settle(app, c)
        window.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
