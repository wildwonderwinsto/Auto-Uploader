"""Account orchestration and nonsensitive persistence, independent of Qt."""

import json
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from typing import Any

from filelock import FileLock, Timeout

from .auth import UPLOAD_SCOPES, AccountError, ClientConfig, GoogleOAuth, Tokens, check_cancel
from .credentials import CredentialStore
from .library import Library, now
from .youtube import Channel, YouTubeClient


@dataclass(frozen=True)
class Connection:
    key: str
    channel: Channel
    verified_at: str


@dataclass(frozen=True)
class AccountView:
    state: str
    message: str
    configured: bool
    connection: Connection | None = None
    choices: tuple[Channel, ...] = ()
    thumbnail: bytes = b""
    upload_allowed: bool = False


class AccountRepository:
    def __init__(self, library: Library) -> None:
        self.library = library

    def config(self) -> ClientConfig | None:
        with closing(self.library.connect()) as db:
            row = db.execute(
                "SELECT client_config FROM account_config WHERE platform='youtube'"
            ).fetchone()
            return ClientConfig.parse(json.loads(row[0])) if row else None

    def save_config(self, config: ClientConfig) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO account_config VALUES ('youtube', ?)",
                (json.dumps(config.document()),),
            )

    def connection(self) -> Connection | None:
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM account_connection WHERE platform='youtube'").fetchone()
            return (
                Connection(
                    row["credential_key"], Channel(**json.loads(row["channel"])), row["verified_at"]
                )
                if row
                else None
            )

    def save(self, connection: Connection) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO account_connection VALUES ('youtube', ?, ?, ?)",
                (connection.key, json.dumps(asdict(connection.channel)), connection.verified_at),
            )

    def clear(self) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute("DELETE FROM account_connection WHERE platform='youtube'")


