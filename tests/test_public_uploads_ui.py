"""Public intent and confirmation through actual Qt workers, controlled transport."""

import unittest
from dataclasses import replace
from threading import Event
from unittest.mock import patch

import test_uploads_ui as fixtures
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit
from support import settle

from shortspublisher.ui.uploads import UploadConfirmation
from shortspublisher.upload_http import UploadReply


class PublicUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UploadUiTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.UploadUiTests.tearDownClass()

    def setUp(self):
        self.ui = fixtures.UploadUiTests()
        self.ui.setUp()
        self.addCleanup(self.ui.tearDown)
        self.page = self.ui.page
        self.f = self.ui.fixture
        self.f.http.privacy = "public"

    def publish(self, accepted=True):
        with patch.object(
            UploadConfirmation,
            "exec",
            return_value=QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected,
        ):
            self.page.public_button.click()
            settle(self.ui.app, self.page)

    def test_public_confirmation_exact_details(self):
        result = self.f.service.execute(
            "prepare_public", (self.f.video.id, 1, self.page.account.connection.channel.id), Event()
        )
        dialog = UploadConfirmation(result["confirmation"], self.ui.window)
        content = "\n".join(box.toPlainText() for box in dialog.findChildren(QPlainTextEdit))
        for expected in (
            "PUBLIC — publicly accessible",
            self.f.video.path,
            "Made for Kids",
            "Synthetic disclosure",
            "Size:",
            "Tags:",
        ):
            self.assertIn(expected, content)
        self.assertEqual(
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).text(),
            "Publish Publicly Now",
        )
        dialog.deleteLater()

    def test_public_cancel_no_remote_action(self):
        self.assertTrue(self.page.public_button.isEnabled(), self.page.public_reason.text())
        self.publish(False)
        self.assertEqual(self.f.http.initiations, 0)
        self.assertEqual(self.page.selected().state, "Cancelled")

    def test_public_button_requires_public_review(self):
        for privacy in ("private", "unlisted", ""):
            self.page.context(self.f.video.id, 1, True, privacy)
            self.assertFalse(self.page.public_button.isEnabled())
            self.assertTrue(self.page.upload_button.isEnabled())
        self.page.context(self.f.video.id, 1, False, "public")
        self.assertFalse(self.page.public_button.isEnabled())

    def test_public_requires_permission(self):
        self.page.account_changed(replace(self.page.account, upload_allowed=False))
        self.assertFalse(self.page.public_button.isEnabled())

    def test_verified_public_history(self):
        self.publish()
        self.assertEqual(self.page.selected().state, "Public Verified")
        self.assertEqual(self.f.http.verify_calls, 1)
        self.assertIn("public", self.page.details.toPlainText())
        self.assertFalse(self.page.public_button.isEnabled())
        self.assertTrue(self.page.link_button.isEnabled())

    def test_unavailable_verification_does_not_claim_public(self):
        self.f.http.verify_reply = UploadReply(503)
        self.publish()
        self.assertEqual(self.page.selected().state, "Transfer Complete")
        self.assertIn("unverified", self.page.details.toPlainText())
        self.assertFalse(self.page.public_button.isEnabled())

    def test_visibility_discrepancy(self):
        self.f.http.privacy = "private"
        self.publish()
        self.assertEqual(self.page.selected().state, "Needs Reconciliation")
        self.assertIn("CRITICAL", self.page.details.toPlainText())

    def test_public_stop_resume_keeps_intent(self):
        self.f.http.after_chunk = lambda: self.page.worker.cancel.set()
        self.publish()
        self.assertEqual(self.page.selected().state, "Paused")
        self.f.http.after_chunk = None
        self.page.start_selected("resume")
        settle(self.ui.app, self.page)
        self.assertEqual(self.page.selected().state, "Public Verified")
        self.assertEqual(self.f.http.initiations, 1)
