"""Settings → Limits & usage counts, read from a real isolated library (no network)."""

import os
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QPushButton
from support import shutdown

from shortspublisher.app import build
from shortspublisher.library import Library
from shortspublisher.usage import snapshot

NOW = datetime(2030, 1, 2, 15, 0, tzinfo=UTC)


class UsageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.library = Library(Path(temp.name) / "library.sqlite3")
        self.library.initialize()

    def post(self, identity, *, submitted=None, state="Sent by Buffer", platform="tiktok"):
        with closing(self.library.connect()) as db, db:
            db.execute(
                f"""INSERT INTO {platform}_posts(id,source_sha256,source_path,channel_id,
                channel_name,origin,caption,ai_generated,mode,state,submitted_at,created_at,
                updated_at) VALUES (?,?,?,?,?,'manual','',0,'shareNow',?,?,?,?)""",
                (
                    identity,
                    identity,
                    "/v.mp4",
                    "channel",
                    "@me",
                    state,
                    submitted.isoformat() if submitted else None,
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )

    def test_empty_library_uses_nothing(self):
        found = snapshot(self.library, NOW)
        self.assertEqual(found["youtube"]["used"], 0)
        self.assertEqual(found["youtube"]["limit"], 100)
        self.assertEqual(found["youtube"]["resets"], datetime(2030, 1, 3, tzinfo=UTC))
        for platform, limit in (("tiktok", 25), ("instagram", 50), ("facebook", 35)):
            counts = found["buffer"][platform]
            self.assertEqual((counts["used"], counts["limit"]), (0, limit))
            self.assertIsNone(counts["frees"])
        self.assertEqual(found["waits"], [])

    def test_buffer_posts_count_for_24_hours_from_when_they_were_sent(self):
        self.post("a", submitted=NOW - timedelta(hours=2))
        self.post("b", submitted=NOW - timedelta(hours=5))
        self.post("old", submitted=NOW - timedelta(hours=25))  # no longer counted
        self.post("waiting", state="Scheduled in Buffer", submitted=NOW - timedelta(hours=1))
        counts = snapshot(self.library, NOW)["buffer"]["tiktok"]
        self.assertEqual(counts["used"], 3)
        self.assertEqual(counts["frees"], NOW - timedelta(hours=5) + timedelta(hours=24))
        self.assertEqual(counts["scheduled"], 1)
        self.assertEqual(counts["scheduled_limit"], 10)
        self.assertEqual(snapshot(self.library, NOW)["buffer"]["instagram"]["used"], 0)

    def test_buffer_wait_shows_until_it_passes(self):
        until = NOW + timedelta(minutes=30)
        with closing(self.library.connect()) as db, db:
            db.execute("UPDATE buffer_control SET retry_after=? WHERE id=1", (until.isoformat(),))
        self.assertEqual(snapshot(self.library, NOW)["waits"], [("Buffer", until)])
        self.assertEqual(snapshot(self.library, until + timedelta(seconds=1))["waits"], [])


class UsagePanelTests(unittest.TestCase):
    """Settings checks Cloudinary's usage by itself; there is no button for it anywhere."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app, self.w = build(Path(temp.name))
        self.w.show()
        self.addCleanup(lambda: shutdown(self.app, self.w))

    def test_cloudinary_usage_is_checked_automatically_and_quietly(self):
        social = self.w.tiktok
        social.data["hosts"] = [{"credential_key": "k", "cloud_name": "demo", "main": 1}]
        with patch.object(social, "check_hosts") as check:
            self.w.navigate("settings")
            check.assert_called_once_with(quiet=True)
            self.w.usage.refresh()  # within 30 minutes: not asked again
            check.assert_called_once()
            social.hosts_checked = datetime.now(UTC) - timedelta(minutes=31)
            self.w.usage.refresh()
            self.assertEqual(check.call_count, 2)
        labels = [b.text() for b in self.w.background.findChildren(QPushButton)]
        self.assertFalse([t for t in labels if "Usage" in t])


if __name__ == "__main__":
    unittest.main()
