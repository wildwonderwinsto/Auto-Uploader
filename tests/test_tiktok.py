"""TikTok via Buffer with real SQLite, FFprobe and files; controlled Buffer and Cloudinary."""

import base64
import hashlib
import shutil
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from buffer_support import FakeBuffer, FakeCloudinary, Response, Secrets
from support import make_video

from shortspublisher.buffer import (
    BufferClientConfig,
    BufferError,
    BufferOAuth,
    CallbackListener,
    authorization_url,
    parse_callback,
    pkce,
)
from shortspublisher.cadence import Rule
from shortspublisher.hosting import HostingError
from shortspublisher.library import Library, LibraryService
from shortspublisher.media import Inspector
from shortspublisher.tiktok import TikTokError, TikTokService, caption_errors
from shortspublisher.watching import WatchService

RELAY = "https://example.github.io/shortspublisher/buffer-callback.html"


class OAuthTests(unittest.TestCase):
    def test_pkce_s256(self):
        verifier, challenge = pkce()
        self.assertTrue(43 <= len(verifier) <= 128)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
        self.assertEqual(challenge, expected.decode())
        self.assertNotEqual(pkce()[0], verifier)

    def test_authorization_url_exact_redirect_and_scopes(self):
        config = BufferClientConfig.parse({"client_id": "cid", "redirect_uri": RELAY})
        query = parse_qs(urlsplit(authorization_url(config, "st", "ch")).query)
        self.assertEqual(query["redirect_uri"], [RELAY])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertIn("offline_access", query["scope"][0].split())
        self.assertEqual(query["prompt"], ["consent"])  # as in Buffer's documented request
        self.assertNotIn("client_secret", query)

    def test_redirect_must_be_https(self):
        for bad in ("http://127.0.0.1:47633/buffer/callback", "", "https://x/#frag"):
            with self.assertRaises(BufferError):
                BufferClientConfig.parse({"client_id": "cid", "redirect_uri": bad})
        with self.assertRaises(BufferError):
            BufferClientConfig.parse({"client_id": "", "redirect_uri": RELAY})

    def test_callback_state_and_denial(self):
        self.assertEqual(parse_callback("code=abc&state=s1", "s1"), "abc")
        self.assertEqual(parse_callback(RELAY + "?code=abc&state=s1", "s1"), "abc")
        for query in (
            "code=abc&state=other",
            "code=abc",
            "error=access_denied&state=s1",
            "state=s1",
        ):
            with self.assertRaises(BufferError):
                parse_callback(query, "s1")

    def test_loopback_listener_only_accepts_callback_path(self):
        listener = CallbackListener(0)
        port = listener.server.server_address[1]
        self.assertEqual(listener.server.server_address[0], "127.0.0.1")

        def browser():
            for path in ("/other", "/buffer/callback?code=c&state=s"):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5).read()
                except Exception:
                    pass

        threading.Thread(target=browser, daemon=True).start()
        self.assertEqual(listener.wait(Event(), timeout=10), "code=c&state=s")

    def test_relay_page_matches_app_and_csp(self):
        import re

        from shortspublisher.buffer import CALLBACK_PATH, CALLBACK_PORT

        page = (Path(__file__).parents[1] / "docs" / "buffer-oauth-relay.html").read_text("utf-8")
        script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn(f"script-src 'sha256-{digest}'", page)
        self.assertNotIn("unsafe-inline';", page.split("script-src", 1)[1].split(";", 1)[0])
        self.assertIn(f"http://127.0.0.1:{CALLBACK_PORT}{CALLBACK_PATH}", script)
        self.assertIn('name="referrer" content="no-referrer"', page)
        self.assertNotRegex(page, r"(src|href)=\"https?://")  # loads nothing external

    def test_listener_cancel(self):
        listener = CallbackListener(0)
        cancel = Event()
        cancel.set()
        with self.assertRaises(BufferError) as raised:
            listener.wait(cancel)
        self.assertEqual(raised.exception.kind, "denied")


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.media.cleanup)
        cls.fixture = Path(cls.media.name) / "fixture.mp4"
        make_video(cls.fixture, size="360x640", duration=4)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.now = datetime(2030, 1, 1, 8, 0, tzinfo=UTC)
        self.library = Library(self.root / "data" / "library.sqlite3")
        self.library.initialize()
        self.buffer, self.cloud, self.secrets = FakeBuffer(), FakeCloudinary(), Secrets()
        self.service = self.make_service()
        self.service.save_client("cid", RELAY)
        self.imports = LibraryService(self.library, Inspector())

    def make_service(self):
        return TikTokService(
            self.library,
            self.secrets,
            lambda: self.now,
            api_session=self.buffer,
            host_session=self.cloud,
            anonymous=self.cloud,
        )

    def connect(self):
        verifier, challenge = pkce()
        self.buffer.challenge = challenge
        key, channels = self.service.finish_connect("good-code", verifier)
        self.service.select_channel(key, channels[0])
        return key, channels

    def host(self, retention="delete_after_sent"):
        self.service.save_hosting(
            "demo", "123456789012345", "cloud-secret", consent=True, retention=retention
        )

    def video(self, name="clip.mp4"):
        path = self.root / name
        shutil.copyfile(self.fixture, path)
        return self.imports.execute("import", str(path), Event())["selected"]

    def ready(self):
        self.connect()
        self.host()
        return self.video()


