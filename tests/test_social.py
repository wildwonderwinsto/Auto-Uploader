"""Instagram and Facebook via Buffer: real SQLite, FFprobe and files; controlled Buffer/Cloudinary."""

import shutil
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import requests
from buffer_support import FakeBuffer, FakeCloudinary, Secrets
from support import make_video

from shortspublisher.buffer import BufferError, pkce
from shortspublisher.cadence import Rule
from shortspublisher.library import SCHEMA_VERSION, Library, LibraryService
from shortspublisher.media import Inspector
from shortspublisher.social import (
    FACEBOOK,
    INSTAGRAM,
    TIKTOK,
    BufferPostService,
    SocialError,
    caption_errors,
    media_errors,
)
from shortspublisher.social_schema import PLATFORMS
from shortspublisher.watching import WatchService

RELAY = "https://example.github.io/shortspublisher/buffer-callback.html"


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.media.cleanup)
        folder = Path(cls.media.name)
        cls.reel = folder / "reel.mp4"  # 9:16 at 540×960 suits every Buffer platform
        make_video(cls.reel, size="540x960", duration=4)
        cls.small = folder / "small.mp4"  # 9:16 but below Facebook's 540×960 minimum
        make_video(cls.small, size="360x640", duration=4)
        cls.wide = folder / "wide.mp4"
        make_video(cls.wide, size="960x540", duration=4)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.now = datetime(2030, 1, 1, 8, 0, tzinfo=UTC)
        self.library = Library(self.root / "data" / "library.sqlite3")
        self.library.initialize()
        self.buffer, self.cloud, self.secrets = FakeBuffer(), FakeCloudinary(), Secrets()
        self.tiktok, self.instagram, self.facebook = (
            self.make(spec) for spec in (TIKTOK, INSTAGRAM, FACEBOOK)
        )
        self.tiktok.save_client("cid", RELAY)
        self.imports = LibraryService(self.library, Inspector())

    def make(self, spec):
        return BufferPostService(
            spec,
            self.library,
            self.secrets,
            lambda: self.now,
            api_session=self.buffer,
            host_session=self.cloud,
            anonymous=self.cloud,
        )

    def sign_in(self, service):
        verifier, challenge = pkce()
        self.buffer.challenge = challenge
        key, channels = service.finish_connect("good-code", verifier)
        service.select_channel(key, channels[0])
        return key, channels

    def host(self):
        self.tiktok.save_hosting("demo", "123456789012345", "cloud-secret", consent=True)

    def video(self, source=None, name="clip.mp4"):
        path = self.root / name
        shutil.copyfile(source or self.reel, path)
        return self.imports.execute("import", str(path), Event())["selected"]


class SchemaTests(Fixture):
    def test_v12_library_gains_instagram_and_facebook_tables_and_keeps_tiktok(self):
        with closing(self.library.connect()) as db, db:
            for platform in PLATFORMS:
                for table in ("drafts", "posts", "profiles", "decisions"):
                    db.execute(f"DROP TABLE {platform}_{table}")
            db.execute(
                "INSERT INTO tiktok_drafts VALUES (7, 'kept', 1, '2030-01-01T00:00:00+00:00')"
            )
            db.execute("PRAGMA user_version=12")
            before = db.execute("SELECT * FROM tiktok_drafts").fetchall()
        self.library.initialize()
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(db.execute("SELECT * FROM tiktok_drafts").fetchall(), before)
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
            for platform in PLATFORMS:
                self.assertIn(f"{platform}_posts_one_per_destination", names)
                self.assertEqual(
                    db.execute(f"SELECT COUNT(*) FROM {platform}_profiles").fetchone()[0], 0
                )
            # Nothing is connected, hosted or enabled by the migration.
            self.assertEqual(db.execute("SELECT COUNT(*) FROM media_hosting").fetchone()[0], 0)


