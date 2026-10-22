import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QLabel
from support import shutdown

from shortspublisher.app import build
from shortspublisher.scheduling import Job
from shortspublisher.upload_store import Attempt


def iso(**delta):
    return (datetime.now(UTC) + timedelta(**delta)).isoformat(timespec="seconds")


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app, self.window = build(Path(self.temp.name))
        self.window.show()
        self.page = self.window.dashboard

    def tearDown(self):
        shutdown(self.app, self.window)

    def texts(self, widget):
        return " ".join(label.text() for label in widget.findChildren(QLabel))

    def test_empty_dashboard_explains_next_steps(self):
        self.page.refresh()
        self.assertEqual(self.page.upcoming_tile.value.text(), "0")
        self.assertIn("Nothing is scheduled", self.texts(self.page.coming))
        self.assertFalse(self.page.attention.isVisibleTo(self.window))
        self.assertIn("Connect an account", self.page.views_tile.note.text())
        self.assertFalse(self.page.refresh_stats.isVisibleTo(self.window))

    def test_all_platforms_in_one_timeline_with_views(self):
        done = Attempt(
            "a1",
            1,
            "UC",
            {"metadata": {"title": "Morning routine"}, "size": 10},
            "Public Verified",
            10,
            "vid1",
            "public",
            "processed",
            "",
            iso(days=-1),
            iso(days=-1),
            iso(days=-1),
            False,
        )
        failed = Attempt(
            "a2",
            2,
            "UC",
            {"metadata": {"title": "Night drive"}, "size": 10},
            "Interrupted",
            5,
            "",
            "public",
            "",
            "",
            iso(),
            iso(),
            "",
            False,
        )
        self.window.uploads.attempts = [done, failed]
        job = Job(
            "j1",
            3,
            "youtube",
            "UC",
            "Ch",
            "h",
            {"metadata": {"title": "Cat video"}},
            iso(hours=5),
            "UTC",
            iso(hours=5),
            0,
            "Scheduled",
            "",
            1,
            iso(),
            iso(),
            iso(),
            "",
            "",
            1,
        )
        self.window.automatic.data = {"jobs": [job], "executions": []}
        self.window.tiktok.data["posts"] = [
            {
                "state": "Scheduled in Buffer",
                "caption": "Cat video #cat",
                "due_at": iso(hours=6),
                "sent_at": None,
                "external_link": "",
                "buffer_post_id": "b",
                "video_id": 3,
                "error": "",
                "released": 0,
                "updated_at": iso(),
            },
            {
                "state": "Sent by Buffer",
                "caption": "Morning routine #fyp",
                "due_at": iso(days=-1),
                "sent_at": iso(days=-1),
                "external_link": "https://www.tiktok.com/@me/video/1",
                "buffer_post_id": "b-sent",
                "video_id": 1,
                "error": "",
                "released": 0,
                "updated_at": iso(),
            },
        ]
        self.page.stats = {
            "channel": {"views": 1500},
            "videos": {"vid1": {"views": 1200}, "b-sent": {"views": 800}},
        }
        self.page.refresh()
        # The same video scheduled on YouTube and TikTok counts and shows once.
        self.assertEqual(self.page.upcoming_tile.value.text(), "1")
        self.assertEqual(self.page.uploaded_tile.value.text(), "1")
        self.assertEqual(self.page.attention_tile.value.text(), "1")
        # Views add up across platforms for the same uploaded video.
        self.assertEqual(self.page.views_tile.value.text(), "2K")
        self.assertIn("YouTube 1.2K", self.page.views_tile.note.text())
        self.assertIn("TikTok 800", self.page.views_tile.note.text())
        coming, recent = self.texts(self.page.coming), self.texts(self.page.recent)
        self.assertIn("Cat video", coming)
        self.assertNotIn("Cat video #cat", coming)  # the shared title, not the TikTok caption
        self.assertIn("×2", coming)
        self.assertIn("2K views", recent)  # one row for the video on both platforms
        self.assertIn("×2", recent)
        self.assertIn("Upload interrupted", self.texts(self.page.attention))
        self.assertTrue(self.page.attention.isVisibleTo(self.window))

    def test_auto_upload_shortcut(self):
        self.page.auto.click()
        self.assertEqual(self.window.stack.currentWidget().objectName(), "automation")