class ConnectionTests(Fixture):
    def test_connect_lists_only_tiktok_and_keeps_tokens_out_of_sqlite(self):
        key, channels = self.connect()
        self.assertEqual([c["id"] for c in channels], ["ch-tt"])
        connection = self.service.connection()
        self.assertEqual(connection["displayName"], "My TikTok")
        self.assertEqual(connection["status"], "Connected")
        dump = "\n".join(self.library.connect().iterdump())
        self.assertNotIn("access-1", dump)
        self.assertNotIn("refresh-1", dump)
        self.assertIn("refresh-1", self.secrets.values[key])

    def test_bad_code_or_wrong_verifier_rejected(self):
        verifier, challenge = pkce()
        self.buffer.challenge = challenge
        with self.assertRaises(BufferError):
            self.service.finish_connect("bad-code", verifier)
        with self.assertRaises(BufferError):
            self.service.finish_connect("good-code", pkce()[0])
        self.assertIsNone(self.service.connection())

    def test_disconnect_removes_secret_and_record(self):
        key, _ = self.connect()
        self.service.disconnect()
        self.assertIsNone(self.service.connection())
        self.assertNotIn(key, self.secrets.values)

    def test_reconnect_replaces_old_secret(self):
        first, _ = self.connect()
        second, _ = self.connect()
        self.assertNotEqual(first, second)
        self.assertNotIn(first, self.secrets.values)
        self.assertEqual(self.service.connection()["key"], second)

    def test_disconnected_channel_not_selectable(self):
        verifier, challenge = pkce()
        self.buffer.challenge = challenge
        key, channels = self.service.finish_connect("good-code", verifier)
        with self.assertRaises(BufferError):
            self.service.select_channel(key, {**channels[0], "isDisconnected": True})