class RulesTests(Fixture):
    def test_reel_fits_every_platform(self):
        video = self.video()
        for spec in (TIKTOK, INSTAGRAM, FACEBOOK):
            self.assertEqual(media_errors(spec, video, 100 * 1024**2), [], spec.name)

    def test_facebook_needs_540_by_960_and_mp4(self):
        small = self.video(self.small, "small.mp4")
        self.assertEqual(media_errors(INSTAGRAM, small, 10**9), [])
        self.assertIn("540×960", " ".join(media_errors(FACEBOOK, small, 10**9)))
        mov = self.video(self.reel, "reel.mov")
        self.assertIn("MP4 files", " ".join(media_errors(FACEBOOK, mov, 10**9)))
        self.assertEqual(media_errors(INSTAGRAM, mov, 10**9), [])

    def test_landscape_is_not_a_reel(self):
        wide = self.video(self.wide, "wide.mp4")
        self.assertIn("between 9:16 and 4:5", " ".join(media_errors(INSTAGRAM, wide, 10**9)))
        self.assertIn("vertical 9:16", " ".join(media_errors(FACEBOOK, wide, 10**9)))
        self.assertEqual(media_errors(TIKTOK, wide, 10**9), [])  # TikTok has no ratio rule

    def test_durations(self):
        video = self.video()
        long = replace(video, media=replace(video.media, duration=120.0))
        self.assertIn("3 to 90 seconds", " ".join(media_errors(FACEBOOK, long, 10**9)))
        self.assertEqual(media_errors(INSTAGRAM, long, 10**9), [])

    def test_caption_limits(self):
        tags = " ".join(f"#t{i}" for i in range(6))
        self.assertTrue(caption_errors(INSTAGRAM, tags))
        self.assertEqual(caption_errors(FACEBOOK, tags), [])  # no Facebook hashtag limit
        self.assertTrue(caption_errors(FACEBOOK, "x" * 5001))
        self.assertEqual(caption_errors(FACEBOOK, "x" * 5000), [])
        self.assertTrue(caption_errors(INSTAGRAM, "x" * 2201))


class ConnectionTests(Fixture):
    def test_fresh_sign_in_lists_only_this_platform_and_never_facebook_groups(self):
        _, channels = self.sign_in(self.instagram)
        self.assertEqual([c["id"] for c in channels], ["ch-ig"])
        _, channels = self.sign_in(self.facebook)
        self.assertEqual([c["id"] for c in channels], ["ch-fb"])
        self.assertEqual(self.facebook.connection()["type"], "page")

    def test_reuse_shares_one_keychain_key_and_disconnect_keeps_it_while_used(self):
        key, _ = self.sign_in(self.tiktok)
        shared, channels = self.instagram.reuse_sign_in()
        self.assertEqual(shared, key)
        self.instagram.select_channel(shared, channels[0])
        self.assertEqual(self.instagram.connection()["key"], key)
        self.tiktok.disconnect()
        self.assertIn(key, self.secrets.values)  # Instagram still uses it
        self.assertTrue(self.instagram.api().channels("instagram"))
        self.instagram.disconnect()
        self.assertNotIn(key, self.secrets.values)

    def test_reuse_needs_a_working_sign_in(self):
        with self.assertRaises(BufferError):
            self.instagram.reuse_sign_in()
        self.sign_in(self.tiktok)
        self.tiktok.set_connection_status("Reconnect required")
        with self.assertRaises(BufferError):
            self.instagram.reuse_sign_in()

    def test_reconnect_required_spreads_to_every_platform_on_the_sign_in(self):
        key, _ = self.sign_in(self.tiktok)
        self.instagram.select_channel(key, self.instagram.reuse_sign_in()[1][0])
        self.facebook.select_channel(key, self.facebook.reuse_sign_in()[1][0])
        self.now += timedelta(hours=2)  # the access token needs renewal
        self.buffer.refresh_fail = "timeout"
        with self.assertRaises(BufferError):
            self.instagram.api().channels("instagram")
        for service in (self.tiktok, self.instagram, self.facebook):
            self.assertEqual(service.connection()["status"], "Reconnect required")
        refreshes = self.buffer.refreshes
        for service in (self.tiktok, self.facebook):
            with self.assertRaises(BufferError):
                service.api().channels("tiktok")
        self.assertEqual(self.buffer.refreshes, refreshes)  # the spent token is never reused

    def test_fresh_sign_in_repoints_other_platforms_and_drops_the_old_key(self):
        old, _ = self.sign_in(self.tiktok)
        self.tiktok.set_connection_status("Reconnect required")
        new, _ = self.sign_in(self.instagram)
        self.assertEqual(self.tiktok.connection()["key"], new)
        self.assertEqual(self.tiktok.connection()["status"], "Connected")
        self.assertNotIn(old, self.secrets.values)
        self.assertIn(new, self.secrets.values)

    def test_youtube_connection_is_never_touched(self):
        with closing(self.library.connect()) as db, db:
            db.execute(
                "INSERT INTO account_connection VALUES ('youtube', 'yt-key', '{\"status\": \"Connected\"}', 'x')"
            )
        self.assertIsNone(self.instagram.shared_sign_in())
        key, _ = self.sign_in(self.instagram)
        self.instagram.set_connection_status("Reconnect required")
        self.instagram.disconnect()
        with closing(self.library.connect()) as db:
            row = db.execute(
                "SELECT credential_key, channel FROM account_connection WHERE platform='youtube'"
            ).fetchone()
        self.assertEqual(tuple(row), ("yt-key", '{"status": "Connected"}'))


