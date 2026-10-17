import hashlib
import json
import logging
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

import requests
from account_support import CHANNEL, CONFIG, ControlledOAuth, ControlledYouTube, MemoryCredentials

from shortspublisher.accounts import AccountRepository, AccountService
from shortspublisher.auth import (
    AUTH_URL,
    SCOPE,
    TOKEN_URL,
    AccountError,
    ClientConfig,
    GoogleOAuth,
    Tokens,
)
from shortspublisher.credentials import KeychainStore
from shortspublisher.library import Library, LibraryError
from shortspublisher.youtube import Channel, YouTubeClient


def response(status, body):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    result._content_consumed = True
    result.request = requests.Request("POST", TOKEN_URL).prepare()
    return result


class OAuthTests(unittest.TestCase):
    def test_config_valid_and_minimized(self):
        data = CONFIG.document()
        data["installed"]["project_id"] = "unneeded"
        self.assertEqual(ClientConfig.parse(data), CONFIG)
        self.assertNotIn("project_id", CONFIG.document()["installed"])

    def test_invalid_config(self):
        for value in (
            {},
            {"web": CONFIG.document()["installed"]},
            None,
            {"installed": {"client_id": "wrong"}},
            {
                "installed": {
                    **CONFIG.document()["installed"],
                    "token_uri": "https://attacker.invalid",
                }
            },
        ):
            with self.subTest(value=value), self.assertRaises(AccountError):
                ClientConfig.parse(value)

    def test_downloaded_legacy_auth_uri_normalizes_to_modern_endpoint(self):
        data = CONFIG.document()
        data["installed"]["auth_uri"] = "https://accounts.google.com/o/oauth2/auth"
        parsed = ClientConfig.parse(data)
        self.assertEqual(parsed.document()["installed"]["auth_uri"], AUTH_URL)
        for uri in (
            "http://accounts.google.com/o/oauth2/auth",
            "https://accounts.google.com.attacker.invalid/o/oauth2/auth",
            "https://accounts.google.com/o/oauth2/auth?redirect=other",
        ):
            data["installed"]["auth_uri"] = uri
            with self.subTest(uri=uri), self.assertRaises(AccountError):
                ClientConfig.parse(data)

    def test_missing_and_malformed_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "config.json"
            for content in (None, "bad-json", "[]", "x" * 65000):
                if content is not None:
                    path.write_text(content)
                with self.assertRaises(AccountError):
                    ClientConfig.load(path)

    def run_callback(self, mode="success", upload=False):
        from shortspublisher.auth import UPLOAD_SCOPES

        scopes = " ".join(sorted(UPLOAD_SCOPES)) if upload else SCOPE
        cancel = Event()
        captured = {}

        def browser(url):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            captured.update(query)
            self.assertEqual(f"{parsed.scheme}://{parsed.netloc}{parsed.path}", AUTH_URL)
            if mode == "cancel":
                cancel.set()
                return True
            if mode == "timeout":
                return True
            redirect = query["redirect_uri"][0]
            captured["redirect"] = redirect
            self.assertEqual(urlsplit(redirect).hostname, "127.0.0.1")
            args = {"state": query["state"][0]}
            args.update(
                {"error": "access_denied"} if mode == "deny" else {"code": "test-code-sensitive"}
            )
            # A browser cannot synchronously wait for this same worker's HTTP listener.
            import threading

            def visit():
                if mode in ("wrong-state", "wrong-path", "duplicate-state"):
                    from urllib.error import HTTPError

                    invalid = redirect + "?" + urlencode({"state": "incorrect", "code": "bad"})
                    if mode == "wrong-path":
                        invalid = redirect + "unexpected?" + urlencode(args)
                    elif mode == "duplicate-state":
                        invalid = redirect + "?" + urlencode(args) + "&state=duplicate"
                    with self.assertRaises(HTTPError) as ctx:
                        urlopen(invalid, timeout=2)
                    self.assertEqual(ctx.exception.code, 400)
                with urlopen(redirect + "?" + urlencode(args), timeout=2) as result:
                    self.assertEqual(result.status, 200)

            def checked_visit():
                try:
                    visit()
                except Exception as exc:
                    captured["thread_error"] = exc

            thread = threading.Thread(target=checked_visit)
            thread.start()
            captured["thread"] = thread
            return True

        def exchange(flow, **kwargs):
            self.assertEqual(kwargs["code"], "test-code-sensitive")
            self.assertGreaterEqual(len(flow.code_verifier), 43)
            import base64

            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(flow.code_verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            self.assertEqual(captured["code_challenge"], [challenge])
            flow.oauth2session.token = {
                "access_token": "test-access-sensitive",
                "refresh_token": "test-refresh-sensitive",
                "expires_in": 3600,
                "scope": scopes,
            }

        try:
            if mode in ("real-exchange", "exchange-error"):
                body = {
                    "access_token": "test-access-sensitive",
                    "refresh_token": "test-refresh-sensitive",
                    "expires_in": 3600,
                    "scope": scopes,
                    "token_type": "Bearer",
                }
                reply = (
                    response(200, body)
                    if mode == "real-exchange"
                    else response(
                        400, {"error": "invalid_grant", "error_description": "sensitive-error"}
                    )
                )
                with patch("requests.sessions.Session.request", return_value=reply) as request:
                    oauth = GoogleOAuth(browser, timeout=1)
                    authorize = oauth.authorize_upload if upload else oauth.authorize
                    tokens = authorize(CONFIG, cancel, lambda _: None)
                self.assertEqual(request.call_args.args[1], TOKEN_URL)
                data = request.call_args.kwargs["data"]
                self.assertEqual(data["code"], "test-code-sensitive")
                self.assertIn("code_verifier", data)
                self.assertNotIn("allow_redirects", data)
                self.assertFalse(request.call_args.kwargs["allow_redirects"])
            else:
                with patch(
                    "shortspublisher.auth.Flow.fetch_token", autospec=True, side_effect=exchange
                ):
                    tokens = GoogleOAuth(browser, timeout=0.05).authorize(
                        CONFIG, cancel, lambda _: None
                    )
        finally:
            if "thread" in captured:
                captured["thread"].join(3)
                self.assertFalse(captured["thread"].is_alive())
                if "thread_error" in captured:
                    raise captured["thread_error"]
        self.assertEqual(captured["scope"], [scopes])
        self.assertEqual(captured["code_challenge_method"], ["S256"])
        self.assertEqual(captured["access_type"], ["offline"])
        self.assertGreaterEqual(len(captured["state"][0]), 32)
        self.assertTrue(tokens.refresh)
        with self.assertRaises(OSError):
            urlopen(captured["redirect"], timeout=0.1)

    def test_upload_consent_official_exchange_pkce_both_scopes(self):
        self.run_callback("real-exchange", upload=True)

    def test_official_library_token_exchange(self):
        self.run_callback("real-exchange")

    def test_token_exchange_error_is_sanitized(self):
        with self.assertRaises(AccountError) as ctx:
            self.run_callback("exchange-error")
        self.assertNotIn("sensitive-error", str(ctx.exception))

    def test_actual_loopback_pkce_state_exchange_and_cleanup(self):
        self.run_callback()

    def test_wrong_state_does_not_consume_valid_callback(self):
        self.run_callback("wrong-state")

    def test_unexpected_callback_path(self):
        self.run_callback("wrong-path")

    def test_duplicate_callback_state(self):
        self.run_callback("duplicate-state")

    def test_user_denial(self):
        with self.assertRaisesRegex(AccountError, "denied"):
            self.run_callback("deny")

    def test_cancel(self):
        with self.assertRaisesRegex(AccountError, "cancelled"):
            self.run_callback("cancel")

    def test_timeout(self):
        with self.assertRaisesRegex(AccountError, "timed out"):
            self.run_callback("timeout")

    def test_browser_failure_sanitized(self):
        with self.assertRaises(AccountError) as ctx:
            GoogleOAuth(lambda _: (_ for _ in ()).throw(RuntimeError("secret-code"))).authorize(
                CONFIG, Event(), lambda _: None
            )
        self.assertNotIn("secret-code", str(ctx.exception))

    def test_token_missing_scope_refresh_or_bad_shape(self):
        for patch_values in (
            {"scope": "email"},
            {"refresh_token": ""},
            {"expires_in": "bad"},
            {"token_type": "other"},
        ):
            with self.subTest(patch_values=patch_values), self.assertRaises(AccountError):
                Tokens.parse(
                    {"access_token": "a", "refresh_token": "r", "expires_in": 3600, **patch_values}
                )

    def test_refresh_rotation_and_preservation(self):
        oauth = GoogleOAuth()
        tokens = Tokens("old-a", "old-r", 0)
        for body, expected in [
            ({"access_token": "new-a", "expires_in": 3600}, "old-r"),
            ({"access_token": "new-a", "refresh_token": "new-r", "expires_in": 3600}, "new-r"),
        ]:
            with patch(
                "shortspublisher.auth.requests.post", return_value=response(200, body)
            ) as post:
                result = oauth.refresh(CONFIG, tokens, Event())
                self.assertEqual(result.refresh, expected)
                self.assertEqual(post.call_args.args[0], TOKEN_URL)
                self.assertEqual(post.call_args.kwargs["data"]["refresh_token"], "old-r")
                self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_refresh_invalid_grant_and_network(self):
        for code, kind in [(400, "reconnect"), (401, "reconnect"), (503, "offline")]:
            with patch(
                "shortspublisher.auth.requests.post",
                return_value=response(code, {"error": "private-server-message"}),
            ):
                with self.assertRaises(AccountError) as ctx:
                    GoogleOAuth().refresh(CONFIG, Tokens("a", "r", 0), Event())
                self.assertEqual(ctx.exception.kind, kind)
                self.assertNotIn("private-server", str(ctx.exception))

    def test_remote_revocation_uses_post_body_and_confirmed_status(self):
        for code in (200, 400, 503):
            with patch(
                "shortspublisher.auth.requests.post", return_value=response(code, {})
            ) as post:
                self.assertEqual(GoogleOAuth().revoke(Tokens("a", "r", 0)), code == 200)
                self.assertNotIn("token=", post.call_args.args[0])
                self.assertEqual(post.call_args.kwargs["data"], {"token": "r"})


class ApiTests(unittest.TestCase):
    def test_real_adapter_parameters_and_identity(self):
        body = {
            "items": [
                {
                    "id": CHANNEL.id,
                    "snippet": {
                        "title": CHANNEL.title,
                        "customUrl": CHANNEL.handle,
                        "description": "desc",
                        "thumbnails": {"default": {"url": "https://attacker.invalid/image"}},
                    },
                }
            ]
        }
        with patch("shortspublisher.youtube.requests.get", return_value=response(200, body)) as get:
            items = YouTubeClient().channels(Tokens("sensitive-a", "r", 0), Event())
        self.assertEqual(items[0].id, CHANNEL.id)
        self.assertEqual(items[0].thumbnail, "")
        self.assertEqual(get.call_args.kwargs["params"]["part"], "snippet")
        self.assertEqual(get.call_args.kwargs["params"]["mine"], "true")
        self.assertNotIn("sensitive-a", get.call_args.args[0])
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "Bearer sensitive-a"})

    def test_thumbnail_host_redirect_and_size_limits(self):
        client = YouTubeClient()
        with patch("shortspublisher.youtube.requests.get") as get:
            self.assertEqual(
                client.thumbnail(
                    Channel("id", "name", thumbnail="https://evil.invalid/image"), Event()
                ),
                b"",
            )
            get.assert_not_called()
        reply = Mock()
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        reply.status_code = 200
        reply.headers = {"Content-Type": "image/png"}
        reply.iter_content.return_value = iter([b"image"])
        with patch("shortspublisher.youtube.requests.get", return_value=reply) as get:
            self.assertEqual(
                client.thumbnail(
                    Channel("id", "name", thumbnail="https://yt3.ggpht.com/image"), Event()
                ),
                b"image",
            )
            self.assertNotIn("headers", get.call_args.kwargs)
            self.assertFalse(get.call_args.kwargs["allow_redirects"])
        reply.iter_content.return_value = iter([b"x" * 256001])
        with patch("shortspublisher.youtube.requests.get", return_value=reply):
            self.assertEqual(
                client.thumbnail(
                    Channel("id", "name", thumbnail="https://yt3.ggpht.com/image"), Event()
                ),
                b"",
            )

    def test_pagination(self):
        bodies = [
            response(
                200,
                {"items": [{"id": "one", "snippet": {"title": "One"}}], "nextPageToken": "next"},
            ),
            response(200, {"items": [{"id": "two", "snippet": {"title": "Two"}}]}),
        ]
        with patch("shortspublisher.youtube.requests.get", side_effect=bodies) as get:
            self.assertEqual(len(YouTubeClient().channels(Tokens("a", "r", 0), Event())), 2)
            self.assertEqual(get.call_args.kwargs["params"]["pageToken"], "next")

    def test_classified_errors_without_blind_retry(self):
        for status, reason, kind in [
            (401, "", "unauthorized"),
            (403, "insufficientPermissions", "permission"),
            (403, "quotaExceeded", "quota"),
            (400, "", "error"),
        ]:
            with patch(
                "shortspublisher.youtube.requests.get",
                return_value=response(status, {"error": {"errors": [{"reason": reason}]}}),
            ) as get:
                with self.assertRaises(AccountError) as ctx:
                    YouTubeClient().channels(Tokens("a", "r", 0), Event())
                self.assertEqual(ctx.exception.kind, kind)
                self.assertEqual(get.call_count, 1)

    def test_retry_bounded(self):
        for status in (429, 500, 503):
            cancel = Mock(spec=Event)
            cancel.is_set.return_value = False
            cancel.wait.return_value = False
            with patch(
                "shortspublisher.youtube.requests.get",
                side_effect=lambda *a, code=status, **k: response(code, {}),
            ) as get:
                with self.assertRaises(AccountError):
                    YouTubeClient().channels(Tokens("a", "r", 0), cancel)
                self.assertEqual(get.call_count, 3)
                self.assertEqual(cancel.wait.call_count, 2)

    def test_network_errors_and_cancel(self):
        cancel = Event()
        cancel.set()
        with patch("shortspublisher.youtube.requests.get") as get, self.assertRaises(AccountError):
            YouTubeClient().channels(Tokens("a", "r", 0), cancel)
        get.assert_not_called()
        cancel = Mock(spec=Event)
        cancel.is_set.return_value = False
        cancel.wait.return_value = False
        with patch(
            "shortspublisher.youtube.requests.get",
            side_effect=requests.ConnectionError("secret in exception"),
        ) as get:
            with self.assertRaises(AccountError) as ctx:
                YouTubeClient().channels(Tokens("a", "r", 0), cancel)
            self.assertNotIn("secret", str(ctx.exception))
            self.assertEqual(get.call_count, 3)


class AccountServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Library(Path(self.temp.name) / "library.sqlite3")
        self.library.initialize()
        self.repository = AccountRepository(self.library)
        self.credentials = MemoryCredentials()
        self.oauth = ControlledOAuth()
        self.youtube = ControlledYouTube()
        self.service = AccountService(self.repository, self.credentials, self.oauth, self.youtube)
        self.repository.save_config(CONFIG)

    def execute(self, action, argument=None):
        return self.service.execute(action, argument, Event())

    def connected(self):
        view = self.execute("connect")
        self.assertEqual(view.state, "Confirm channel")
        self.assertIsNone(self.repository.connection())
        view = self.execute("select", CHANNEL.id)
        self.assertTrue(view.state.startswith("Connected"), view.message)
        return view

    def test_connection_persistence_and_live_restore(self):
        connected = self.connected()
        service = AccountService(
            AccountRepository(Library(self.library.path)),
            self.credentials,
            self.oauth,
            self.youtube,
        )
        restored = service.execute("restore", None, Event())
        self.assertEqual(restored.connection.channel, CHANNEL)
        self.assertEqual(restored.connection.key, connected.connection.key)
        self.assertEqual(self.youtube.calls, 2)

    def test_missing_configuration(self):
        with closing(self.library.connect()) as db, db:
            db.execute("DELETE FROM account_config")
        view = self.execute("connect")
        self.assertFalse(view.configured)
        self.assertIn("JSON", view.message)

    def test_configuration_file_selection(self):
        path = Path(self.temp.name) / "client.json"
        path.write_text(json.dumps(CONFIG.document()))
        view = self.execute("configure", path)
        self.assertTrue(view.configured)
        self.assertEqual(self.repository.config(), CONFIG)

    def test_expiration_refresh_rotation_persisted(self):
        view = self.connected()
        key = view.connection.key
        self.credentials.values[key].expires_at = 0
        self.execute("refresh")
        self.assertEqual(self.oauth.refresh_count, 1)
        self.assertEqual(self.credentials.values[key].refresh, "test-refresh-rotated")

    def test_revoked_credentials_show_reconnect_and_keep_metadata(self):
        view = self.connected()
        self.credentials.values[view.connection.key].expires_at = 0
        self.oauth.error = AccountError("Revoked", "reconnect")
        self.assertEqual(self.execute("refresh").state, "Reconnection required")
        self.assertIsNotNone(self.repository.connection())

    def test_missing_keychain_entry(self):
        self.connected()
        self.credentials.values.clear()
        self.assertEqual(self.execute("restore").state, "Reconnection required")

    def test_keychain_write_failure_never_connects(self):
        self.execute("connect")
        self.credentials.fail = "put"
        view = self.execute("select", CHANNEL.id)
        self.assertEqual(view.state, "Verification pending")
        self.assertIsNone(self.repository.connection())

    def test_keychain_read_and_delete_failure_preserve_identity(self):
        self.connected()
        for operation, action in [("get", "refresh"), ("delete", "disconnect")]:
            self.credentials.fail = operation
            view = self.execute(action)
            self.assertEqual(view.state, "Verification pending")
            self.assertIsNotNone(self.repository.connection())

    def test_no_channel(self):
        self.youtube.items = []
        view = self.execute("connect")
        self.assertIn("No YouTube channel", view.message)
        self.assertIsNone(self.service.pending)
        self.assertFalse(self.credentials.values)

    def test_multiple_channels_require_explicit_id(self):
        other = Channel("other-id", "Another channel")
        self.youtube.items = [CHANNEL, other]
        view = self.execute("connect")
        self.assertEqual(len(view.choices), 2)
        self.assertIsNone(self.repository.connection())
        view = self.execute("select", other.id)
        self.assertEqual(view.connection.channel.id, other.id)

    def test_unexpected_identity_does_not_replace(self):
        self.connected()
        self.youtube.items = [Channel("different", "Unexpected")]
        self.assertEqual(self.execute("refresh").state, "Reconnection required")
        self.assertEqual(self.repository.connection().channel.id, CHANNEL.id)

    def test_temporary_network_failure_preserves_credentials(self):
        self.connected()
        before = dict(self.credentials.values)
        self.youtube.error = AccountError("Offline", "offline")
        self.assertEqual(self.execute("refresh").state, "Temporarily offline")
        self.assertEqual(before, self.credentials.values)

    def test_cancel_and_expired_pending(self):
        self.execute("connect")
        self.execute("discard")
        self.assertIsNone(self.service.pending)
        self.assertFalse(self.credentials.values)
        self.execute("connect")
        tokens, choices, _ = self.service.pending
        self.service.pending = (tokens, choices, time.monotonic() - 301)
        self.assertIn("expired", self.execute("select", CHANNEL.id).message)

    def test_local_disconnect_and_revoke_outcomes(self):
        for remote, success in [(False, True), (True, True), (True, False)]:
            self.connected()
            self.oauth.revoke_ok = success
            view = self.execute("disconnect", remote)
            self.assertEqual(view.state, "Disconnected")
            self.assertFalse(self.credentials.values)
            self.assertIsNone(self.repository.connection())
            self.assertIn("confirmed" if remote else "not revoked", view.message)

    def test_pending_confirmation_rejects_changed_client(self):
        self.execute("connect")
        self.repository.save_config(
            ClientConfig("999999999-other.apps.googleusercontent.com", "another-public-value")
        )
        view = self.execute("select", CHANNEL.id)
        self.assertIn("selection changed", view.message)
        self.assertFalse(self.credentials.values)

    def test_disconnect_preserves_videos_and_reviews(self):
        fixture = Path(__file__).parent / "fixtures" / "library-v2.sqlite3"
        with sqlite3.connect(fixture) as source, sqlite3.connect(self.library.path) as target:
            source.backup(target)
        self.library.initialize()
        self.repository.save_config(CONFIG)
        with closing(self.library.connect()) as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM preparations")]
            videos = [tuple(row) for row in db.execute("SELECT * FROM videos")]
        self.connected()
        self.execute("disconnect")
        with closing(self.library.connect()) as db:
            self.assertEqual(
                before, [tuple(row) for row in db.execute("SELECT * FROM preparations")]
            )
            self.assertEqual(videos, [tuple(row) for row in db.execute("SELECT * FROM videos")])

    def test_cannot_replace_active_configuration_or_connect(self):
        original = self.connected().connection
        self.execute("connect")
        self.execute("configure", Path("unused"))
        self.assertEqual(self.repository.connection(), original)

    def test_database_failure_after_secret_write_cleans_new_secret(self):
        self.execute("connect")
        with patch.object(
            self.repository, "save", side_effect=sqlite3.OperationalError("injected")
        ):
            view = self.execute("select", CHANNEL.id)
        self.assertFalse(view.state.startswith("Connected"))
        self.assertFalse(self.credentials.values)

    def test_cross_instance_lock_rejects_concurrent_refresh(self):
        self.connected()
        other = AccountService(self.repository, self.credentials, self.oauth, self.youtube)
        with self.service.lock:
            view = other.execute("refresh", None, Event())
        self.assertIn("Another account", view.message)
        self.assertEqual(self.oauth.refresh_count, 0)

    def test_no_tokens_in_sqlite_or_repr_or_logs(self):
        with self.assertNoLogs("shortspublisher", level=logging.DEBUG):
            self.connected()
            self.execute("refresh")
        raw = self.library.path.read_bytes()
        for value in (b"test-access-sensitive", b"test-refresh-sensitive"):
            self.assertNotIn(value, raw)
        self.assertNotIn("sensitive", repr(next(iter(self.credentials.values.values()))))

    def test_keychain_corrupt_payload_never_falls_back(self):
        store = KeychainStore("ShortsPublisher.Tests.Unused")
        backend = Mock()
        backend.get_password.return_value = "broken-json"
        with patch.object(store, "backend", return_value=backend), self.assertRaises(AccountError):
            store.get("unused")
        backend.set_password.assert_not_called()

    def test_401_attempts_refresh_once(self):
        connected = self.connected()
        self.youtube.error = AccountError("Rejected", "unauthorized")
        view = self.execute("refresh")
        self.assertEqual(view.state, "Reconnection required")
        self.assertEqual(self.oauth.refresh_count, 1)
        self.assertEqual(
            self.credentials.values[connected.connection.key].refresh, "test-refresh-rotated"
        )

    def test_keychain_backend_errors_sanitized(self):
        store = KeychainStore("ShortsPublisher.Tests.Unused")
        with patch.object(store, "backend", side_effect=RuntimeError("token-sensitive")):
            for operation in (
                lambda: store.get("unused"),
                lambda: store.put("unused", Tokens("a", "r", 0)),
                lambda: store.delete("unused"),
            ):
                with self.assertRaises(AccountError) as ctx:
                    operation()
                self.assertNotIn("token-sensitive", str(ctx.exception))

    def test_schema_v2_migration_preserves_preparations(self):
        # Genuine Stage 3 native SQLite backup contains saved metadata and completed review.
        fixture = Path(__file__).parent / "fixtures" / "library-v2.sqlite3"
        with sqlite3.connect(fixture) as source, sqlite3.connect(self.library.path) as target:
            source.backup(target)
        with closing(self.library.connect()) as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM preparations")]
            videos = [tuple(row) for row in db.execute("SELECT * FROM videos")]
        self.library.initialize()
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 11)
            self.assertEqual(
                before, [tuple(row) for row in db.execute("SELECT * FROM preparations")]
            )
            self.assertEqual(videos, [tuple(row) for row in db.execute("SELECT * FROM videos")])
        self.assertTrue(before)

    def test_schema_migration_failure_rollback(self):
        with closing(self.library.connect()) as db, db:
            db.execute("DROP TABLE account_config")
            db.execute("PRAGMA user_version=2")
        with self.assertRaises(sqlite3.OperationalError):
            self.library.initialize()  # conflicting account_connection table
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertIsNone(
                db.execute("SELECT name FROM sqlite_master WHERE name='account_config'").fetchone()
            )

    def test_future_version_refused(self):
        with closing(self.library.connect()) as db, db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(LibraryError):
            self.library.initialize()