class RefreshTests(Fixture):
    def test_concurrent_processes_refresh_once_without_revocation(self):
        self.connect()
        self.now += timedelta(hours=2)  # access token expired
        # Two independent authorities share Keychain and the lock file, like GUI and worker.
        authorities = [self.make_service().authority for _ in range(2)]
        tokens, errors = [], []

        def use(a):
            try:
                tokens.append(a.access_token())
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=use, args=(a,)) for a in authorities * 2]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.buffer.refreshes, 1)
        self.assertFalse(self.buffer.revoked)
        self.assertEqual(len(set(tokens)), 1)

    def test_uncertain_refresh_requires_reconnect_and_never_retries(self):
        self.connect()
        self.now += timedelta(hours=2)
        self.buffer.refresh_fail = "timeout"
        for _ in range(3):
            with self.assertRaises(BufferError) as raised:
                self.service.token()
            self.assertEqual(raised.exception.kind, "reconnect")
        self.assertEqual(self.buffer.refreshes, 1)  # the possibly-spent token is never reused
        self.assertFalse(self.buffer.revoked)
        self.assertEqual(self.service.connection()["status"], "Reconnect required")
        self.connect()  # a fresh sign-in restores the connection
        self.assertEqual(self.service.connection()["status"], "Connected")

    def test_reused_refresh_token_revokes_and_reports_reconnect(self):
        self.connect()
        stale = self.secrets.values[self.service.connection()["key"]]
        self.now += timedelta(hours=2)
        self.service.authority.access_token()
        self.secrets.values[self.service.connection()["key"]] = stale  # simulate a stale copy
        self.now += timedelta(hours=2)
        with self.assertRaises(BufferError) as raised:
            self.service.token()
        self.assertEqual(raised.exception.kind, "reconnect")
        self.assertTrue(self.buffer.revoked)
        self.assertEqual(self.service.connection()["status"], "Reconnect required")

    def test_refresh_sends_no_client_secret(self):
        oauth = BufferOAuth(self.buffer)
        self.buffer.issue()
        config = BufferClientConfig.parse({"client_id": "cid", "redirect_uri": RELAY})
        from shortspublisher.buffer import BufferTokens

        sent = []
        original = self.buffer.token
        self.buffer.token = lambda data: (sent.append(data), original(data))[1]
        oauth.refresh(config, BufferTokens("access-1", "refresh-1", 0, ""))
        self.assertNotIn("client_secret", sent[0])
        self.assertEqual(sent[0]["client_id"], "cid")


class HostingTests(Fixture):
    def test_hosting_requires_explicit_consent_and_keeps_secret_in_keychain(self):
        with self.assertRaises(HostingError):
            self.service.save_hosting("demo", "123456789012345", "cloud-secret", consent=False)
        self.host()
        self.assertNotIn("cloud-secret", "\n".join(self.library.connect().iterdump()))
        self.assertIn("cloud-secret", self.secrets.values.values())
        self.assertEqual(self.service.host().usage()["plan"], "Free")

    def test_pasted_environment_variable_and_whitespace_are_normalized(self):
        from shortspublisher.hosting import cloudinary_fields

        self.assertEqual(
            cloudinary_fields(
                "CLOUDINARY_URL=cloudinary://123456789012345:abcSECRET_xyz-12@demo", "", ""
            ),
            ("demo", "123456789012345", "abcSECRET_xyz-12"),
        )
        self.assertEqual(
            cloudinary_fields(" demo ", " 123 \n", " cloud-secret\n"),
            ("demo", "123", "cloud-secret"),
        )
        for bad in (
            ("https://cloudinary.com/demo", "1", "cloud-secret"),
            ("demo", "key-abc", "cloud-secret"),
            ("demo", "1", "short"),
        ):
            with self.assertRaises(HostingError):
                cloudinary_fields(*bad)

    def test_wrong_credentials_are_never_saved(self):
        with self.assertRaises(HostingError) as raised:
            self.service.save_hosting("demo", "999", "wrong-secret-value", consent=True)
        self.assertIn("rejected", str(raised.exception))
        self.assertIsNone(self.service.hosting())
        self.assertEqual(self.secrets.values, {})

    def test_save_trims_and_verifies(self):
        self.cloud.secret = "cloud-secret"
        self.service.save_hosting(" demo ", "123456789012345", "cloud-secret\n", consent=True)
        self.assertEqual(self.service.hosting()["cloud_name"], "demo")
        self.assertIn("cloud-secret", self.secrets.values.values())
        self.assertNotIn("cloud-secret\n", self.secrets.values.values())

    def test_check_unsaved_values(self):
        self.assertEqual(
            self.service.check_hosting("demo", "123456789012345", "cloud-secret")["plan"], "Free"
        )
        self.assertIsNone(self.service.hosting())

    def test_upload_is_exact_independent_copy_and_original_unchanged(self):
        video = self.ready()
        before = hashlib.sha256(Path(video.path).read_bytes()).hexdigest()
        post_id = self.service.publish(video.id, "Hello #fyp", False, None, Event())
        post = self.service.get(post_id)
        stored = self.cloud.stored[post["hosted_id"]]
        self.assertEqual(hashlib.sha256(stored).hexdigest(), before)
        self.assertEqual(hashlib.sha256(Path(video.path).read_bytes()).hexdigest(), before)
        self.assertNotIn("clip", post["hosted_id"])  # no filename in the public link
        self.assertTrue(post["hosted_url"].startswith("https://"))

    def test_size_limit_checked_before_any_upload(self):
        self.connect()
        self.service.save_hosting(
            "demo", "123456789012345", "cloud-secret", consent=True, max_bytes=1000
        )
        video = self.video()
        with self.assertRaises(TikTokError):
            self.service.publish(video.id, "x", False, None, Event())
        self.assertEqual(self.cloud.uploads, 0)
        self.assertEqual(self.buffer.creates, [])

    def test_unreachable_hosted_copy_never_sent_to_buffer(self):
        video = self.ready()
        self.cloud.public = False
        with self.assertRaises(TikTokError):
            self.service.publish(video.id, "x", False, None, Event())
        self.assertEqual(self.buffer.creates, [])
        self.assertEqual(self.service.posts()[0]["state"], "Hosted and ready")