class AccountService:
    def __init__(
        self,
        repository: AccountRepository,
        credentials: CredentialStore,
        oauth: GoogleOAuth | None = None,
        youtube: YouTubeClient | None = None,
    ) -> None:
        self.repository, self.credentials = repository, credentials
        self.oauth, self.youtube = oauth or GoogleOAuth(), youtube or YouTubeClient()
        self.pending: tuple[Tokens, tuple[Channel, ...], float] | None = None
        self.pending_config: ClientConfig | None = None
        self.pending_verified_at = ""
        # Serializes refresh, replacement and removal across threads AND app instances.
        self.lock = FileLock(str(repository.library.path.with_suffix(".accounts.lock")))

    def execute(
        self,
        action: str,
        argument: Any,
        cancel: Event,
        progress: Callable[[str], None] = lambda message: None,
    ) -> AccountView:
        try:
            self.repository.library.path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock.acquire(timeout=0):
                check_cancel(cancel)
                self.repository.library.initialize()
                return self._execute(action, argument, cancel, progress)
        except Timeout:
            return AccountView(
                "Verification pending",
                "Another account operation is active. Wait for it to finish, then refresh.",
                False,
            )
        except AccountError as exc:
            return self.failure(exc)
        except Exception:
            # Never format an SDK/network/Keychain exception or its traceback.
            return AccountView(
                "Verification pending",
                "Account operation failed. Check database access and retry. No connection is confirmed.",
                False,
            )

    def statistics(self, video_ids: list[str], cancel: Event) -> dict[str, Any]:
        """Read-only view counts for the connected channel; empty when unavailable.

        Never holds the account lock during the reads: scheduled uploads need it within their
        due window. A needed token refresh takes the lock briefly, or is skipped if it is busy.
        """
        empty: dict[str, Any] = {"channel": {}, "videos": {}}
        connection, config = self.repository.connection(), self.repository.config()
        tokens = self.credentials.get(connection.key) if connection and config else None
        if not connection or not config or tokens is None:
            return empty
        if tokens.expires_at <= time.time() + 60:
            try:
                with self.lock.acquire(timeout=0):
                    tokens = self.credentials.get(connection.key) or tokens
                    if tokens.expires_at <= time.time() + 60:
                        tokens = self.oauth.refresh(config, tokens, cancel)
                        self.credentials.put(connection.key, tokens)
            except Timeout:
                return empty
        return self.youtube.statistics(tokens, connection.channel.id, video_ids, cancel)

    def failure(self, error: AccountError) -> AccountView:
        try:
            connection = self.repository.connection()
            configured = self.repository.config() is not None
        except Exception:
            connection, configured = None, False
        state = {
            "offline": "Temporarily offline",
            "quota": "Verification pending",
            "reconnect": "Reconnection required",
            "unauthorized": "Reconnection required",
            "permission": "Reconnection required",
            "storage": "Verification pending",
            "cancelled": "Verification pending" if connection else "Disconnected",
        }.get(error.kind, "Verification pending" if connection else "Disconnected")
        message = str(error)
        if state == "Reconnection required" and connection:
            message += " Use Disconnect to remove local authorization, then Connect YouTube again."
        return AccountView(state, message, configured, connection)

    def _execute(
        self, action: str, argument: Any, cancel: Event, progress: Callable[[str], None]
    ) -> AccountView:
        config, connection = self.repository.config(), self.repository.connection()
        if action == "configure":
            if connection:
                raise AccountError(
                    "Disconnect the existing authorization before replacing the Desktop client configuration.",
                    "configuration",
                )
            self.pending = None
            self.repository.save_config(ClientConfig.load(Path(argument)))
            return AccountView(
                "Disconnected",
                "Desktop client configured. Connect YouTube to authorize read-only access.",
                True,
            )
        if action == "discard":
            self.pending = None
            return AccountView(
                "Verification pending" if connection else "Disconnected",
                "Channel selection cancelled. No new authorization was stored.",
                config is not None,
                connection,
            )
        if action == "disconnect":
            self.pending = None
            message = "Local authorization removed. Google access was not revoked."
            if connection:
                tokens = self.credentials.get(connection.key) if argument else None
                revoked = self.oauth.revoke(tokens) if tokens else False
                self.credentials.delete(connection.key)
                self.repository.clear()
                if argument:
                    message = (
                        "Local authorization removed. Google confirmed revocation."
                        if revoked
                        else "Local authorization removed. Google revocation was not confirmed; remove access in your Google Account permissions when online."
                    )
            return AccountView("Disconnected", message, config is not None)
        if not config:
            return AccountView(
                "Disconnected",
                "First choose your Google Desktop client JSON in Settings (one-time setup), then connect.",
                False,
                connection,
            )
        if action == "authorize_upload":
            if not connection:
                raise AccountError("Connect and confirm the intended channel first.")
            old = self.credentials.get(connection.key)
            if old is None:
                raise AccountError(
                    "Existing authorization is missing. Reconnect first.", "reconnect"
                )
            candidate = self.oauth.authorize_upload(config, cancel, progress)
            if not UPLOAD_SCOPES.issubset(candidate.scopes):
                raise AccountError(
                    "Upload permission was not granted. Existing connection was preserved."
                )
            channels = self.youtube.channels(candidate, cancel)
            if len(channels) != 1 or channels[0].id != connection.channel.id:
                raise AccountError(
                    "Additional consent returned a different or ambiguous channel. Existing authorization was preserved; resolve the account choice before retrying.",
                    "identity",
                )
            if not candidate.refresh:
                # A previous read-only refresh token may NOT carry the newly granted scope.
                probe = Tokens(candidate.access, old.refresh, candidate.expires_at, old.scopes)
                candidate = self.oauth.refresh(config, probe, cancel)
                if not UPLOAD_SCOPES.issubset(candidate.scopes):
                    raise AccountError(
                        "Google did not return renewable upload permission. Existing connection was preserved; retry consent."
                    )
            check_cancel(cancel)
            self.credentials.put(connection.key, candidate)
            updated = Connection(connection.key, connection.channel, now())
            self.repository.save(updated)
            return AccountView(
                "Connected · recently verified",
                "Read-only and upload permissions verified for this channel. Uploads require a separate visibility-specific confirmation.",
                True,
                updated,
                upload_allowed=True,
            )
        if action == "connect":
            if connection:
                raise AccountError(
                    "Disconnect first to replace authorization. This protects the existing channel from accidental replacement."
                )
            self.pending = None
            tokens = self.oauth.authorize(config, cancel, progress)
            progress("Retrieving authorized YouTube channels…")
            choices = tuple(self.youtube.channels(tokens, cancel))
            check_cancel(cancel)
            if not choices:
                raise AccountError(
                    "No YouTube channel was returned. Create a channel or authorize the intended YouTube/Brand Account, then reconnect.",
                    "identity",
                )
            self.pending = (tokens, choices, time.monotonic())
            self.pending_config = config
            self.pending_verified_at = now()
            return AccountView(
                "Confirm channel",
                "Confirm the channel ID below before activating this authorization. If it is unexpected, cancel and choose the intended account in Google.",
                True,
                choices=choices,
            )
        if action == "select":
            if not self.pending or time.monotonic() - self.pending[2] > 300:
                self.pending = None
                raise AccountError("Channel confirmation expired. Connect YouTube again.")
            tokens, choices, _ = self.pending
            selected = next((channel for channel in choices if channel.id == argument), None)
            if selected is None or connection or config != self.pending_config:
                raise AccountError("Channel selection changed. Cancel and connect again.")
            check_cancel(cancel)
            key = uuid.uuid4().hex
            self.credentials.put(key, tokens)
            connected = Connection(key, selected, self.pending_verified_at)
            try:
                check_cancel(cancel)
                self.repository.save(connected)
            except Exception:
                self.credentials.delete(key)
                raise
            self.pending = None
            return AccountView(
                "Connected · recently verified",
                "Read-only connection verified. Uploads require separate permission and confirmation.",
                True,
                connected,
                thumbnail=self.youtube.thumbnail(selected, cancel),
                upload_allowed=UPLOAD_SCOPES.issubset(tokens.scopes),
            )
        if action not in ("restore", "refresh"):
            raise AccountError("Unknown account action.")
        if not connection:
            return AccountView(
                "Disconnected",
                "Connect YouTube to verify your channel. Only read-only access is requested.",
                True,
            )
        progress("Verifying stored authorization with YouTube…")
        tokens = self.credentials.get(connection.key)
        if tokens is None:
            raise AccountError(
                "Stored channel details exist, but Keychain authorization is missing. Disconnect locally, then reconnect.",
                "reconnect",
            )
        refreshed = False
        if tokens.expires_at <= time.time() + 60:
            tokens = self.oauth.refresh(config, tokens, cancel)
            self.credentials.put(connection.key, tokens)
            refreshed = True
        check_cancel(cancel)
        try:
            choices = tuple(self.youtube.channels(tokens, cancel))
        except AccountError as exc:
            if exc.kind != "unauthorized" or refreshed:
                raise
            tokens = self.oauth.refresh(config, tokens, cancel)
            self.credentials.put(connection.key, tokens)
            choices = tuple(self.youtube.channels(tokens, cancel))
        check_cancel(cancel)
        selected = next(
            (channel for channel in choices if channel.id == connection.channel.id), None
        )
        if not selected:
            raise AccountError(
                "The saved channel ID was not returned by this authorization. Disconnect and reconnect to the intended channel; cached identity was kept.",
                "reconnect",
            )
        updated = Connection(connection.key, selected, now())
        self.repository.save(updated)
        return AccountView(
            "Connected · recently verified",
            "Channel verified with YouTube just now. Uploads require separate permission and confirmation.",
            True,
            updated,
            thumbnail=self.youtube.thumbnail(selected, cancel),
            upload_allowed=UPLOAD_SCOPES.issubset(tokens.scopes),
        )
