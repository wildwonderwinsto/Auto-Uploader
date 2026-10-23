"""Buffer public OAuth client (PKCE) and GraphQL API. Tokens never enter messages or logs.

Verified against developers.buffer.com (2026-10-17): authorization code + PKCE S256 is required;
public clients send no secret; redirect URIs must be pre-registered HTTPS (even localhost) and
match exactly; refresh tokens rotate and reusing an old one revokes the whole grant.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from filelock import FileLock, Timeout

AUTH_URL = "https://auth.buffer.com/auth"
TOKEN_URL = "https://auth.buffer.com/token"
API_URL = "https://api.buffer.com"
SCOPES = ("account:read", "posts:read", "posts:write", "offline_access")
# The hosted HTTPS relay page forwards Buffer's redirect to this loopback listener.
CALLBACK_PORT = 47633
CALLBACK_PATH = "/buffer/callback"
REFRESH_MARGIN = 120  # seconds


class BufferError(Exception):
    """kind: config, denied, auth, reconnect, rate, network, uncertain, api."""

    def __init__(self, message: str, kind: str = "api", retry_after: float | None = None):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


@dataclass(frozen=True)
class BufferClientConfig:
    client_id: str
    redirect_uri: str

    @classmethod
    def parse(cls, value: dict[str, Any]) -> BufferClientConfig:
        client_id = str(value.get("client_id", "")).strip()
        redirect = str(value.get("redirect_uri", "")).strip()
        parts = urlsplit(redirect)
        if not client_id or len(client_id) > 200 or any(c.isspace() for c in client_id):
            raise BufferError(
                "Enter the Client ID of your Buffer public app client (Buffer → Settings → API → App Clients).",
                "config",
            )
        if parts.scheme != "https" or not parts.netloc or parts.fragment or parts.username:
            raise BufferError(
                "The redirect URI must be the exact HTTPS address registered for your Buffer app client (Buffer requires HTTPS, even for localhost).",
                "config",
            )
        return cls(client_id, redirect)


@dataclass(frozen=True)
class BufferTokens:
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str

    def encode(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def decode(cls, value: str) -> BufferTokens:
        data = json.loads(value)
        tokens = cls(
            str(data["access_token"]),
            str(data["refresh_token"]),
            float(data["expires_at"]),
            str(data.get("scope", "")),
        )
        if not tokens.access_token or not tokens.refresh_token:
            raise ValueError("incomplete")
        return tokens


class SecretStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode()


def authorization_url(config: BufferClientConfig, state: str, challenge: str) -> str:
    return (
        AUTH_URL
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": config.client_id,
                "redirect_uri": config.redirect_uri,
                "scope": " ".join(SCOPES),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                # Buffer's documented example includes it; required for the offline_access grant.
                "prompt": "consent",
            }
        )
    )


def parse_callback(query: str, expected_state: str) -> str:
    """Return the authorization code from a callback query or pasted relay text."""
    text = query.strip()
    if "?" in text:
        text = text.split("?", 1)[1]
    values = {k: v[0] for k, v in parse_qs(text.lstrip("#&"), keep_blank_values=True).items()}
    state = values.get("state", "")
    if not hmac.compare_digest(state.encode(), expected_state.encode()):
        raise BufferError(
            "The sign-in response did not match this request. Start connecting again.",
            "denied",
        )
    if values.get("error"):
        if values["error"] == "access_denied":
            raise BufferError("Buffer access was not granted. Nothing was connected.", "denied")
        raise BufferError("Buffer could not complete sign-in. Try again.", "denied")
    code = values.get("code", "")
    if not code or len(code) > 2048:
        raise BufferError("No authorization code was returned. Start again.", "denied")
    return code


class CallbackListener:
    """One-shot listener bound to 127.0.0.1 only; accepts a single matching GET."""

    def __init__(self, port: int = CALLBACK_PORT) -> None:
        self.result: str | None = None
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib API
                path, _, query = self.path.partition("?")
                if path != CALLBACK_PATH:
                    self.send_error(404)
                    return
                listener.result = query
                body = (
                    b"<!doctype html><meta charset=utf-8><title>ShortsScheduler</title>"
                    b"<p style='font:16px system-ui'>You can close this tab and return to ShortsScheduler.</p>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:  # never log callback queries
                return

        try:
            self.server = HTTPServer(("127.0.0.1", port), Handler)
        except OSError as exc:
            raise BufferError(
                f"Port {port} on this Mac is busy, so the browser cannot return here. Paste the code shown by the relay page instead.",
                "config",
            ) from exc
        self.server.timeout = 0.2

    def wait(self, cancel: Event, timeout: float = 600) -> str:
        deadline = time.monotonic() + timeout
        try:
            while self.result is None:
                if cancel.is_set():
                    raise BufferError("Sign-in was cancelled. Nothing was connected.", "denied")
                if time.monotonic() > deadline:
                    raise BufferError("Sign-in timed out. Nothing was connected.", "denied")
                self.server.handle_request()
            return self.result
        finally:
            self.server.server_close()


def token_response(response: requests.Response, now: float) -> BufferTokens:
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code != 200:
        error = data.get("error", "") if isinstance(data, dict) else ""
        if error in ("invalid_grant", "invalid_client"):
            raise BufferError(
                "Buffer no longer accepts this connection. Reconnect it in Accounts.", "reconnect"
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise BufferError("Buffer is temporarily unavailable. Try again later.", "network")
        raise BufferError("Buffer rejected the sign-in request.", "denied")
    try:
        access, refresh = str(data["access_token"]), str(data.get("refresh_token", ""))
        lifetime = float(data.get("expires_in", 3600))
    except (KeyError, TypeError, ValueError):
        raise BufferError("Buffer returned an unreadable sign-in response.", "denied") from None
    if not refresh:
        raise BufferError(
            "Buffer did not return a refresh token. Ensure offline access is allowed.", "denied"
        )
    return BufferTokens(access, refresh, now + lifetime, str(data.get("scope", "")))


class BufferOAuth:
    def __init__(self, session: Any | None = None, clock: Callable[[], float] = time.time) -> None:
        self.session = session or requests.Session()
        self.clock = clock

    def exchange(self, config: BufferClientConfig, code: str, verifier: str) -> BufferTokens:
        try:
            response = self.session.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": config.redirect_uri,
                    "client_id": config.client_id,
                    "code_verifier": verifier,
                },
                timeout=30,
            )
        except requests.RequestException:
            raise BufferError(
                "Could not reach Buffer to finish sign-in. Start connecting again.", "network"
            ) from None
        return token_response(response, self.clock())

    def refresh(self, config: BufferClientConfig, tokens: BufferTokens) -> BufferTokens:
        try:
            response = self.session.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token,
                    "client_id": config.client_id,
                },
                timeout=30,
            )
        except requests.RequestException:
            # The request may have rotated the token on Buffer's side; reusing the old one would
            # revoke the grant, so never retry automatically.
            raise BufferError(
                "Buffer sign-in renewal had an uncertain result. Reconnect in Accounts to continue safely.",
                "reconnect",
            ) from None
        try:
            return token_response(response, self.clock())
        except BufferError as exc:
            if response.status_code == 429:
                raise BufferError("Buffer asked to wait before renewing sign-in.", "rate") from None
            if exc.kind == "network":
                raise BufferError(
                    "Buffer sign-in renewal failed with an uncertain result. Reconnect in Accounts to continue safely.",
                    "reconnect",
                ) from None
            raise


class BufferAuthority:
    """Owns the rotating refresh token across the GUI and background worker processes."""

    def __init__(
        self,
        store: SecretStore,
        lock_path: Path,
        config: Callable[[], BufferClientConfig | None],
        key: Callable[[], str | None],
        oauth: BufferOAuth | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store, self.lock_path, self.config, self.key = store, lock_path, config, key
        self.oauth = oauth or BufferOAuth(clock=clock)
        self.clock = clock

    def load(self, key: str) -> BufferTokens | None:
        try:
            value = self.store.get(key)
            return BufferTokens.decode(value) if value else None
        except Exception:
            raise BufferError(
                "Cannot read the Buffer sign-in from macOS Keychain. Unlock Keychain and retry.",
                "auth",
            ) from None

    def save(self, key: str, tokens: BufferTokens) -> None:
        try:
            self.store.put(key, tokens.encode())
        except Exception:
            raise BufferError(
                "Could not save the renewed Buffer sign-in in Keychain. Reconnect in Accounts.",
                "reconnect",
            ) from None

    def access_token(self) -> str:
        config, key = self.config(), self.key()
        if not config or not key:
            raise BufferError("Connect an account via Buffer first.", "auth")
        try:
            with FileLock(str(self.lock_path), timeout=45):
                # Always re-read inside the lock: another process may already have rotated it.
                tokens = self.load(key)
                if tokens is None:
                    raise BufferError("Connect an account via Buffer first.", "auth")
                if tokens.expires_at - REFRESH_MARGIN > self.clock():
                    return tokens.access_token
                renewed = self.oauth.refresh(config, tokens)
                self.save(key, renewed)
                return renewed.access_token
        except Timeout:
            raise BufferError(
                "Another ShortsScheduler process is renewing the Buffer sign-in. Try again shortly.",
                "network",
            ) from None


ORGANIZATIONS = "query { account { organizations { id name } } }"
CHANNELS = """query Channels($input: ChannelsInput!) {
  channels(input: $input) { id name displayName service type avatar isDisconnected isLocked }
}"""
CREATE_POST = """mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id status schedulingType shareMode dueAt externalLink } }
    ... on MutationError { message }
  }
}"""
POST = """query Post($input: PostInput!) {
  post(input: $input) {
    id status schedulingType dueAt sentAt externalLink
    error { message supportUrl }
    assets { source }
  }
}"""
POSTS = """query Posts($input: PostsInput!, $first: Int) {
  posts(input: $input, first: $first) {
    edges { node { id status schedulingType dueAt sentAt externalLink assets { source } } }
  }
}"""
# Read-only. Buffer serves metrics only to a personal API key (insightsRead), not app clients.
POST_METRICS = """query PostMetrics($input: PostInput!) {
  post(input: $input) { id metrics { type value } metricsUpdatedAt }
}"""
DELETE_POST = """mutation DeletePost($input: DeletePostInput!) {
  deletePost(input: $input) { __typename ... on MutationError { message } }
}"""


class BufferAPI:
    def __init__(self, token: Callable[[], str], session: Any | None = None) -> None:
        self.token, self.session = token, session or requests.Session()

    def call(
        self, query: str, variables: dict[str, Any] | None = None, *, write: bool = False
    ) -> Any:
        try:
            response = self.session.post(
                API_URL,
                json={"query": query, "variables": variables or {}},
                headers={"Authorization": f"Bearer {self.token()}"},
                timeout=45,
            )
        except requests.RequestException:
            raise BufferError(
                "The connection to Buffer was interrupted"
                + ("; the post may or may not have been created." if write else "."),
                "uncertain" if write else "network",
            ) from None
        if response.status_code == 401:
            raise BufferError(
                "Buffer no longer accepts this sign-in. Reconnect it in Accounts.", "auth"
            )
        if response.status_code == 429:
            try:
                wait = float(response.headers.get("Retry-After", "900"))
            except ValueError:
                wait = 900.0
            # A 429 is rejected before processing, so a later identical request is safe.
            raise BufferError(
                "Buffer's API limit was reached; waiting before retrying.", "rate", wait
            )
        if response.status_code >= 500:
            raise BufferError(
                "Buffer had a server problem"
                + ("; the post may or may not have been created." if write else "."),
                "uncertain" if write else "network",
            )
        try:
            body = response.json()
        except ValueError:
            raise BufferError(
                "Buffer returned an unreadable response.", "uncertain" if write else "network"
            ) from None
        if body.get("errors"):
            raise BufferError(
                "Buffer rejected the request: "
                + "; ".join(str(e.get("message", "")) for e in body["errors"])[:300],
                "api",
            )
        return body.get("data") or {}

    def organizations(self) -> list[dict[str, Any]]:
        account = self.call(ORGANIZATIONS).get("account") or {}
        return list(account.get("organizations") or [])

    def all_channels(self) -> list[dict[str, Any]]:
        found = []
        for org in self.organizations():
            data = self.call(CHANNELS, {"input": {"organizationId": org["id"]}})
            for channel in data.get("channels") or []:
                found.append({**channel, "organization_id": org["id"]})
        return found

    def channels(self, service: str) -> list[dict[str, Any]]:
        """Every channel of one service (Buffer's `Service` name) across the organizations."""
        return [c for c in self.all_channels() if str(c.get("service", "")).lower() == service]

    def tiktok_channels(self) -> list[dict[str, Any]]:
        return self.channels("tiktok")

    def create_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.call(CREATE_POST, {"input": payload}, write=True).get("createPost") or {}
        if result.get("__typename") == "PostActionSuccess" and (result.get("post") or {}).get("id"):
            return dict(result["post"])
        # A typed MutationError means Buffer processed and refused the request: definite.
        raise BufferError(str(result.get("message") or "Buffer did not create the post."), "api")

    def post(self, identity: str) -> dict[str, Any] | None:
        return self.call(POST, {"input": {"id": identity}}).get("post")

    def post_metrics(self, identity: str) -> dict[str, int]:
        """Counts Buffer has gathered for one post (daily); missing types mean "not reported"."""
        post = self.call(POST_METRICS, {"input": {"id": identity}}).get("post") or {}
        values: dict[str, int] = {}
        for metric in post.get("metrics") or []:
            try:
                values[str(metric["type"])] = int(float(metric["value"]))
            except (KeyError, TypeError, ValueError):
                continue
        return values

    def recent_posts(
        self, organization: str, channel: str, first: int = 50
    ) -> list[dict[str, Any]]:
        data = self.call(
            POSTS,
            {
                "input": {"organizationId": organization, "filter": {"channelIds": [channel]}},
                "first": first,
            },
        )
        return [edge["node"] for edge in ((data.get("posts") or {}).get("edges") or [])]

    def delete_post(self, identity: str) -> None:
        result = (
            self.call(DELETE_POST, {"input": {"id": identity}}, write=True).get("deletePost") or {}
        )
        if result.get("message") or "Error" in str(result.get("__typename", "")):
            raise BufferError(
                str(result.get("message") or "Buffer did not delete the post."), "api"
            )