class PublishTests(Fixture):
    def test_publish_now_payload_is_automatic_public_post(self):
        video = self.ready()
        post_id = self.service.publish(video.id, "Hello #fyp", True, None, Event())
        payload = self.buffer.creates[0]
        self.assertEqual(payload["schedulingType"], "automatic")
        self.assertEqual(payload["mode"], "shareNow")
        self.assertFalse(payload["needsApproval"])
        self.assertNotIn("dueAt", payload)
        self.assertEqual(payload["metadata"], {"tiktok": {"isAiGenerated": True}})
        self.assertEqual(
            payload["assets"][0]["video"]["url"], self.service.get(post_id)["hosted_url"]
        )
        self.assertEqual(self.service.get(post_id)["state"], "Publishing")

    def test_status_tracking_until_sent_without_inventing_ids(self):
        video = self.ready()
        post_id = self.service.publish(video.id, "Hello", False, None, Event())
        self.assertEqual(self.service.get(post_id)["external_link"], "")
        self.now += timedelta(minutes=3)
        self.service.refresh(Event())
        self.assertEqual(self.service.get(post_id)["state"], "Publishing")
        self.buffer.send("post-1")
        self.now += timedelta(minutes=10)
        self.service.refresh(Event())
        post = self.service.get(post_id)
        self.assertEqual(post["state"], "Sent by Buffer")
        self.assertEqual(post["external_link"], "https://www.tiktok.com/@mytiktok/video/123")
        self.assertEqual(post["buffer_post_id"], "post-1")

    def test_schedule_uses_exact_custom_time(self):
        video = self.ready()
        due = datetime(2030, 1, 1, 15, 30, tzinfo=UTC)
        post_id = self.service.publish(video.id, "Later", False, due, Event())
        self.assertEqual(self.buffer.creates[0]["mode"], "customScheduled")
        self.assertEqual(self.buffer.creates[0]["dueAt"], "2030-01-01T15:30:00Z")
        post = self.service.get(post_id)
        self.assertEqual(post["state"], "Scheduled in Buffer")
        self.assertEqual(post["next_check_at"], "2030-01-01T15:31:30+00:00")

    def test_past_or_imminent_schedule_rejected(self):
        video = self.ready()
        with self.assertRaises(TikTokError):
            self.service.publish(video.id, "x", False, self.now + timedelta(minutes=1), Event())
        self.assertEqual(self.buffer.creates, [])

    def test_explicit_ai_disclosure_and_caption_limits(self):
        video = self.ready()
        with self.assertRaises(TikTokError):
            self.service.publish(video.id, "x", None, None, Event())
        self.assertTrue(caption_errors("x" * 2201))
        self.assertTrue(caption_errors("#a #b #c #d #e #f"))
        self.assertFalse(caption_errors("#a #b #c #d #e"))
        self.assertEqual(self.cloud.uploads, 0)

    def test_duplicate_same_bytes_same_account_blocked_even_when_renamed(self):
        video = self.ready()
        self.service.publish(video.id, "One", False, None, Event())
        renamed = self.video("renamed copy.mp4")
        for v in (video, renamed):
            with self.assertRaises(TikTokError) as raised:
                self.service.publish(v.id, "Again", False, None, Event())
            self.assertIn("Duplicate", str(raised.exception))
        self.assertEqual(len(self.buffer.creates), 1)
        self.assertEqual(self.cloud.uploads, 1)

    def test_uniqueness_is_enforced_by_the_database(self):
        video = self.ready()
        post_id = self.service.publish(video.id, "One", False, None, Event())
        row = self.service.get(post_id)
        with self.assertRaises(sqlite3.IntegrityError), self.library.connect() as db:
            db.execute(
                "INSERT INTO tiktok_posts(id,source_sha256,source_path,channel_id,channel_name,origin,caption,ai_generated,mode,state,created_at,updated_at) VALUES('x',?,?,?,'n','manual','c',0,'shareNow','Hosted and ready','t','t')",
                (row["source_sha256"], row["source_path"], row["channel_id"]),
            )

    def test_uncertain_create_is_reconciled_not_resent(self):
        video = self.ready()
        self.buffer.create_fail = "timeout_after"
        with self.assertRaises(TikTokError):
            self.service.publish(video.id, "One", False, None, Event())
        post = self.service.posts()[0]
        self.assertEqual(post["state"], "Needs reconciliation")
        self.buffer.create_fail = None
        self.now += timedelta(minutes=3)
        self.service.refresh(Event())
        post = self.service.get(post["id"])
        self.assertEqual(post["buffer_post_id"], "post-1")
        self.assertEqual(post["state"], "Publishing")
        self.assertEqual(len(self.buffer.creates), 1)

    def test_unresolved_uncertainty_needs_explicit_release(self):
        video = self.ready()
        self.buffer.create_fail = "timeout_before"
        with self.assertRaises(TikTokError):
            self.service.publish(video.id, "One", False, None, Event())
        self.buffer.create_fail = None
        self.now += timedelta(minutes=3)
        self.service.refresh(Event())
        post = self.service.posts()[0]
        self.assertEqual(post["state"], "Needs reconciliation")
        with self.assertRaises(TikTokError):
            self.service.publish(video.id, "Again", False, None, Event())
        self.service.release(post["id"])
        self.service.publish(video.id, "Again", False, None, Event())
        self.assertEqual(len(self.buffer.creates), 1)

    def test_rate_limit_waits_then_submits_once(self):
        video = self.ready()
        self.buffer.create_fail = "rate"
        due = self.now + timedelta(hours=3)
        with self.assertRaises(TikTokError):
            self.service.publish(video.id, "One", False, due, Event())
        post = self.service.posts()[0]
        self.assertEqual(post["state"], "Waiting for Buffer limit")
        self.buffer.create_fail = None
        self.service.submit(post["id"])  # still inside the Retry-After window
        self.assertEqual(self.buffer.creates, [])
        self.now += timedelta(seconds=61)
        self.service.submit(post["id"])
        self.assertEqual(len(self.buffer.creates), 1)
        self.assertEqual(self.service.get(post["id"])["state"], "Scheduled in Buffer")

    def test_missed_time_is_never_posted_late(self):
        video = self.ready()
        self.buffer.create_fail = "rate"
        with self.assertRaises(TikTokError):
            self.service.publish(video.id, "One", False, self.now + timedelta(minutes=5), Event())
        self.buffer.create_fail = None
        self.now += timedelta(minutes=10)
        post = self.service.posts()[0]
        self.service.submit(post["id"])
        self.assertEqual(self.buffer.creates, [])
        self.assertEqual(self.service.get(post["id"])["state"], "Missed — not sent")

    def test_notification_mode_is_never_reported_as_automatic(self):
        video = self.ready()
        self.buffer.create_fail = "notification"
        post_id = self.service.publish(video.id, "One", False, None, Event())
        self.assertEqual(self.service.get(post_id)["state"], "Manual completion required")

    def test_buffer_refusal_is_failed_not_retried(self):
        video = self.ready()
        self.buffer.create_fail = "error"
        with self.assertRaises(TikTokError):
            self.service.publish(video.id, "One", False, None, Event())
        post = self.service.posts()[0]
        self.assertEqual(post["state"], "Failed")
        self.assertIn("not supported", post["error"])
        self.service.refresh(Event())
        self.assertEqual(self.buffer.creates, [])

    def test_cancel_scheduled_deletes_buffer_post_and_hosted_copy(self):
        video = self.ready()
        post_id = self.service.publish(
            video.id, "Later", False, self.now + timedelta(hours=5), Event()
        )
        hosted = self.service.get(post_id)["hosted_id"]
        self.service.cancel(post_id)
        self.assertEqual(self.buffer.deleted, ["post-1"])
        self.assertNotIn(hosted, self.cloud.stored)
        post = self.service.get(post_id)
        self.assertEqual((post["state"], post["released"]), ("Cancelled", 1))
        self.service.publish(video.id, "Again", False, None, Event())  # released: allowed again

    def test_cannot_cancel_after_buffer_started(self):
        video = self.ready()
        post_id = self.service.publish(
            video.id, "Later", False, self.now + timedelta(hours=5), Event()
        )
        self.buffer.posts["post-1"]["status"] = "sending"
        with self.assertRaises(TikTokError):
            self.service.cancel(post_id)
        self.assertEqual(self.buffer.deleted, [])
        self.assertEqual(self.service.get(post_id)["state"], "Publishing")

    def test_retention_deletes_only_after_sent_plus_grace(self):
        video = self.ready()
        post_id = self.service.publish(video.id, "One", False, None, Event())
        hosted = self.service.get(post_id)["hosted_id"]
        self.service.cleanup()
        self.assertIn(hosted, self.cloud.stored)  # accepted by Buffer is not enough
        self.buffer.send("post-1")
        self.now = datetime(2030, 1, 1, 9, 5, tzinfo=UTC)
        self.service.refresh(Event())
        self.service.cleanup()
        self.assertIn(hosted, self.cloud.stored)  # inside the grace period
        self.now += timedelta(hours=25)
        self.service.cleanup()
        self.assertNotIn(hosted, self.cloud.stored)

    def test_keep_retention_never_deletes(self):
        self.connect()
        self.host(retention="keep")
        video = self.video()
        post_id = self.service.publish(video.id, "One", False, None, Event())
        self.buffer.send("post-1")
        self.now += timedelta(days=5)
        self.service.refresh(Event())
        self.service.cleanup()
        self.assertIn(self.service.get(post_id)["hosted_id"], self.cloud.stored)

    def test_youtube_records_untouched(self):
        video = self.ready()
        self.service.publish(video.id, "One", False, None, Event())
        with self.library.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM upload_attempts").fetchone()[0], 0)
            self.assertIsNone(
                db.execute("SELECT 1 FROM account_connection WHERE platform='youtube'").fetchone()
            )


