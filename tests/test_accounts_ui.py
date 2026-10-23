import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from account_support import CHANNEL, CONFIG, ControlledOAuth, ControlledYouTube, MemoryCredentials
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox
from support import settle, shutdown

from shortspublisher.accounts import AccountRepository, AccountService
from shortspublisher.app import build
from shortspublisher.auth import AccountError
from shortspublisher.library import Library


class AccountsUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.credentials = MemoryCredentials()
        self.oauth = ControlledOAuth()
        self.youtube = ControlledYouTube()
        self.service = AccountService(
            AccountRepository(Library(self.root / "library.sqlite3")),
            self.credentials,
            self.oauth,
            self.youtube,
        )
        self.app, self.window = build(self.root, account_service=self.service)
        self.window.show()
        self.window.navigate("accounts")
        self.page = self.window.accounts
        settle(self.app, self.page)
        # Close tests assume only the YouTube worker is busy; wait for the Buffer platforms'
        # startup checks.
        for controller in self.window.socials:
            settle(self.app, controller)

    def tearDown(self):
        shutdown(self.app, self.window)
        self.temp.cleanup()

    def configure(self):
        path = self.root / "client.json"
        path.write_text(json.dumps(CONFIG.document()))
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "JSON")):
            self.page.configure_button.click()
        settle(self.app, self.page)
        self.assertTrue(self.page.view.configured)

    def connect(self):
        self.configure()
        self.page.connect_button.click()
        settle(self.app, self.page)
        self.assertEqual(self.page.status.text(), "Choose channel")
        self.page.confirm_button.click()
        settle(self.app, self.page)
        self.assertTrue(self.page.status.text().startswith("Connected"))

    def test_disconnected_guidance_and_unconfigured_connect(self):
        self.assertEqual(self.page.status.text(), "Not connected")
        self.page.connect_button.click()
        settle(self.app, self.page)
        self.assertIn("JSON", self.page.feedback.text())
        self.assertFalse(self.credentials.values)

    def test_config_picker_cancel_and_invalid_json(self):
        with patch.object(QFileDialog, "getOpenFileName", return_value=("", "")):
            self.page.configure_button.click()
        self.assertIsNone(self.page.worker)
        path = self.root / "bad.json"
        path.write_text("{}")
        self.page.start("configure", path)
        settle(self.app, self.page)
        self.assertFalse(self.page.view.configured)
        self.assertIn("valid Google", self.page.feedback.text())

    def test_actual_storage_confirm_refresh_and_stale_status(self):
        self.connect()
        self.assertIn(CHANNEL.id, self.page.technical.text())
        self.assertNotIn(CHANNEL.id, self.page.details.text())
        self.assertIn(CHANNEL.handle, self.page.details.text())
        self.page.refresh_button.click()
        settle(self.app, self.page)
        self.assertEqual(self.youtube.calls, 2)
        # A stale verification checks again by itself; no "Check Again" click is needed.
        self.page.expire_verification()
        settle(self.app, self.page)
        self.assertEqual(self.youtube.calls, 3)
        self.assertTrue(self.page.status.text().startswith("Connected"))
        # ...but never while an upload is due, because uploads need the account first.
        self.page.hold_refresh = lambda: True
        self.page.expire_verification()
        self.assertIsNone(self.page.worker)
        self.assertEqual(self.youtube.calls, 3)
        self.assertEqual(self.page.status.text(), "Checking…")
        self.assertIn("shortly", self.page.feedback.text())

    def test_connect_cancellation_responsive_repeated_clicks(self):
        self.configure()
        self.oauth.wait = True
        self.page.connect_button.click()
        self.assertTrue(self.oauth.entered.wait(2))
        worker = self.page.worker
        self.page.start("connect")
        self.assertIs(self.page.worker, worker)
        self.window.navigate("settings")
        self.assertEqual(self.window.stack.currentWidget().objectName(), "settings")
        self.page.cancel_button.click()
        settle(self.app, self.page)
        self.assertEqual(self.page.status.text(), "Not connected")
        self.assertFalse(self.credentials.values)
        self.assertFalse(self.page.progress.isVisible())

    def test_shutdown_during_authorization(self):
        self.configure()
        self.oauth.wait = True
        self.page.connect_button.click()
        self.assertTrue(self.oauth.entered.wait(2))
        self.assertFalse(self.window.close())
        settle(self.app, self.page)
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.credentials.values)

    def test_application_quit_cancels_worker(self):
        self.configure()
        self.oauth.wait = True
        self.page.connect_button.click()
        self.assertTrue(self.oauth.entered.wait(2))
        event = QEvent(QEvent.Type.Quit)
        self.assertTrue(self.window.eventFilter(self.app, event))
        settle(self.app, self.page)
        self.assertFalse(self.window.isVisible())

    def test_failure_and_offline_cached_identity(self):
        self.connect()
        self.youtube.error = AccountError("Controlled offline test", "offline")
        self.page.refresh_button.click()
        settle(self.app, self.page)
        self.assertEqual(self.page.status.text(), "Offline")
        self.assertIn(CHANNEL.id, self.page.technical.text())
        self.assertNotIn(CHANNEL.id, self.page.details.text())
        self.assertTrue(self.credentials.values)

    def test_disconnect_confirmation_cancel_and_confirm(self):
        self.connect()
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Cancel):
            self.page.disconnect_button.click()
        self.assertTrue(self.credentials.values)
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes):
            self.page.disconnect_button.click()
        settle(self.app, self.page)
        self.assertEqual(self.page.status.text(), "Not connected")
        self.assertFalse(self.credentials.values)
        self.assertFalse(self.page.details.isVisible())

    def test_pending_channel_confirmation_cancel(self):
        self.configure()
        self.page.connect_button.click()
        settle(self.app, self.page)
        self.page.cancel_button.click()
        settle(self.app, self.page)
        self.assertIsNone(self.service.pending)
        self.assertFalse(self.credentials.values)

    def test_small_normal_layout_and_keyboard(self):
        self.connect()
        for width, height in ((760, 560), (1180, 780)):
            self.window.resize(width, height)
            self.app.processEvents()
            self.assertEqual(self.page.horizontalScrollBar().maximum(), 0)
            self.page.ensureWidgetVisible(self.page.disconnect_button)
            self.assertTrue(self.page.disconnect_button.isVisible())
            self.page.refresh_button.setFocus()
            QTest.keyClick(
                self.page.refresh_button,
                __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Key.Key_Space,
            )
            settle(self.app, self.page)
