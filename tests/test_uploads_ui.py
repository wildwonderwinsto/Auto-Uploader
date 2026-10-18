"""Real Qt workers and persistence with a transport that cannot reach Google."""

import os
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import test_uploads as upload_fixtures
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QPlainTextEdit
from support import settle

from shortspublisher.app import build
from shortspublisher.auth import AccountError
from shortspublisher.ui.uploads import UploadConfirmation
from shortspublisher.upload_http import UploadReply


class UploadUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        upload_fixtures.UploadServiceTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        upload_fixtures.UploadServiceTests.doClassCleanups()

    def setUp(self):
        self.fixture = upload_fixtures.UploadServiceTests()
        self.fixture.setUp()
        f = self.fixture
        self.app, self.window = build(f.root, account_service=f.accounts, upload_service=f.service)
        self.window.show()
        self.window.navigate("accounts")
        settle(self.app, self.window.accounts)
        self.window.navigate("videos")
        settle(self.app, self.window.videos)
        settle(self.app, self.window.uploads)
        self.window.videos.list.setCurrentRow(0)
        settle(self.app, self.window.videos)
        self.window.videos.tabs.setCurrentIndex(3)
        self.page = self.window.uploads

    def tearDown(self):
        self.window.close()
        for page in (self.window.videos, self.window.accounts, self.page):
            settle(self.app, page)
        self.window.deleteLater()
        self.app.processEvents()
        self.fixture.doCleanups()

    def upload(self, accepted=True):
        with patch.object(
            UploadConfirmation,
            "exec",
            return_value=QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected,
        ):
            self.page.upload_button.click()
            settle(self.app, self.page)

    def test_explicit_confirmation_cancel_sends_nothing(self):
        self.assertTrue(self.page.upload_button.isEnabled(), self.page.reason.text())
        self.upload(False)
        self.assertEqual(self.fixture.http.initiations, 0)
        self.assertEqual(self.page.selected().state, "Cancelled")

    def test_confirmation_exact_snapshot_private_and_channel(self):
        attempt = self.fixture.prepare()
        dialog = UploadConfirmation(attempt, self.window)
        content = "\n".join(box.toPlainText() for box in dialog.findChildren(QPlainTextEdit))
        self.assertIn(f"Size: {attempt.snapshot['size']:,} bytes", content)
        self.assertIn(attempt.snapshot["path"], content)
        for value in (
            attempt.channel_id,
            "PRIVATE",
            attempt.snapshot["sha256"],
            "Made for Kids: No",
        ):
            self.assertIn(value, content)
        dialog.deleteLater()

    def test_completion_progress_history_and_no_duplicate_click(self):
        self.upload()
        self.assertEqual(self.page.selected().state, "Uploaded Private")
        self.assertEqual(self.page.progress.value(), 100)
        self.assertIn("private", self.page.details.toPlainText())
        self.assertTrue(self.page.link_button.isEnabled())
        self.assertFalse(self.page.upload_button.isEnabled())
        # The block explains itself instead of a generic "existing attempt" message.
        self.assertIn("already been uploaded", self.page.reason.text())
        self.assertIn(" · Private · ", self.page.history.item(0).text())
        self.page.upload_button.click()
        self.assertEqual(self.fixture.http.initiations, 1)

    def test_no_permission_and_dirty_review_disable(self):
        view = self.page.account
        view = replace(view, upload_allowed=False)
        self.page.account_changed(view)
        self.assertFalse(self.page.upload_button.isEnabled())
        self.assertIn("Authorize", self.page.reason.text())
        view = replace(view, upload_allowed=True)
        self.page.context(self.fixture.video.id, 1, False)
        self.assertIn("complete review", self.page.reason.text())

    def test_stop_navigation_and_resume(self):
        self.fixture.http.after_chunk = lambda: self.page.worker.cancel.set()
        self.upload()
        self.assertEqual(self.page.selected().state, "Paused")
        self.assertGreater(self.page.progress.value(), 0)
        self.assertLess(self.page.progress.value(), 100)
        self.window.navigate("dashboard")
        self.assertEqual(self.window.stack.currentWidget().objectName(), "dashboard")
        self.fixture.http.after_chunk = None
        self.page.start_selected("resume")
        settle(self.app, self.page)
        self.assertEqual(self.page.selected().state, "Uploaded Private")
        self.assertEqual(self.fixture.http.initiations, 1)

    def test_expired_session_reconciliation_and_no_automatic_retry(self):
        self.fixture.http.query_replies = [UploadReply(404)]
        self.upload()
        self.assertEqual(self.page.selected().state, "Needs Reconciliation")
        self.assertFalse(self.page.upload_button.isEnabled())
        self.assertFalse(self.page.link_button.isEnabled())
        self.page.start("history")
        settle(self.app, self.page)
        self.assertEqual(self.fixture.http.initiations, 1)

    def test_history_visible_after_library_removal(self):
        self.upload()
        self.fixture.library.remove(self.fixture.video.id)
        self.window.videos.start("load")
        settle(self.app, self.window.videos)
        self.assertFalse(self.window.videos.card.isVisible())
        self.window.show_upload_history()
        settle(self.app, self.window.videos)
        self.assertTrue(self.window.videos.card.isVisible())
        self.assertEqual(self.window.videos.tabs.currentIndex(), 3)
        self.assertIn("Uploaded Private", self.page.details.toPlainText())
        self.assertFalse(self.page.upload_button.isEnabled())

    def test_close_during_transfer_preserves_recovery(self):
        attempt = self.fixture.prepare()
        self.fixture.http.delay = 0.3
        self.page.start("history")
        settle(self.app, self.page)
        self.page.start("confirm", attempt.id)
        deadline = time.monotonic() + 3
        while not self.fixture.http.offsets:
            self.app.processEvents()
            QTest.qWait(5)
            self.assertLess(time.monotonic(), deadline)
        self.app.processEvents()
        self.assertEqual(self.page.selected().state, "Uploading")
        self.window.navigate("dashboard")
        self.assertFalse(self.window.close())
        settle(self.app, self.page)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.fixture.store.get(attempt.id).state, "Paused")

    def test_quit_during_transfer_waits_for_worker(self):
        attempt = self.fixture.prepare()
        self.fixture.http.delay = 0.3
        self.page.start("history")
        settle(self.app, self.page)
        self.page.start("confirm", attempt.id)
        event = QEvent(QEvent.Type.Quit)
        self.assertTrue(self.window.eventFilter(self.app, event))
        settle(self.app, self.page)
        self.assertFalse(self.window.isVisible())
        self.assertNotEqual(self.fixture.store.get(attempt.id).state, "Uploading")

    def test_scope_upgrade_failure_preserves_connection_ui(self):
        self.fixture.oauth.error = AccountError("Controlled denied consent", "denied")
        self.window.accounts.start("authorize_upload")
        settle(self.app, self.window.accounts)
        self.assertIsNotNone(self.fixture.repo.connection())
        self.assertIn("denied", self.window.accounts.feedback.text())