class AutomationTests(Fixture):
    def setUp(self):
        super().setUp()
        self.connect()
        self.host()
        self.folder_path = self.root / "watched"
        self.folder_path.mkdir()
        shutil.copyfile(self.fixture, self.folder_path / "Existing.mp4")
        self.scanner = WatchService(self.imports, lambda: self.now.timestamp())
        self.scanner.tiktok = lambda: self.service
        self.folder = self.scanner.add(
            self.scanner.preview(str(self.folder_path), Event()),
            consent=True,
            existing=False,
            quiet=1,
            background=True,
        )
        with self.library.connect() as db:  # the user's explicit "Turn On"; it starts paused
            db.execute("UPDATE publishing_control SET paused=0 WHERE id=1")
        rule = Rule("UTC", tuple(range(7)), "09:00", "21:00", 2)
        self.settings = dict(
            caption_template="{filename} #fyp",
            ai_generated=False,
            rule=rule.encode(),
            daily_cap=2,
            pattern="*.mp4",
            background=False,
        )

    def approve(self, **changes):
        self.settings.update(changes)
        proposal = self.service.preview(
            {"name": "TT", "folder_id": self.folder, "settings": self.settings}
        )
        return self.service.approve(proposal, self.scanner, Event())

    def arrive(self, name, source=None):
        if source:
            shutil.copyfile(source, self.folder_path / name)
        else:
            # Distinct durations give distinct bytes (the generated pattern is deterministic).
            self.generated = getattr(self, "generated", 0) + 1
            make_video(self.folder_path / name, size="360x640", duration=3 + self.generated)

    def scan(self):
        self.scanner.scan(Event())
        self.now += timedelta(seconds=2)
        self.scanner.scan(Event())

    def test_preview_requires_explicit_ai_choice_and_valid_cap(self):
        for change in ({"ai_generated": None}, {"daily_cap": 0}, {"caption_template": "{title}"}):
            with self.assertRaises(TikTokError):
                self.service.preview(
                    {"name": "T", "folder_id": self.folder, "settings": {**self.settings, **change}}
                )
        proposal = self.service.preview(
            {"name": "T", "folder_id": self.folder, "settings": self.settings}
        )
        self.assertIn("PUBLIC", proposal["summary"])
        self.assertIn(str(self.folder_path), proposal["summary"])

    def test_existing_files_excluded_new_file_enrolled_at_next_slot(self):
        identity = self.approve()
        self.arrive("First_clip.mp4")
        self.scan()
        decisions = {d["name"]: d["state"] for d in self.service.decisions(identity)}
        self.assertEqual(decisions["Existing.mp4"], "Excluded existing")
        self.assertEqual(decisions["First_clip.mp4"], "Enrolled")
        self.assertEqual(len(self.buffer.creates), 1)
        created = self.buffer.creates[0]
        self.assertEqual(created["mode"], "customScheduled")
        self.assertEqual(created["dueAt"], "2030-01-01T09:00:00Z")
        self.assertEqual(created["text"], "First clip #fyp")

    def test_one_pending_post_at_a_time_and_duplicates_skipped(self):
        identity = self.approve()
        self.arrive("First_clip.mp4")
        self.arrive("Second_clip.mp4")
        self.arrive("Renamed_first.mp4", source=self.folder_path / "First_clip.mp4")
        self.scan()
        self.scan()
        decisions = {d["name"]: d["state"] for d in self.service.decisions(identity)}
        self.assertEqual(len(self.buffer.creates), 1, decisions)
        self.assertIn(decisions["Renamed_first.mp4"], ("Skipped", "Duplicate — skipped"))
        self.buffer.send("post-1")
        self.now = datetime(2030, 1, 1, 9, 5, tzinfo=UTC)
        self.scan()
        self.assertEqual(len(self.buffer.creates), 2)
        self.assertEqual(self.buffer.creates[1]["dueAt"], "2030-01-01T11:00:00Z")

    def test_daily_cap_moves_to_next_day(self):
        self.approve(daily_cap=1)
        self.arrive("First_clip.mp4")
        self.arrive("Second_clip.mp4")
        self.scan()
        self.buffer.send("post-1")
        self.now = datetime(2030, 1, 1, 9, 5, tzinfo=UTC)
        self.scan()
        self.assertEqual(self.buffer.creates[1]["dueAt"], "2030-01-02T09:00:00Z")

    def test_default_global_pause_blocks_until_turned_on(self):
        with self.library.connect() as db:
            self.assertEqual(db.execute("SELECT paused FROM publishing_control").fetchone()[0], 0)
            db.execute("UPDATE publishing_control SET paused=1 WHERE id=1")
        self.approve()
        self.arrive("First_clip.mp4")
        self.scan()
        self.assertEqual(self.buffer.creates, [])

    def test_global_pause_blocks_new_submissions(self):
        identity = self.approve()
        with self.library.connect() as db:
            db.execute("UPDATE publishing_control SET paused=1 WHERE id=1")
        self.arrive("First_clip.mp4")
        self.scan()
        self.assertEqual(self.buffer.creates, [])
        self.assertIn("Paused app-wide", self.service.profiles()[0]["status"])
        with self.library.connect() as db:
            db.execute("UPDATE publishing_control SET paused=0 WHERE id=1")
        self.scan()
        self.assertEqual(len(self.buffer.creates), 1)
        self.assertEqual(
            {d["name"]: d["state"] for d in self.service.decisions(identity)}["First_clip.mp4"],
            "Enrolled",
        )

    def test_paused_or_revoked_profile_does_nothing(self):
        identity = self.approve()
        self.service.control(identity, "pause")
        self.arrive("First_clip.mp4")
        self.scan()
        self.assertEqual(self.buffer.creates, [])
        self.service.control(identity, "revoke")
        with self.assertRaises(TikTokError):
            self.service.control(identity, "resume")

    def test_background_requires_profile_permission(self):
        self.approve(background=False)
        self.arrive("First_clip.mp4")
        self.scanner.scan(Event(), background=True)
        self.now += timedelta(seconds=2)
        self.scanner.scan(Event(), background=True)
        self.assertEqual(self.buffer.creates, [])

    def test_background_scan_posts_when_both_permissions_allow(self):
        self.approve(background=True)  # the folder was added with background=True
        self.arrive("First_clip.mp4")
        self.scanner.scan(Event(), background=True)
        self.now += timedelta(seconds=2)
        self.scanner.scan(Event(), background=True)
        self.assertEqual(len(self.buffer.creates), 1)
        self.assertEqual(self.buffer.creates[0]["mode"], "customScheduled")

    def test_missed_opportunities_do_not_burst(self):
        self.approve()
        for name in ("A_clip.mp4", "B_clip.mp4", "C_clip.mp4"):
            self.arrive(name)
        self.scan()
        self.buffer.send("post-1")
        self.now = datetime(2030, 1, 1, 18, 30, tzinfo=UTC)  # app was closed for hours
        self.scan()
        self.assertEqual(len(self.buffer.creates), 2)
        self.assertEqual(self.buffer.creates[1]["dueAt"], "2030-01-01T19:00:00Z")

    def test_scanner_survives_buffer_outage(self):
        self.approve()
        self.arrive("First_clip.mp4")
        self.buffer.create_fail = "timeout_before"
        self.scan()  # must not raise
        self.assertEqual(self.service.posts()[0]["state"], "Needs reconciliation")


