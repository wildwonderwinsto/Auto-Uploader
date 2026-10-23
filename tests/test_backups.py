"""Backup Buffer and Cloudinary accounts (schema 15): automatic hosting failover, a platform
switched to another Buffer account, per-account limits. Controlled Buffer/Cloudinary only."""

import shutil
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

from buffer_support import CHANNELS, FakeBuffer, FakeCloudinary, Response, Secrets
from support import make_video

from shortspublisher.backups_schema import PLATFORMS
from shortspublisher.buffer import TOKEN_URL, BufferError, pkce
from shortspublisher.hosting import HostingError, over_limit
from shortspublisher.library import SCHEMA_VERSION, Library, LibraryService
from shortspublisher.media import Inspector
from shortspublisher.social import MAIN, TIKTOK, BufferPostService, SocialError

RELAY = "https://example.github.io/shortspublisher/buffer-callback.html"
# The same TikTok account, connected in a second Buffer account: a different Buffer channel ID.
BACKUP_CHANNELS = [{**CHANNELS[0], "id": "ch-tt-b"}]


class BackupBuffer(FakeBuffer):
    """A second Buffer account; its tokens start with "b-" so the router can tell them apart."""

    def __init__(self):
        super().__init__()
        self.channels = BACKUP_CHANNELS
        self.clients: list[str] = []

    def issue(self):
        values = super().issue()
        for name, pool in (("access_token", self.access), ("refresh_token", self.refresh_valid)):
            pool.discard(values[name])
            values[name] = "b-" + values[name]
            pool.add(values[name])
        return values

    def token(self, data):
        self.clients.append(data["client_id"])
        return super().token(data)

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        response = super().post(url, data=data, json=json, headers=headers, timeout=timeout)
        if json and "organizations" in json["query"] and response.status_code == 200:
            return Response(
                200, {"data": {"account": {"organizations": [{"id": "org-2", "name": "Spare"}]}}}
            )
        return response


class Buffers:
    """Routes sign-ins by client ID and API calls by token to the Buffer account they belong to."""

    def __init__(self):
        self.main, self.backup = FakeBuffer(), BackupBuffer()

    def post(self, url, data=None, json=None, headers=None, timeout=None):
        if url == TOKEN_URL:
            backup = data["client_id"] == "cid-b" or data.get("refresh_token", "").startswith("b-")
        else:
            backup = (headers or {}).get("Authorization", "")[7:].startswith("b-")
        target = self.backup if backup else self.main
        return target.post(url, data=data, json=json, headers=headers, timeout=timeout)


class FullCloudinary(FakeCloudinary):
    """Cloudinary's answer once an account has used up its plan."""

    def __init__(self, secret):
        super().__init__(secret)
        self.full = False
        self.refused = 0

    def post(self, url, data=None, files=None, timeout=None):
        if self.full and url.endswith("/video/upload"):
            self.refused += 1
            return Response(420, {"error": {"message": "Rate Limit Exceeded"}})
        return super().post(url, data=data, files=files, timeout=timeout)


class Clouds:
    """Two Cloudinary accounts, told apart by the cloud name in the API address."""

    def __init__(self):
        self.accounts = {"demo": FullCloudinary("cloud-secret"), "spare": FullCloudinary("s" * 12)}

    def route(self, url):
        return self.accounts[url.split("/v1_1/", 1)[1].split("/", 1)[0]]

    def post(self, url, data=None, files=None, timeout=None):
        return self.route(url).post(url, data=data, files=files, timeout=timeout)

    def get(self, url, auth=None, timeout=None):
        return self.route(url).get(url, auth=auth, timeout=timeout)

    def head(self, url, allow_redirects=False, timeout=None):
        public_id = url.split("/video/upload/", 1)[1].rsplit(".", 1)[0]
        owner = next(
            (a for a in self.accounts.values() if public_id in a.stored), self.accounts["demo"]
        )
        return owner.head(url, allow_redirects, timeout)


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.media.cleanup)
        cls.reels = [Path(cls.media.name) / f"reel{n}.mp4" for n in range(3)]
        for n, reel in enumerate(cls.reels):  # different bytes: not duplicates of each other
            make_video(reel, size="540x960", duration=4 + n)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.now = datetime(2030, 1, 1, 8, 0, tzinfo=UTC)
        self.library = Library(self.root / "library.sqlite3")
        self.library.initialize()
        self.buffers, self.clouds, self.secrets = Buffers(), Clouds(), Secrets()
        self.service = BufferPostService(
            TIKTOK,
            self.library,
            self.secrets,
            lambda: self.now,
            api_session=self.buffers,
            host_session=self.clouds,
            anonymous=self.clouds,
        )
        self.service.save_client("cid", RELAY)
        self.service.save_hosting("demo", "123456789012345", "cloud-secret", consent=True)
        self.imports = LibraryService(self.library, Inspector())

    def connect(self, account=MAIN):
        verifier, challenge = pkce()
        self.buffers.main.challenge = self.buffers.backup.challenge = challenge
        key, channels = self.service.finish_connect("good-code", verifier, account)
        self.service.select_channel(key, channels[0])
        return key

    def video(self, name="clip.mp4", n=0):
        path = self.root / name
        shutil.copyfile(self.reels[n], path)
        return self.imports.execute("import", str(path), Event())["selected"]