class PublishTests(Fixture):
    def ready(self, service):
        self.sign_in(service)
        self.host()
        return self.video()

    def test_instagram_posts_a_public_reel_with_explicit_ai_label(self):
        video = self.ready(self.instagram)
        with self.assertRaises(SocialError):
            self.instagram.publish(video.id, "Hi", None, None, Event())
        self.assertEqual(self.cloud.uploads, 0)
        post_id = self.instagram.publish(video.id, "Hi #reel", True, None, Event())
        payload = self.buffer.creates[-1]
        self.assertEqual(payload["channelId"], "ch-ig")
        self.assertEqual(payload["schedulingType"], "automatic")
        self.assertEqual(payload["mode"], "shareNow")
        self.assertEqual(
            payload["metadata"],
            {"instagram": {"type": "reel", "shouldShareToFeed": True, "isAiGenerated": True}},
        )
        self.assertEqual(self.instagram.get(post_id)["state"], "Publishing")
        self.assertEqual(self.tiktok.posts(), [])

    def test_facebook_posts_a_page_reel_without_an_ai_label(self):
        video = self.ready(self.facebook)
        due = self.now + timedelta(hours=1)
        post_id = self.facebook.publish(video.id, "Hello", None, due, Event())
        payload = self.buffer.creates[-1]
        self.assertEqual(payload["metadata"], {"facebook": {"type": "reel"}})
        self.assertEqual(payload["dueAt"], "2030-01-01T09:00:00Z")
        self.assertEqual(self.facebook.get(post_id)["state"], "Scheduled in Buffer")
        self.buffer.send("post-1", "https://www.facebook.com/reel/1")
        self.now = due + timedelta(minutes=3)
        self.facebook.refresh(Event())
        post = self.facebook.get(post_id)
        self.assertEqual(post["state"], "Sent by Buffer")
        self.assertEqual(post["external_link"], "https://www.facebook.com/reel/1")

    def test_facebook_refuses_unsuitable_video_before_any_upload(self):
        self.ready(self.facebook)
        small = self.video(self.small, "small.mp4")
        with self.assertRaises(SocialError):
            self.facebook.publish(small.id, "Hello", None, None, Event())
        self.assertEqual((self.cloud.uploads, self.buffer.creates), (0, []))

    def test_duplicates_are_per_platform(self):
        key, _ = self.sign_in(self.tiktok)
        self.instagram.select_channel(key, self.instagram.reuse_sign_in()[1][0])
        self.host()
        video = self.video()
        self.tiktok.publish(video.id, "Hi", False, None, Event())
        self.instagram.publish(video.id, "Hi", False, None, Event())  # another platform: allowed
        renamed = self.video(name="renamed.mp4")
        with self.assertRaises(SocialError) as raised:
            self.instagram.publish(renamed.id, "Hi", False, None, Event())
        self.assertIn("Duplicate", str(raised.exception))
        self.assertEqual(self.cloud.uploads, 2)

    def test_personal_instagram_is_manual_completion_not_automatic(self):
        video = self.ready(self.instagram)
        self.buffer.create_fail = "notification"
        post_id = self.instagram.publish(video.id, "Hi", False, None, Event())
        post = self.instagram.get(post_id)
        self.assertEqual(post["state"], "Manual completion required")
        self.assertIn("Instagram app", post["error"])

    def test_uncertain_create_is_reconciled_not_resent(self):
        video = self.ready(self.instagram)
        self.buffer.create_fail = "timeout_after"
        with self.assertRaises(SocialError):
            self.instagram.publish(video.id, "Hi", False, None, Event())
        self.buffer.create_fail = None
        self.now += timedelta(minutes=3)
        self.instagram.refresh(Event())
        self.assertEqual(len(self.buffer.creates), 1)
        self.assertEqual(self.instagram.posts()[0]["buffer_post_id"], "post-1")

    def test_auth_failure_waits_in_this_platforms_reconnection_state(self):
        video = self.ready(self.facebook)
        self.buffer.revoked = True
        with self.assertRaises(SocialError):
            self.facebook.publish(video.id, "Hello", None, None, Event())
        self.assertEqual(self.facebook.posts()[0]["state"], "Waiting for Facebook reconnection")