if __name__ == "__main__":
    unittest.main()


class InsightsTests(unittest.TestCase):
    """Optional TikTok view counts through a Buffer personal API key. Read-only."""

    def test_metrics_query_reads_views_and_skips_unreported(self):
        from shortspublisher.buffer import BufferAPI

        class Session:
            def __init__(self):
                self.sent = []

            def post(self, url, json, headers, timeout):
                self.sent.append((json, headers))
                return Response(
                    200,
                    {
                        "data": {
                            "post": {
                                "id": "p1",
                                "metrics": [
                                    {"type": "views", "value": 1520.0},
                                    {"type": "reactions", "value": 40},
                                    {"type": "comments", "value": "x"},
                                ],
                            }
                        }
                    },
                )

        session = Session()
        api = BufferAPI(lambda: "personal-key", session)
        self.assertEqual(api.post_metrics("p1"), {"views": 1520, "reactions": 40})
        query, headers = session.sent[0]
        self.assertIn("metrics", query["query"])
        self.assertNotIn("mutation", query["query"])
        self.assertEqual(headers["Authorization"], "Bearer personal-key")


class InsightsServiceTests(Fixture):
    def test_counts_only_with_the_optional_key_and_only_for_sent_posts(self):
        posts = [
            {"state": "Sent by Buffer", "buffer_post_id": "b-sent"},
            {"state": "Scheduled in Buffer", "buffer_post_id": "b-later"},
        ]
        self.buffer.posts["b-sent"] = {"id": "b-sent", "metrics": [{"type": "views", "value": 77}]}
        with patch.object(self.service, "posts", return_value=posts):
            before = len(self.buffer.requests)
            self.assertEqual(self.service.statistics(Event()), {})
            self.assertEqual(len(self.buffer.requests), before)  # no key, no request
            self.buffer.access.add("personal-key")
            self.service.save_insights_key("  personal-key ")
            self.assertEqual(self.service.statistics(Event()), {"b-sent": {"views": 77}})
        asked = [r["variables"]["input"]["id"] for r in self.buffer.requests[before:]]
        self.assertEqual(asked, ["b-sent"])
        self.service.remove_insights_key()
        self.assertEqual(self.service.insights_key(), "")
