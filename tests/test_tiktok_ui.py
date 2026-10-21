"""TikTok UI in the real window: isolated SQLite and files, controlled Buffer and Cloudinary."""

import unittest
from threading import Event
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox
from support import settle
from test_tiktok import Fixture

from shortspublisher.app import build
from shortspublisher.buffer import pkce


class TikTokUiTests(Fixture):
    def setUp(self):
        super().setUp()
        self.app, self.w = build(self.library.path.parent)
        self.c = self.w.tiktok
        self.c.service = self.service
        self.w.show()
        self.c.activate()
        settle(self.app, self.c)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for controller in (
            self.c,
            self.w.automation,
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

    def reload(self):
        for controller in (self.w.videos, self.w.uploads):
            settle(self.app, controller)
        self.c.run(self.c.snapshot, self.c.render)
        settle(self.app, self.c)

    def test_accounts_card_beside_youtube_and_no_tokens_shown(self):
        self.w.navigate("accounts")
        settle(self.app, self.c)
        layout = self.w.accounts.content_layout
        self.assertEqual(layout.indexOf(self.c.card), layout.indexOf(self.w.accounts.card) + 1)
        self.assertEqual(self.c.card_status.text(), "Not connected")
        self.assertTrue(self.c.connect_button.isEnabled())  # client saved by the fixture
        self.assertEqual(self.c.connect_button.text(), "Connect TikTok via Buffer…")
        self.connect()
        self.reload()
        self.assertIn("@mytiktok", self.c.card_status.text())
        self.assertIn("through Buffer", self.c.card_status.text())
        shown = self.c.card_status.text() + self.c.card_detail.text()
        self.assertNotIn("access-", shown)
        self.assertNotIn("ch-tt", shown)  # IDs only under Account details

    def test_connect_disabled_without_client_configuration(self):
        with self.library.connect() as db:
            db.execute("DELETE FROM account_config WHERE platform='tiktok'")
        self.reload()
        self.assertFalse(self.c.connect_button.isEnabled())
        self.assertIn("Buffer app client", self.c.card_detail.text())

    def test_choose_channel_connects_single_tiktok_account(self):
        verifier, challenge = pkce()
        self.buffer.challenge = challenge
        key, channels = self.service.finish_connect("good-code", verifier)
        self.c.choose_channel((key, channels))
        settle(self.app, self.c)
        self.assertEqual(self.service.connection()["name"], "mytiktok")

    def test_no_tiktok_channel_discards_sign_in(self):
        verifier, challenge = pkce()
        self.buffer.challenge = challenge
        key, _ = self.service.finish_connect("good-code", verifier)
        self.c.choose_channel((key, []))
        settle(self.app, self.c)
        self.assertNotIn(key, self.secrets.values)
        self.assertIsNone(self.service.connection())
        self.assertIn("No usable TikTok channel", self.c.card_feedback.text())

    def test_hosting_requires_consent_in_settings(self):
        self.w.navigate("settings")
        self.c.cloud_name.setText("demo")
        self.c.cloud_key.setText("123456789012345")
        self.c.cloud_secret.setText("cloud-secret")
        self.c.save_hosting()
        settle(self.app, self.c)
        self.assertIsNone(self.service.hosting())
        self.assertIn("public", self.c.hosting_feedback.text().lower())
        self.c.cloud_secret.setText("cloud-secret")
        self.c.hosting_consent.setChecked(True)
        self.c.save_hosting()
        settle(self.app, self.c)
        settle(self.app, self.c)
        self.assertIn("Cloudinary · demo", self.c.hosting_status.text())
        self.assertEqual(self.c.cloud_secret.text(), "")
        self.assertEqual(self.cloud.uploads, 0)

    def test_pasted_cloudinary_variable_fills_fields_and_save_verifies(self):
        self.c.cloud_secret.setText("CLOUDINARY_URL=cloudinary://123456789012345:cloud-secret@demo")
        self.assertEqual(
            (self.c.cloud_name.text(), self.c.cloud_key.text(), self.c.cloud_secret.text()),
            ("demo", "123456789012345", "cloud-secret"),
        )
        self.c.hosting_consent.setChecked(True)
        self.c.save_hosting()
        settle(self.app, self.c)
        settle(self.app, self.c)
        self.assertEqual(self.service.hosting()["cloud_name"], "demo")
        self.assertIn("Cloudinary account OK", self.c.hosting_feedback.text())

    def test_errors_from_clicks_open_a_non_blocking_dialog(self):
        self.c.cloud_name.setText("demo")
        self.c.cloud_key.setText("123456789012345")
        self.c.cloud_secret.setText("wrong-secret-value")
        self.c.hosting_consent.setChecked(True)
        self.c.save_hosting()
        settle(self.app, self.c)
        self.assertIsNotNone(self.c.error_box)
        self.assertIn("rejected", self.c.error_box.text())
        self.assertEqual(self.c.cloud_secret.text(), "wrong-secret-value")  # kept for correction
        self.assertIsNone(self.service.hosting())
        self.c.error_box.close()

    def test_video_tab_publish_now_and_history(self):
        video = self.ready()
        self.reload()
        self.c.context(video.id)
        settle(self.app, self.c)
        self.assertEqual(self.w.videos.tabs.tabText(5), "TikTok")
        self.assertTrue(self.c.publish_button.isEnabledTo(self.c.video_panel))
        self.c.caption.setPlainText("Hello #fyp")
        self.c.ai.setCurrentIndex(2)  # explicit "No"
        with (
            patch.object(QMessageBox, "exec", lambda box: box.buttons()[-1].click() or 0),
            patch.object(
                QMessageBox,
                "clickedButton",
                lambda box: next(b for b in box.buttons() if "Publicly" in b.text()),
            ),
        ):
            self.c.publish(None)
        settle(self.app, self.c)
        settle(self.app, self.c)
        self.assertEqual(len(self.buffer.creates), 1)
        self.assertEqual(self.buffer.creates[0]["text"], "Hello #fyp")
        self.assertEqual(self.c.video_history.count(), 1)
        self.assertIn("Publishing", self.c.video_history.item(0).text())
        self.assertIn("TikTok", self.c.queue_list.item(0).text())

    def test_publish_confirmation_cancel_sends_nothing(self):
        video = self.ready()
        self.reload()
        self.c.context(video.id)
        settle(self.app, self.c)
        with (
            patch.object(QMessageBox, "exec", lambda box: 0),
            patch.object(QMessageBox, "clickedButton", lambda box: None),
        ):
            self.c.publish(None)
        settle(self.app, self.c)
        self.assertEqual(self.buffer.creates, [])
        self.assertEqual(self.cloud.uploads, 0)

    def test_publish_disabled_until_connected_and_hosted(self):
        video = self.video()
        self.reload()
        self.c.context(video.id)
        settle(self.app, self.c)
        self.assertFalse(self.c.publish_button.isEnabledTo(self.c.video_panel))
        self.assertIn("Accounts", self.c.video_account.text())

    def test_automation_section_and_queue_empty_state(self):
        self.w.navigate("automation")
        settle(self.app, self.c)
        self.assertTrue(self.c.new_automation.isVisible())
        self.assertEqual(self.c.auto_headline.text(), "No TikTok automations yet")
        self.assertEqual(self.w.automation.new.text(), "New YouTube Automation…")
        self.w.navigate("queue")
        settle(self.app, self.c)
        self.assertTrue(self.c.queue_empty.isVisible())
        self.assertFalse(self.c.queue_list.isVisible())

    def test_automation_headline_never_active_while_globally_paused(self):
        self.connect()
        self.host()
        folder = self.root / "watched"
        folder.mkdir()
        scanner = self.w.watching.service
        identity = scanner.add(
            scanner.preview(str(folder), Event()), consent=True, existing=False, quiet=1
        )
        from shortspublisher.cadence import Rule

        settings = dict(
            caption_template="{filename}",
            ai_generated=False,
            rule=Rule("UTC", tuple(range(7)), "09:00", "21:00", 2).encode(),
            daily_cap=2,
            pattern="*.mp4",
            background=False,
        )
        proposal = self.service.preview({"name": "TT", "folder_id": identity, "settings": settings})
        self.service.approve(proposal, scanner, Event())
        self.w.automatic.data = {"paused": 1}
        self.reload()
        self.assertIn("paused for the whole app", self.c.auto_headline.text())
        self.assertNotIn("Active", self.c.auto_headline.text())
        self.w.automatic.data = {"paused": 0}
        self.reload()
        self.assertTrue(self.c.auto_headline.text().startswith("Active"))
        self.assertTrue(self.c.auto_pause.isVisibleTo(self.c.automation))
        self.assertFalse(self.c.auto_resume.isVisibleTo(self.c.automation))


if __name__ == "__main__":
    unittest.main()