class AutomationTests(Fixture):
    def setUp(self):
        super().setUp()
        key, _ = self.sign_in(self.tiktok)
        for service in (self.instagram, self.facebook):
            service.select_channel(key, service.reuse_sign_in()[1][0])
        self.host()
        self.folder_path = self.root / "watched"
        self.folder_path.mkdir()
        shutil.copyfile(self.reel, self.folder_path / "Existing.mp4")
        self.scanner = WatchService(self.imports, lambda: self.now.timestamp())
        self.scanner.tiktok = lambda: self.tiktok
        self.scanner.instagram = lambda: self.instagram
        self.scanner.facebook = lambda: self.facebook
        self.folder = self.scanner.add(
            self.scanner.preview(str(self.folder_path), Event()),
            consent=True,
            existing=False,
            quiet=1,
            background=True,
        )
        with self.library.connect() as db:  # the user's explicit "Turn On"; it starts paused
            db.execute("UPDATE publishing_control SET paused=0 WHERE id=1")
        self.settings = dict(
            caption_template="{filename}",
            ai_generated=None,
            rule=Rule("UTC", tuple(range(7)), "09:00", "21:00", 2).encode(),
            daily_cap=2,
            pattern="*.mp4",
            background=False,
        )

    def approve(self, service, **changes):
        settings = {**self.settings, **changes}
        proposal = service.preview(
            {"name": service.spec.name, "folder_id": self.folder, "settings": settings}
        )
        return service.approve(proposal, self.scanner, Event()), proposal

    def scan(self):
        self.scanner.scan(Event())
        self.now += timedelta(seconds=2)
        self.scanner.scan(Event())

    def test_daily_limits_reach_each_platforms_own_maximum(self):
        for service, ai, maximum in (
            (self.tiktok, False, 25),
            (self.instagram, False, 50),
            (self.facebook, None, 35),
        ):
            _, proposal = self.approve(service, ai_generated=ai, daily_cap=15)
            self.assertIn("At most 15 posts per local day", proposal["summary"])
            self.approve(service, ai_generated=ai, daily_cap=maximum)
            with self.assertRaises(SocialError) as raised:
                self.approve(service, ai_generated=ai, daily_cap=maximum + 1)
            self.assertIn(f"at most {maximum} posts a day", str(raised.exception))

    def test_instagram_requires_ai_choice_facebook_does_not(self):
        with self.assertRaises(SocialError):
            self.approve(self.instagram)
        _, proposal = self.approve(self.facebook)
        self.assertNotIn("AI-generated", proposal["summary"])
        self.assertIn("Facebook Page", proposal["summary"])
        self.assertIn("Facebook Page", self.facebook.spec.automation_consent)

    def test_one_folder_posts_to_each_platform_independently(self):
        self.approve(self.tiktok, ai_generated=False)
        self.approve(self.instagram, ai_generated=False)
        self.approve(self.facebook)
        make_video(self.folder_path / "New.mp4", size="540x960", duration=5)
        self.scan()
        channels = sorted(p["channelId"] for p in self.buffer.creates)
        self.assertEqual(channels, ["ch-fb", "ch-ig", "ch-tt"])
        for service in (self.tiktok, self.instagram, self.facebook):
            posts = service.posts()
            self.assertEqual([p["state"] for p in posts], ["Scheduled in Buffer"], service.spec)
            self.assertEqual(posts[0]["origin"], "automation")
        # The existing file was excluded on every platform.
        self.assertNotIn("Existing", " ".join(p["text"] for p in self.buffer.creates))

    def test_unsuitable_file_needs_review_on_facebook_only(self):
        self.approve(self.instagram, ai_generated=False)
        profile, _ = self.approve(self.facebook)
        shutil.copyfile(self.small, self.folder_path / "Small.mp4")
        self.scan()
        self.assertEqual([p["channelId"] for p in self.buffer.creates], ["ch-ig"])
        decision = self.facebook.decisions(profile)[0]
        self.assertEqual(decision["state"], "Needs review")
        self.assertIn("540×960", decision["reason"])

    def test_one_platform_outage_does_not_stop_the_others(self):
        self.approve(self.instagram, ai_generated=False)
        self.approve(self.facebook)
        original = self.buffer.create

        def flaky(payload):
            if payload["channelId"] == "ch-ig":
                raise requests.ConnectionError()
            return original(payload)

        self.buffer.create = flaky
        make_video(self.folder_path / "New.mp4", size="540x960", duration=5)
        self.scan()
        self.assertEqual([p["channelId"] for p in self.buffer.creates], ["ch-fb"])
        self.assertEqual(self.instagram.posts()[0]["state"], "Needs reconciliation")


if __name__ == "__main__":
    unittest.main()
