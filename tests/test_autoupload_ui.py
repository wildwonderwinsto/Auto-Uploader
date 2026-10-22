import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from support import settle, shutdown

from shortspublisher.app import build
from shortspublisher.cadence import Rule
from shortspublisher.ui.autoupload import AutoUploadEditor


class AutoUploadEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app, self.window = build(Path(self.temp.name))
        self.c = self.window.automation
        settle(self.app, self.c)
        self.c.folders = [{"id": "f1", "name": "Drafts", "path": "/tmp/Drafts", "removed": 0}]
        self.c.account = {"id": "UC1", "title": "My channel"}
        self.window.tiktok.data["connection"] = {
            "status": "Connected",
            "name": "me",
            "id": "ch-tt",
            "organization_id": "org",
            "verified_at": "2026-10-01T00:00:00+00:00",
        }
        self.window.tiktok.data["hosting"] = {
            "cloud_name": "demo",
            "retention": "delete_after_sent",
            "api_key": "k",
        }
        self.e = AutoUploadEditor(self.c, self.window.tiktok)
        self.addCleanup(self.e.deleteLater)

    def tearDown(self):
        self.e.dirty = False
        self.e.reject()
        shutdown(self.app, self.window)

    def fill(self):
        self.e.template.setText("{filename} #Shorts")
        self.e.description.setPlainText("Made in Blender.")
        self.e.tags.setText("cats, funny videos")
        self.e.kids.setCurrentIndex(self.e.kids.findData(False))
        self.e.synthetic.setCurrentIndex(self.e.synthetic.findData(False))
        self.e.ai.setCurrentIndex(self.e.ai.findData(False))
        self.e.days_preset.setCurrentIndex(1)  # weekdays
        self.e.frequency.setCurrentIndex(self.e.frequency.findData(3))

    def test_one_set_of_details_feeds_both_platforms(self):
        self.fill()
        youtube, tiktok = self.e.values(), self.e.tiktok_values()
        self.assertEqual(youtube["folder_id"], "f1")
        self.assertEqual(youtube["channel"], "UC1")
        settings = youtube["settings"]
        self.assertEqual(settings["title_template"], "{filename} #Shorts")
        self.assertEqual(settings["tags"], ["cats", "funny videos"])
        self.assertIs(settings["made_for_kids"], False)
        rule = Rule.decode(settings["rule"])
        self.assertEqual((rule.days, rule.hours), ((0, 1, 2, 3, 4), 3))
        self.assertEqual(tiktok["folder_id"], "f1")
        self.assertEqual(tiktok["settings"]["rule"], settings["rule"])
        self.assertEqual(
            tiktok["settings"]["caption_template"],
            "{filename}\n\nMade in Blender.\n\n#cats #funnyvideos",
        )
        self.assertIs(tiktok["settings"]["ai_generated"], False)
        self.assertIn("Weekdays every 3 hours", self.e.summary.text())

    def test_needs_a_platform_and_youtube_is_approved_before_tiktok(self):
        self.fill()
        for check in self.e.targets.values():
            check.setChecked(False)
        with patch.object(self.c, "start") as start:
            self.e.start_review()
            start.assert_not_called()
        self.assertIn("at least one platform", self.e.feedback.text())
        self.e.targets["youtube"].setChecked(True)
        self.e.targets["tiktok"].setChecked(True)
        with patch.object(self.c, "start") as start:
            self.e.start_review()
        start.assert_called_once_with("preview", self.e.values())
        with patch.object(self.window.tiktok, "review_automation") as review:
            self.e.approved()  # what the controller calls once YouTube is approved
        review.assert_called_once()
        self.assertEqual(review.call_args.args[0], self.e.tiktok_values())
        self.e.tiktok_done(False)
        self.assertIn("YouTube is on", self.e.feedback.text())

    def test_tiktok_only_skips_youtube_and_requires_ai_choice(self):
        self.e.targets["youtube"].setChecked(False)
        self.e.targets["tiktok"].setChecked(True)
        with (
            patch.object(self.c, "start") as start,
            patch.object(self.window.tiktok, "review_automation") as review,
        ):
            self.e.start_review()
            start.assert_not_called()
            review.assert_not_called()
        self.assertIn("AI-generated", self.e.feedback.text())

    def test_unavailable_platforms_cannot_be_chosen(self):
        self.window.tiktok.data["hosting"] = None
        editor = AutoUploadEditor(self.c, self.window.tiktok)
        self.addCleanup(editor.deleteLater)
        self.assertFalse(editor.targets["tiktok"].isEnabled())
        self.assertFalse(editor.targets["tiktok"].isChecked())
        editor.dirty = False
        editor.reject()

    def test_window_uses_the_all_platform_editor(self):
        editor = self.c.editor_factory(self.c, None)
        self.addCleanup(editor.deleteLater)
        self.assertIsInstance(editor, AutoUploadEditor)
        editor.dirty = False
        editor.reject()