class MigrationTests(Fixture):
    def test_v14_library_keeps_posts_on_the_main_accounts(self):
        with closing(self.library.connect()) as db, db:
            for table in ("media_hosts", "buffer_accounts", "buffer_sign_ins"):
                db.execute(f"DROP TABLE {table}")
            db.execute("ALTER TABLE media_hosting DROP COLUMN full_until")
            for platform in PLATFORMS:
                for column in ("host_key", "buffer_key", "buffer_org"):
                    db.execute(f"ALTER TABLE {platform}_posts DROP COLUMN {column}")
            db.execute(
                """INSERT INTO tiktok_posts(id,source_sha256,video_id,source_path,channel_id,
                channel_name,origin,caption,ai_generated,mode,state,hosted_id,created_at,updated_at)
                VALUES('p','sha',1,'/x.mp4','ch-tt','My TikTok','manual','c',0,'shareNow',
                'Sent by Buffer','shortspublisher/abc','t','t')"""
            )
            db.execute("PRAGMA user_version=14")
        main = self.service.hosting()["credential_key"]
        self.library.initialize()
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            post = db.execute("SELECT host_key, buffer_key FROM tiktok_posts").fetchone()
            self.assertEqual(tuple(post), (main, ""))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM media_hosts").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM buffer_accounts").fetchone()[0], 0)

    def test_v15_backup_accounts_gain_their_own_sign_in(self):
        self.service.add_buffer_account("Second", "", "")
        with closing(self.library.connect()) as db, db:
            for column in ("credential_key", "organizations"):
                db.execute(f"ALTER TABLE buffer_accounts DROP COLUMN {column}")
            db.execute("PRAGMA user_version=15")
        self.library.initialize()
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        backup = self.service.buffer_accounts()[1]
        self.assertEqual(
            (backup["label"], backup["key"], backup["organizations"]), ("Second", "", [])
        )


class HostingTests(Fixture):
    def add_spare(self):
        self.service.add_host("spare", "123456789012345", "s" * 12)

    def test_backup_takes_over_when_the_main_account_is_full(self):
        self.connect()
        self.add_spare()
        self.clouds.accounts["demo"].full = True
        first = self.service.publish(self.video().id, "One", False, None, Event())
        spare = self.service.hosts()[1]["credential_key"]
        self.assertEqual(self.service.get(first)["host_key"], spare)
        self.assertEqual(self.service.get(first)["state"], "Publishing")
        self.assertIn(self.service.get(first)["hosted_id"], self.clouds.accounts["spare"].stored)
        # The full account is skipped for a day, then tried again.
        self.service.publish(self.video("two.mp4", 1).id, "Two", False, None, Event())
        self.assertEqual(self.clouds.accounts["demo"].refused, 1)
        self.now += timedelta(hours=25)
        self.clouds.accounts["demo"].full = False
        third = self.service.publish(self.video("three.mp4", 2).id, "Three", False, None, Event())
        self.assertEqual(
            self.service.get(third)["host_key"], self.service.hosts()[0]["credential_key"]
        )

    def test_every_account_full_fails_hosting_and_nothing_reaches_buffer(self):
        self.connect()
        self.add_spare()
        for account in self.clouds.accounts.values():
            account.full = True
        with self.assertRaisesRegex(SocialError, "Every Cloudinary account"):
            self.service.publish(self.video().id, "One", False, None, Event())
        self.assertEqual(self.service.posts()[0]["state"], "Hosting failed")
        self.assertEqual(self.buffers.main.creates, [])

    def test_copies_are_removed_from_the_account_that_holds_them(self):
        self.connect()
        self.add_spare()
        self.clouds.accounts["demo"].full = True
        post_id = self.service.publish(self.video().id, "One", False, None, Event())
        self.service.remove_hosted(post_id)
        hosted = self.service.get(post_id)["hosted_id"]
        self.assertEqual(self.clouds.accounts["spare"].destroyed, [hosted])
        self.assertEqual(self.clouds.accounts["demo"].destroyed, [])

    def test_backup_is_verified_unique_and_kept_while_a_post_waits_on_it(self):
        with self.assertRaises(HostingError):
            self.service.add_host("spare", "123456789012345", "wrong-secret-value")
        self.assertEqual(len(self.service.hosts()), 1)
        self.add_spare()
        with self.assertRaisesRegex(HostingError, "already in the list"):
            self.add_spare()
        self.connect()
        self.clouds.accounts["demo"].full = True
        self.buffers.main.create_fail = "rate"
        with self.assertRaises(SocialError):
            self.service.publish(
                self.video().id, "One", False, self.now + timedelta(hours=3), Event()
            )
        spare = self.service.hosts()[1]["credential_key"]
        with self.assertRaisesRegex(HostingError, "waiting"):
            self.service.remove_host(spare)

    def test_removing_the_main_account_promotes_the_first_backup(self):
        self.add_spare()
        self.service.remove_hosting()
        hosts = self.service.hosts()
        self.assertEqual([h["cloud_name"] for h in hosts], ["spare"])
        self.assertEqual(self.service.hosting()["retention"], "delete_after_sent")
        self.assertTrue(self.secrets.get(hosts[0]["credential_key"]))

    def test_check_marks_a_used_up_account_and_clears_it_again(self):
        self.add_spare()
        account = self.clouds.accounts["demo"]
        account.get = lambda url, auth=None, timeout=None: Response(
            200, {"plan": "Free", "credits": {"usage": 25, "limit": 25, "used_percent": 100}}
        )
        results = self.service.check_hosts()
        self.assertEqual([r["full"] for r in results], [True, False])
        self.assertEqual(self.service.upload_order(""), [self.service.hosts()[1]["credential_key"]])

    def test_only_plan_limits_count_as_full(self):
        self.assertTrue(over_limit(420, "Rate Limit Exceeded"))
        self.assertTrue(over_limit(400, "Monthly usage limit reached"))
        self.assertFalse(
            over_limit(400, "File size too large. Got 120000000. Maximum is 104857600.")
        )
        self.assertFalse(over_limit(400, "Invalid video file"))


