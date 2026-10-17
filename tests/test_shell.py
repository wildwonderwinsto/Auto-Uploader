import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton
from support import settle

from shortspublisher.app import build
from shortspublisher.diagnostics import configure_logging
from shortspublisher.ui.window import PAGES


class ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.app, window = build(Path(cls.temp.name))
        window.deleteLater()

    def setUp(self):
        self.app, self.window = build(Path(self.temp.name))
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        settle(self.app, self.window.videos)
        settle(self.app, self.window.accounts)
        settle(self.app, self.window.uploads)
        settle(self.app, self.window.schedules)
        settle(self.app, self.window.background)
        self.window.deleteLater()
        self.app.processEvents()

    def test_every_destination_and_selection(self):
        for page in PAGES:
            QTest.mouseClick(self.window.buttons[page.key], Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual(self.window.stack.currentWidget().objectName(), page.key)
            self.assertEqual(
                [key for key, button in self.window.buttons.items() if button.isChecked()],
                [page.key],
            )
            labels = self.window.stack.currentWidget().findChildren(QLabel)
            self.assertIn(page.title, [widget.text() for widget in labels])
            if page.key not in (
                "videos",
                "accounts",
                "queue",
                "calendar",
                "dashboard",
                "settings",
                "automation",
            ):
                self.assertIn("Not implemented yet", [widget.text() for widget in labels])
            settle(self.app, self.window.videos)

    def test_dashboard_action(self):
        button = next(
            button
            for button in self.window.stack.currentWidget().findChildren(QPushButton)
            if button.text().startswith("Prepare a video")
        )
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.stack.currentWidget().objectName(), "videos")

    def test_keyboard_navigation(self):
        button = self.window.buttons["accounts"]
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
        self.assertEqual(self.window.stack.currentWidget().objectName(), "accounts")

    def test_invalid_route_preserves_selection(self):
        with self.assertRaises(ValueError):
            self.window.navigate("unknown")
        self.assertEqual(self.window.stack.currentWidget().objectName(), "dashboard")

    def test_resize_render_and_close(self):
        for width, height in ((760, 560), (1180, 780), (1600, 1000)):
            self.window.resize(width, height)
            for page in PAGES:
                self.window.navigate(page.key)
                self.app.processEvents()
                scroll = self.window.stack.currentWidget()
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                self.assertFalse(self.window.grab().isNull())
                for button in self.window.buttons.values():
                    self.assertTrue(button.isVisible())
                    self.assertGreaterEqual(button.width(), button.sizeHint().width())
        self.assertIn("QPushButton#Nav:checked", self.app.styleSheet())
        settle(self.app, self.window.videos)
        settle(self.app, self.window.accounts)
        settle(self.app, self.window.uploads)
        settle(self.app, self.window.schedules)
        settle(self.app, self.window.background)
        self.assertTrue(self.window.close())
        self.assertFalse(self.window.isVisible())

    def test_real_entry_point_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {
                key: value
                for key, value in os.environ.items()
                if not any(word in key.upper() for word in ("GOOGLE", "YOUTUBE", "TOKEN"))
            }
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "shortspublisher",
                    "--smoke-test",
                    "--log-dir",
                    directory,
                    "--data-dir",
                    directory,
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            log = (Path(directory) / "application.log").read_text()
            self.assertIn("Starting desktop shell", log)
            self.assertIn("Desktop closed with code 0", log)


class LoggingTests(unittest.TestCase):
    def tearDown(self):
        import logging

        logger = logging.getLogger("shortspublisher")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

    def test_reconfiguration_does_not_duplicate_records(self):
        with tempfile.TemporaryDirectory() as directory:
            configure_logging(Path(directory))
            logger = configure_logging(Path(directory))
            logger.info("test-record")
            self.assertEqual(
                (Path(directory) / "application.log").read_text().count("test-record"), 1
            )

    def test_unwritable_log_location_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "file"
            file.touch()
            logger = configure_logging(file / "logs")
            self.assertEqual(len(logger.handlers), 1)
