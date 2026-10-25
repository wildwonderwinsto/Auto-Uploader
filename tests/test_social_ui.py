"""Instagram and Facebook in the real window: isolated SQLite and files, controlled Buffer and
Cloudinary, no Keychain."""

import json
import os
import unittest
from contextlib import closing
from unittest.mock import patch

from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton
from support import settle
from test_social import Fixture

from shortspublisher.app import build
from shortspublisher.buffer import AUTH_URL
from shortspublisher.cadence import Rule
from shortspublisher.ui.social import (
    AddBufferAccount,
    AddCloudinaryAccount,
    ChooseAccounts,
    ConnectDialog,
)


def answer(text):
    """Make the next message box report that its button with this text was clicked."""
    return patch.object(
        QMessageBox,
        "clickedButton",
        lambda box: next(b for b in box.buttons() if b.text() == text),
    )


class SocialUiTests(Fixture):
    def setUp(self):
        super().setUp()
        self.app, self.w = build(self.library.path.parent)
        for controller, service in zip(
            self.w.socials, (self.tiktok, self.instagram, self.facebook), strict=True
        ):
            controller.service = service
        self.w.show()
        for controller in self.w.socials:
            controller.activate()
            settle(self.app, controller)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for controller in (
            *self.w.socials,
            self.w.automation,
            self.w.recurring,
            self.w.watching,
            self.w.automatic,
            self.w.background,
            self.w.dashboard,
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
        for controller in self.w.socials:
            controller.run(controller.snapshot, controller.render)
            settle(self.app, controller)

    def connect_all(self):
        self.sign_in(self.tiktok)
        for service in (self.instagram, self.facebook):
            key, channels = service.reuse_sign_in()
            service.select_channel(key, channels[0])
        self.host()
        self.reload()

    def test_accounts_show_real_instagram_and_facebook_cards(self):
        self.w.navigate("accounts")
        for controller in (self.w.instagram, self.w.facebook):
            self.assertIn(controller.card, self.w.accounts.grid.cards)
        self.assertEqual(self.w.instagram.connect_button.text(), "Connect Instagram via Buffer…")
        self.assertEqual(len(self.w.accounts.soon.cards), 1)  # only Snapchat is still coming
        self.assertEqual(self.w.instagram.card_status.text(), "Not connected")

    def choose(self, controller, decide=lambda dialog: QDialog.DialogCode.Accepted):
        """Connect from a card; `decide` stands in for the person in the chooser."""
        seen = []

        def run(dialog):
            seen.append(dialog)
            return decide(dialog)

        with patch.object(ChooseAccounts, "exec", run):
            controller.connect_account()
            for _ in range(3):
                settle(self.app, controller)
        return seen[0] if seen else None

    def test_one_click_connects_every_platform_with_the_existing_sign_in(self):
        self.sign_in(self.tiktok)
        self.host()
        self.reload()
        with patch("shortspublisher.ui.social.open_page") as opened:
            dialog = self.choose(self.w.instagram)
        opened.assert_not_called()  # no browser: the TikTok sign-in is reused
        # TikTok already works and wasn't clicked, so it isn't reconnected.
        self.assertEqual([s.name for s, c in dialog.picks()], ["Instagram", "Facebook"])
        for _ in range(3):
            for controller in self.w.socials:
                settle(self.app, controller)
        key = self.tiktok.connection()["key"]
        for service in (self.instagram, self.facebook):
            self.assertEqual(service.connection()["key"], key)
        self.assertEqual(self.w.instagram.card_status.text(), "Connected")
        self.assertEqual(self.w.facebook.card_status.text(), "Connected")
        self.assertIn(
            "Connected Instagram (@insta), Facebook (My Page)",
            self.w.instagram.card_feedback.text(),
        )

    def test_chooser_preselects_what_is_missing_and_leaves_groups_out(self):
        self.sign_in(self.tiktok)
        self.reload()
        dialog = self.choose(self.w.tiktok, lambda d: QDialog.DialogCode.Rejected)
        rows = {social.key: (check, choice) for social, check, choice in dialog.rows}
        self.assertTrue(rows["tiktok"][0].isChecked())  # the platform being connected
        self.assertTrue(rows["instagram"][0].isChecked())  # not connected yet
        facebook = rows["facebook"][1]
        self.assertEqual([facebook.itemText(i) for i in range(facebook.count())], ["My Page"])

    def test_choosing_nothing_connects_nothing_and_keeps_the_sign_in(self):
        key, _ = self.sign_in(self.tiktok)
        self.reload()
        self.choose(self.w.instagram, lambda d: QDialog.DialogCode.Rejected)
        self.assertIsNone(self.instagram.connection())
        self.assertIn(key, self.secrets.values)
        self.assertIn("Nothing was connected", self.w.instagram.card_feedback.text())

    def test_different_buffer_account_starts_a_fresh_sign_in(self):
        self.sign_in(self.tiktok)
        self.reload()

        def different(dialog):
            dialog.different()
            return QDialog.DialogCode.Rejected

        with patch.object(self.w.instagram, "sign_in") as fresh:
            self.choose(self.w.instagram, different)
        fresh.assert_called_once()

    def test_without_a_sign_in_connect_goes_straight_to_buffer(self):
        with (
            patch("shortspublisher.ui.social.open_page") as opened,
            patch.object(ConnectDialog, "exec", return_value=QDialog.DialogCode.Rejected),
        ):
            self.w.instagram.connect_account()
            settle(self.app, self.w.instagram)
        self.assertEqual(opened.call_count, 1)
        self.assertTrue(opened.call_args.args[0].startswith(AUTH_URL))
        self.assertIn("cancelled", self.w.instagram.card_feedback.text())

    def test_every_setup_step_opens_its_page(self):
        opened = []
        sections = (self.w.accounts.setup_guide, self.w.tiktok.client_setup, self.w.tiktok.hosting)
        with patch("shortspublisher.ui.components.open_page", opened.append):
            for section in sections:
                for button in section.findChildren(QPushButton):
                    if button.toolTip().startswith("https://"):
                        button.click()
        self.assertEqual(
            opened,
            [
                "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
                "https://console.cloud.google.com/auth/audience",
                "https://console.cloud.google.com/auth/clients",
                "https://account.buffer.com/channels",
                "https://publish.buffer.com/settings/api",
                "https://cloudinary.com/users/register_free",
                "https://console.cloudinary.com/app/settings/api-keys",
            ],
        )

    def test_open_setup_goes_to_the_section_it_needs(self):
        self.w.navigate("accounts")
        self.w.instagram.open_setup("hosting")
        self.assertIs(self.w.stack.currentWidget(), self.w.background)
        self.w.navigate("accounts")
        self.w.accounts.setup_requested.emit()
        self.assertIs(self.w.stack.currentWidget(), self.w.background)

    def test_facebook_shows_the_page_name(self):
        self.connect_all()
        self.assertIn("My Page · through Buffer", self.w.facebook.card_account.text())
        self.assertNotIn("@", self.w.facebook.card_account.text())

    def test_shared_settings_reach_every_platform(self):
        self.sign_in(self.tiktok)
        self.reload()
        self.assertIsNone(self.w.instagram.data["hosting"])
        self.w.tiktok.cloud_name.setText("demo")
        self.w.tiktok.cloud_key.setText("123456789012345")
        self.w.tiktok.cloud_secret.setText("cloud-secret")
        self.w.tiktok.hosting_consent.setChecked(True)
        self.w.tiktok.save_hosting()
        for _ in range(3):
            for controller in self.w.socials:
                settle(self.app, controller)
        self.assertIsNotNone(self.w.instagram.data["hosting"])
        self.assertIsNotNone(self.w.facebook.data["hosting"])

    def settle_all(self):
        for _ in range(3):
            for controller in self.w.socials:
                settle(self.app, controller)

    def rows(self, layout):
        return [layout.itemAt(i).widget() for i in range(layout.count())]

    def labels(self, widget):
        return [label.text() for label in widget.findChildren(QLabel)]

    def test_add_cloudinary_account_is_a_guided_window(self):
        self.host()
        self.reload()
        tiktok = self.w.tiktok
        self.assertEqual(len(self.rows(tiktok.host_rows)), 1)

        def paste(dialog):
            dialog.value.setText("not a variable")
            dialog.check()  # stays open and explains what to paste
            self.assertTrue(dialog.problem.isVisibleTo(dialog))
            dialog.value.setText("cloudinary://123456789012345:cloud-secret@spare")
            dialog.check()
            return dialog.result()

        with patch.object(AddCloudinaryAccount, "exec", paste):
            tiktok.add_host()
            self.settle_all()
        rows = self.rows(tiktok.host_rows)
        self.assertEqual(len(rows), 2)
        self.assertIn("spare", self.labels(rows[1]))
        self.assertIn("Backup", self.labels(rows[1]))
        self.assertEqual(len(self.w.instagram.data["hosts"]), 2)  # shared by every platform

    def test_add_buffer_account_adds_then_signs_that_account_in(self):
        self.reload()
        tiktok = self.w.tiktok
        self.assertEqual(len(self.rows(tiktok.buffer_rows)), 1)

        def name(dialog):
            dialog.name.setText("Second")
            return QDialog.DialogCode.Accepted

        with (
            patch.object(AddBufferAccount, "exec", name),
            patch.object(tiktok, "sign_in") as signed,
        ):
            tiktok.add_buffer_account()
            self.settle_all()
        account = self.tiktok.buffer_accounts()[1]
        self.assertEqual(account["label"], "Second")
        signed.assert_called_once_with(account["id"], backup=True)
        rows = self.rows(tiktok.buffer_rows)
        self.assertEqual(len(rows), 2)
        self.assertIn("Not signed in · sign in to use it as a backup", self.labels(rows[1]))

    def test_paused_auto_uploading_warns_where_uploads_are_planned(self):
        self.sign_in(self.tiktok)
        self.host()
        with closing(self.library.connect()) as db, db:
            db.execute(
                "INSERT INTO tiktok_profiles VALUES ('pr',1,'Clips','f','ch-tt','My TikTok',?,"
                "'{}',1,0,0,'','t','t')",
                (
                    json.dumps(
                        {
                            "rule": Rule("UTC", tuple(range(7)), "09:00", "21:00", 2).encode(),
                            "caption_template": "{filename}",
                            "ai_generated": False,
                            "daily_cap": 3,
                            "pattern": "*",
                        }
                    ),
                ),
            )
        self.reload()
        automatic = self.w.automatic
        automatic.data = {"paused": 1, "jobs": [], "executions": []}
        automatic.render_switch()
        self.assertEqual(automatic.light.text(), "Off")
        self.assertEqual(automatic.rail.objectName(), "RailStatusWarn")
        self.assertEqual(automatic.button.text(), "Turn On")
        self.assertIn("1 watched folder", automatic.state.text())
        self.assertEqual(len(automatic.banners), 4)  # Dashboard, Schedule, Calendar, Auto Upload
        self.w.navigate("dashboard")
        banner, words = automatic.banners[0]
        self.assertTrue(banner.isVisible())
        self.assertIn("won't go out: 1 watched folder", words.text())
        automatic.data = {"paused": 0, "jobs": [], "executions": []}
        automatic.render_switch()
        self.assertEqual(automatic.button.text(), "Pause All")
        self.assertEqual(automatic.rail.objectName(), "RailStatus")
        self.assertFalse(banner.isVisible())

    def test_view_counts_reload_without_a_click(self):
        dashboard = self.w.dashboard
        self.assertTrue(dashboard.stats_timer.isActive())
        self.assertEqual(dashboard.stats_timer.interval(), 60_000)
        started = []
        with patch("shortspublisher.ui.dashboard.StatsWorker") as worker:
            worker.side_effect = lambda *a: started.append(a) or worker.return_value
            self.w.tiktok.data["insights"] = True  # a view-count source is ready
            dashboard.stats_timer.timeout.emit()  # the regular tick, no button
            dashboard.stats_worker = None
            dashboard.stats_timer.timeout.emit()  # a minute hasn't passed: not asked again
        self.assertEqual(len(started), 1)

    def test_regular_refreshes_leave_buttons_alone(self):
        self.w.navigate("automation")
        settle(self.app, self.w.automation)
        self.w.automation.start("load")
        self.assertIsNotNone(self.w.automation.worker)
        self.assertTrue(self.w.automation.new.isEnabled())  # no flicker during the refresh
        settle(self.app, self.w.automation)
        controller = self.w.tiktok
        controller.poll()
        self.assertIsNotNone(controller.worker)
        self.assertTrue(controller.connect_button.isEnabled())
        settle(self.app, controller)

    def test_settings_name_the_shared_buffer_setup(self):
        self.w.navigate("settings")
        self.assertEqual(self.w.tiktok.client_setup.heading.text(), "Buffer connection")
        self.assertIn("Instagram", self.w.tiktok.hosting_status.text())

    def import_in_page(self, source, name):
        path = self.root / name
        path.write_bytes(source.read_bytes())
        settle(self.app, self.w.videos)  # a busy page ignores new work
        self.w.videos.start("import", str(path))
        settle(self.app, self.w.videos)
        self.reload()
        return self.w.videos.selected

    def test_video_editor_pages_and_fits(self):
        self.connect_all()
        self.w.navigate("videos")
        self.import_in_page(self.reel, "reel.mp4")
        self.assertEqual(
            [self.w.videos.tabs.tabText(i) for i in (5, 6, 7)], ["TikTok", "Instagram", "Facebook"]
        )
        self.w.videos.choose_upload("platform", "instagram")
        self.assertEqual(self.w.videos.tabs.currentIndex(), 6)
        self.w.videos.choose_upload("platform", "facebook")
        self.assertEqual(self.w.videos.tabs.currentIndex(), 7)
        self.assertTrue(self.w.videos.platform_buttons["facebook"].isChecked())
        for key in ("instagram", "facebook"):
            self.assertEqual(self.w.videos.fits[key].text(), "Fits")
        self.import_in_page(self.small, "small.mp4")
        self.assertEqual(self.w.videos.fits["facebook"].text(), "Won't post")
        self.assertEqual(self.w.videos.fits["instagram"].text(), "Fits")

    def test_instagram_publish_from_its_page_and_dashboard(self):
        self.connect_all()
        video = self.video()
        controller = self.w.instagram
        controller.context(video.id)
        settle(self.app, controller)
        # Instagram's own setting sits with the shared details, like TikTok's.
        self.assertEqual(self.w.videos.editor.instagram_slot.indexOf(controller.ai_field), 1)
        self.assertEqual(self.w.videos.editor.facebook_slot.indexOf(self.w.facebook.ai_field), -1)
        controller.ai.setCurrentIndex(controller.ai.findData(False))
        controller.caption.setPlainText("Hello #reel")
        with patch.object(QMessageBox, "exec", return_value=0), answer("Post Publicly via Buffer"):
            controller.publish(None)
        settle(self.app, controller)
        settle(self.app, controller)
        payload = self.buffer.creates[-1]
        self.assertEqual(payload["metadata"]["instagram"]["type"], "reel")
        self.assertIn("Publishing", controller.video_history.item(0).text())
        self.assertIn("Hello #reel", controller.queue_list.item(0).text())
        self.w.navigate("dashboard")
        self.w.dashboard.refresh()
        self.assertEqual(self.w.dashboard.buffer_cards["instagram"].account.text(), "@insta")
        platforms = {e.platform for e in self.w.dashboard.entries}
        self.assertIn("instagram", platforms)

    def test_upload_step_sends_one_video_to_every_ticked_platform(self):
        self.connect_all()
        self.import_in_page(self.reel, "all.mp4")
        for controller in (self.w.tiktok, self.w.instagram):
            settle(self.app, controller)
            controller.ai.setCurrentIndex(controller.ai.findData(False))
        self.w.videos.go_step(3)
        upload = self.w.upload_all
        upload.render()
        # Nothing goes anywhere until the details are saved and marked as ready.
        self.assertFalse(any(check.isEnabled() for check, _s, _m in upload.rows.values()))
        self.assertEqual(upload.rows["tiktok"][1].text(), "Waiting for Review")
        self.assertTrue(upload.not_ready.isVisibleTo(upload))
        editor, videos = self.w.videos.editor, self.w.videos
        editor.title.setText("One video, every platform")
        editor.kids.setCurrentIndex(2)
        editor.synthetic.setCurrentIndex(1)
        editor.privacy.setCurrentIndex(1)
        editor.save_button.click()
        settle(self.app, videos)
        videos.tabs.setCurrentIndex(2)
        settle(self.app, videos)
        videos.review_button.click()
        settle(self.app, videos)
        videos.go_step(3)
        self.assertIs(self.w.videos.tabs.currentWidget(), upload)
        upload.render()
        self.assertFalse(upload.not_ready.isVisibleTo(upload))
        rows = upload.rows
        # YouTube isn't connected here: it says why and can't be ticked.
        self.assertFalse(rows["youtube"][0].isEnabled())
        for key in ("tiktok", "instagram", "facebook"):
            self.assertTrue(rows[key][0].isChecked(), rows[key][1].text())
        self.assertEqual(upload.go.text(), "Upload to 3 Platforms…")
        rows["facebook"][0].click()  # untick one: it stays unticked
        upload.render()
        self.assertFalse(rows["facebook"][0].isChecked())
        self.assertEqual(upload.go.text(), "Upload to 2 Platforms…")
        if path := os.environ.get("SHOT"):
            upload.resize(760, upload.sizeHint().height())
            self.app.processEvents()
            upload.grab().save(path)
        with patch.object(QMessageBox, "exec", return_value=0), answer("Upload to 2 Platforms"):
            upload.go.click()
        self.settle_all()
        sent = {p["channelId"] for p in self.buffer.creates}
        self.assertEqual(sent, {"ch-tt", "ch-ig"})
        self.assertIn("Already", rows["tiktok"][1].text())
        # One platform's own page is still there, with a way back.
        rows["instagram"][2].click()
        self.assertEqual(self.w.videos.tabs.currentIndex(), 6)
        self.w.videos.go_step(3)
        self.assertIs(self.w.videos.tabs.currentWidget(), upload)

    def test_facebook_publish_needs_no_ai_choice(self):
        self.connect_all()
        video = self.video()
        controller = self.w.facebook
        controller.context(video.id)
        settle(self.app, controller)
        self.assertIs(controller.ai.currentData(), False)
        shown = []
        with (
            patch.object(QMessageBox, "exec", lambda box: shown.append(box.text()) or 0),
            answer("Post Publicly via Buffer"),
        ):
            controller.publish(None)
        self.assertIn("PUBLICLY to Facebook My Page", shown[0])
        self.assertNotIn("AI-generated content:", shown[0])
        settle(self.app, controller)
        settle(self.app, controller)
        self.assertEqual(self.buffer.creates[-1]["metadata"], {"facebook": {"type": "reel"}})

    def test_publish_cancel_sends_nothing(self):
        self.connect_all()
        video = self.video()
        controller = self.w.facebook
        controller.context(video.id)
        settle(self.app, controller)
        with patch.object(QMessageBox, "exec", return_value=0), answer("Cancel"):
            controller.publish(None)
        settle(self.app, controller)
        self.assertEqual((self.buffer.creates, self.cloud.uploads), ([], 0))

    def test_auto_upload_offers_and_approves_each_platform_in_turn(self):
        from shortspublisher.ui.autoupload import AutoUploadEditor

        self.connect_all()
        automation = self.w.automation
        automation.folders = [{"id": "f1", "name": "Drafts", "path": "/tmp/Drafts", "removed": 0}]
        automation.account = {}
        editor = AutoUploadEditor(automation, self.w.tiktok)
        self.addCleanup(editor.deleteLater)
        for key in ("tiktok", "instagram", "facebook"):
            self.assertTrue(editor.targets[key].isEnabled(), key)
        self.assertFalse(editor.targets["youtube"].isEnabled())
        editor.targets["tiktok"].setChecked(False)
        editor.targets["instagram"].setChecked(True)
        editor.targets["facebook"].setChecked(True)
        editor.start_review()
        self.assertIn("For Instagram", editor.feedback.text())  # its own AI choice is needed
        editor.instagram_ai.setCurrentIndex(editor.instagram_ai.findData(True))
        calls = []
        for controller in self.w.socials:
            controller.review_automation = lambda values, done, parent, name=controller.name: (
                calls.append((name, values))
            )
        editor.start_review()
        self.assertEqual([c[0] for c in calls], ["Instagram"])
        self.assertIs(calls[0][1]["settings"]["ai_generated"], True)
        editor.platform_done(True)
        self.assertEqual([c[0] for c in calls], ["Instagram", "Facebook"])
        self.assertIs(calls[1][1]["settings"]["ai_generated"], False)  # nothing to send
        editor.platform_done(False)
        self.assertIn("Instagram is on. Facebook was not turned on", editor.feedback.text())
        editor.start_review()  # Instagram is never approved twice
        self.assertEqual([c[0] for c in calls], ["Instagram", "Facebook", "Facebook"])
        editor.dirty = False
        editor.approved_buffer.clear()
        editor.reject()

    def test_settings_mark_which_platforms_need_each_setup(self):
        def users(section):
            return {w.accessibleName() for w in section.findChildren(QLabel) if w.accessibleName()}

        self.assertEqual(users(self.w.accounts.setup_guide), {"Used by YouTube"})
        expected = {"Used by TikTok", "Used by Instagram", "Used by Facebook"}
        self.assertEqual(users(self.w.tiktok.client_setup), expected)
        self.assertEqual(users(self.w.tiktok.insights_setup), expected)

    def test_auto_upload_has_no_daily_limit_of_its_own(self):
        from PySide6.QtWidgets import QSpinBox

        from shortspublisher.ui.autoupload import AutoUploadEditor

        self.connect_all()
        automation = self.w.automation
        automation.folders = [{"id": "f1", "name": "Drafts", "path": "/tmp/Drafts", "removed": 0}]
        automation.account = {"id": "UC1", "title": "My channel"}
        automation.limit = 100  # the global value since schema 14
        editor = AutoUploadEditor(automation, self.w.tiktok)
        self.addCleanup(editor.deleteLater)
        spins = {s.accessibleName() or s.suffix() for s in editor.findChildren(QSpinBox)}
        self.assertFalse([s for s in spins if "per day" in s])
        self.assertEqual(editor.values()["settings"]["limit"], 100)
        self.assertEqual(
            {s.key: editor.platform_values(s)["settings"]["daily_cap"] for s in self.w.socials},
            {"tiktok": 25, "instagram": 50, "facebook": 35},  # each platform's own ceiling
        )
        editor.dirty = False
        editor.reject()

    def test_settings_offer_no_daily_upload_limit(self):
        self.w.navigate("settings")
        texts = [label.text() for label in self.w.background.findChildren(QLabel)]
        self.assertNotIn("Daily upload limit", texts)

    def test_automation_cards_for_each_platform(self):
        self.w.navigate("automation")
        self.assertEqual(self.w.automation.views, [])  # no empty card per platform
        for controller in self.w.socials:
            self.assertIn(f"No {controller.name} uploads yet", controller.queue_empty.text())

    def test_automation_is_never_active_without_hosting(self):
        from threading import Event

        from shortspublisher.cadence import Rule

        self.connect_all()
        folder = self.root / "watched"
        folder.mkdir()
        scanner = self.w.watching.service
        identity = scanner.add(
            scanner.preview(str(folder), Event()), consent=True, existing=False, quiet=1
        )
        settings = dict(
            caption_template="{filename}",
            rule=Rule("UTC", tuple(range(7)), "09:00", "21:00", 2).encode(),
            daily_cap=2,
            pattern="*.mp4",
            background=False,
        )
        proposal = self.facebook.preview(
            {"name": "FB", "folder_id": identity, "settings": settings}
        )
        self.facebook.approve(proposal, scanner, Event())
        self.w.automatic.data = {"paused": 0}
        self.reload()
        [view] = self.w.automation.views
        self.assertEqual(view.platforms, ("facebook",))
        self.assertTrue(view.lines[0][1].startswith("Active"))
        self.tiktok.remove_hosting()
        self.reload()
        [view] = self.w.automation.views
        self.assertTrue(
            view.lines[0][1].startswith("Not posting · set up video hosting in Settings")
        )

    def test_one_auto_upload_is_one_card_and_delete_stops_every_platform(self):
        from threading import Event

        self.connect_all()
        folder = self.root / "watched"
        folder.mkdir()
        scanner = self.w.watching.service
        identity = scanner.add(
            scanner.preview(str(folder), Event()), consent=True, existing=False, quiet=1
        )
        settings = dict(
            caption_template="{filename}",
            ai_generated=False,
            rule=Rule("UTC", tuple(range(7)), "09:00", "21:00", 2).encode(),
            pattern="*.mp4",
            background=False,
        )
        for service in (self.instagram, self.facebook):
            values = {
                "name": "Clips",
                "folder_id": identity,
                "settings": {**settings, "daily_cap": service.spec.daily_max},
            }
            service.approve(service.preview(values), scanner, Event())
        self.reload()
        [view] = self.w.automation.views  # made together: one auto upload, both logos
        self.assertEqual(view.platforms, ("instagram", "facebook"))
        self.assertEqual([p for p, _ in view.lines], ["instagram", "facebook"])
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.w.automation.act("delete", view.key)
        for controller in (*self.w.socials, self.w.watching):
            settle(self.app, controller)
        self.assertEqual(self.instagram.profiles(), [])
        self.assertEqual(self.facebook.profiles(), [])
        self.assertEqual(scanner.folders(), [])  # nothing else used the folder
        self.reload()
        self.assertEqual(self.w.automation.views, [])


if __name__ == "__main__":
    unittest.main()