class BufferAccountTests(Fixture):
    def add_backup(self, client=""):
        return self.service.add_buffer_account("Second", client, "")

    def test_backup_reuses_the_main_client_unless_given_its_own(self):
        same = self.add_backup()
        own = self.service.add_buffer_account("Third", "cid-b", "")
        accounts = {a["id"]: a for a in self.service.buffer_accounts()}
        self.assertEqual(accounts[same]["client"].client_id, "cid")
        self.assertEqual(accounts[own]["client"].client_id, "cid-b")
        self.assertEqual(accounts[own]["client"].redirect_uri, RELAY)
        with self.assertRaisesRegex(BufferError, "already called"):
            self.service.add_buffer_account("second", "", "")

    def test_sign_in_and_renewal_use_that_accounts_client(self):
        backup = self.add_backup("cid-b")
        key = self.connect(backup)
        self.assertEqual(self.service.account_of(key), backup)
        self.assertEqual(self.service.connection()["id"], "ch-tt-b")
        self.now += timedelta(hours=2)  # the access token has expired: renew it
        self.service.api().organizations()
        self.assertEqual(self.buffers.backup.clients, ["cid-b", "cid-b"])
        self.assertEqual(self.buffers.main.refreshes, 0)

    def sign_in_backup(self, account):
        verifier, challenge = pkce()
        self.buffers.main.challenge = self.buffers.backup.challenge = challenge
        return self.service.sign_in_backup(account, "good-code", verifier)

    def schedule(self, n=0):
        return self.service.publish(
            self.video(f"clip{n}.mp4", n).id, "One", False, self.now + timedelta(hours=3), Event()
        )

    def test_a_limited_buffer_account_falls_back_to_a_signed_in_backup(self):
        backup = self.add_backup("cid-b")
        self.connect()
        found = self.sign_in_backup(backup)
        self.assertEqual(found, {"found": ["TikTok (My TikTok)"], "missing": []})
        self.buffers.main.create_fail = "rate"
        post = self.service.get(self.schedule())  # no error: the backup took it
        self.assertEqual(post["state"], "Scheduled in Buffer")
        self.assertEqual(post["channel_id"], "ch-tt-b")
        self.assertEqual(self.service.account_of(post["buffer_key"]), backup)
        self.assertEqual(len(self.buffers.backup.creates), 1)
        self.assertIsNotNone(self.service.retry_after(MAIN))
        self.assertIsNone(self.service.retry_after(backup))
        # While the main account waits, the next post goes straight to the backup.
        self.buffers.main.create_fail = None
        self.schedule(1)
        self.assertEqual((len(self.buffers.main.creates), len(self.buffers.backup.creates)), (0, 2))
        # The same video still can't be posted again, whichever Buffer account is used.
        with self.assertRaisesRegex(SocialError, "already has a TikTok post"):
            self.service.publish(self.service.posts()[0]["video_id"], "Again", False, None, Event())

    def test_a_plan_limit_refusal_also_falls_back(self):
        backup = self.add_backup("cid-b")
        self.connect()
        self.sign_in_backup(backup)
        refusal = {
            "data": {
                "createPost": {
                    "__typename": "LimitReachedError",
                    "message": "You have reached the limit of 10 scheduled posts for this channel.",
                }
            }
        }
        self.buffers.main.create = lambda payload: Response(200, refusal)
        post = self.service.get(self.schedule())
        self.assertEqual(post["state"], "Scheduled in Buffer")
        self.assertEqual(len(self.buffers.backup.creates), 1)

    def test_every_account_limited_waits_and_says_so(self):
        backup = self.add_backup("cid-b")
        self.connect()
        self.sign_in_backup(backup)
        self.buffers.main.create_fail = self.buffers.backup.create_fail = "rate"
        with self.assertRaisesRegex(SocialError, "Every Buffer account has reached its limit"):
            self.schedule()
        post = self.service.posts()[0]
        self.assertEqual(post["state"], "Waiting for Buffer limit")
        self.buffers.main.create_fail = self.buffers.backup.create_fail = None
        self.now += timedelta(seconds=61)
        self.service.submit(post["id"])
        self.assertEqual(self.service.get(post["id"])["state"], "Scheduled in Buffer")

    def test_a_backup_without_the_same_account_is_never_used(self):
        backup = self.add_backup("cid-b")
        self.connect()
        self.buffers.backup.channels = [
            {**BACKUP_CHANNELS[0], "name": "other", "displayName": "Someone Else"}
        ]
        found = self.sign_in_backup(backup)
        self.assertEqual(found["missing"], ["TikTok (My TikTok)"])
        self.buffers.main.create_fail = "rate"
        with self.assertRaises(SocialError):
            self.schedule()
        self.assertEqual(self.buffers.backup.creates, [])
        self.assertEqual(self.service.posts()[0]["state"], "Waiting for Buffer limit")

    def test_the_same_buffer_account_cannot_be_added_twice(self):
        backup = self.add_backup()  # main client: the browser signs in to the main account again
        self.connect()
        with self.assertRaisesRegex(BufferError, "same Buffer account as “Main”"):
            self.sign_in_backup(backup)
        self.assertEqual(self.service.standing_key(backup), "")
        self.assertEqual(len([k for k in self.secrets.values if k.startswith("buffer-")]), 1)

    def test_posts_keep_checking_through_the_sign_in_they_were_sent_with(self):
        backup = self.add_backup("cid-b")
        old = self.connect()
        post_id = self.service.publish(
            self.video().id, "One", False, self.now + timedelta(hours=3), Event()
        )
        self.connect(backup)
        self.assertTrue(self.secrets.get(old))  # kept while the post waits on it
        post = self.service.get(post_id)
        self.buffers.main.send(post["buffer_post_id"])
        self.now += timedelta(hours=4)
        self.service.refresh(Event())
        self.assertEqual(self.service.get(post_id)["state"], "Sent by Buffer")
        self.assertIsNone(self.secrets.get(old))  # forgotten once nothing waits on it

    def test_same_video_cannot_post_again_through_another_buffer_account(self):
        backup = self.add_backup("cid-b")
        self.connect()
        video = self.video()
        self.service.publish(video.id, "One", False, None, Event())
        self.connect(backup)
        with self.assertRaisesRegex(SocialError, "already has a TikTok post"):
            self.service.publish(video.id, "One", False, None, Event())

    def test_a_different_account_is_never_posted_to_instead(self):
        backup = self.add_backup("cid-b")
        self.connect()
        self.buffers.main.create_fail = "rate"
        with self.assertRaises(SocialError):
            self.service.publish(
                self.video().id, "One", False, self.now + timedelta(hours=3), Event()
            )
        self.buffers.backup.channels = [
            {**BACKUP_CHANNELS[0], "name": "other", "displayName": "Someone Else"}
        ]
        self.connect(backup)
        post = self.service.posts()[0]
        self.service.submit(post["id"])
        self.assertEqual(self.service.get(post["id"])["state"], "Failed")
        self.assertEqual(self.buffers.backup.creates, [])

    def test_backup_in_use_cannot_be_removed(self):
        backup = self.add_backup("cid-b")
        self.connect(backup)
        with self.assertRaisesRegex(BufferError, "connected"):
            self.service.remove_buffer_account(backup)
        self.service.disconnect()
        self.sign_in_backup(backup)  # its own standing sign-in doesn't block removal
        self.service.remove_buffer_account(backup)
        self.assertEqual([a["id"] for a in self.service.buffer_accounts()], [MAIN])
        self.assertFalse([k for k in self.secrets.values if k.startswith("buffer-")])


if __name__ == "__main__":
    unittest.main()
