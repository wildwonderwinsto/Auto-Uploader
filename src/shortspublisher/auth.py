"""Google desktop OAuth; secret values never enter messages or diagnostic logs."""

import json
import logging
import secrets
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests
from google_auth_oauthlib.flow import Flow  # type: ignore[import-untyped]

SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
UPLOAD_SCOPES = frozenset((SCOPE, UPLOAD_SCOPE))
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# These dependencies can log request bodies at DEBUG. Never allow OAuth wire logging.
for _name in ("oauthlib", "requests_oauthlib", "google.auth", "urllib3"):
    logging.getLogger(_name).setLevel(logging.CRITICAL + 1)


class AccountError(Exception):
    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


def check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise AccountError("Operation cancelled. No authorization will be activated.", "cancelled")


@dataclass(frozen=True, repr=False)
class ClientConfig:
    client_id: str
    client_secret: str = field(repr=False)

    @classmethod
    def parse(cls, value: Any) -> "ClientConfig":
        try:
            item = value["installed"]
            client_id, secret = item["client_id"], item.get("client_secret", "")
            if not isinstance(client_id, str) or not client_id.endswith(
                ".apps.googleusercontent.com"
            ):
                raise ValueError
            if not isinstance(secret, str) or len(client_id) > 256 or len(secret) > 512:
                raise ValueError
            if any(c.isspace() for c in client_id) or len(client_id) < 30:
                raise ValueError
            if (
                item.get("auth_uri", AUTH_URL)
                not in (AUTH_URL, "https://accounts.google.com/o/oauth2/auth")
                or item.get("token_uri", TOKEN_URL) != TOKEN_URL
            ):
                raise ValueError
            # Google downloads can contain the legacy auth URI. document() always
            # supplies our fixed modern endpoint; never follow a configurable URL.
            return cls(client_id, secret)
        except (KeyError, TypeError, ValueError):
            raise AccountError(
                "Choose a valid Google OAuth JSON for a Desktop application, with Google's official endpoints.",
                "configuration",
            ) from None

    @classmethod
    def load(cls, path: Path) -> "ClientConfig":
        try:
            if path.stat().st_size > 64_000:
                raise ValueError
            return cls.parse(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            raise AccountError(
                "Cannot read the OAuth configuration. Choose the downloaded Desktop client JSON.",
                "configuration",
            ) from None

    def document(self) -> dict[str, Any]:
        return {
            "installed": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": AUTH_URL,
                "token_uri": TOKEN_URL,
            }
        }


@dataclass(repr=False)
class Tokens:
    access: str = field(repr=False)
    refresh: str = field(repr=False)
    expires_at: float
    scopes: frozenset[str] = frozenset((SCOPE,))

    @classmethod
    def parse(
        cls,
        value: dict[str, Any],
        previous: str = "",
        expected: frozenset[str] = frozenset((SCOPE,)),
        require_refresh: bool = True,
    ) -> "Tokens":
        try:
            scope = value.get("scope", " ".join(sorted(expected)))
            scopes = scope.split() if isinstance(scope, str) else scope
            if not expected.issubset(scopes) or not set(scopes).issubset(UPLOAD_SCOPES):
                raise AccountError(
                    "The required YouTube permissions were not granted. Existing authorization was preserved.",
                    "reconnect",
                )
            if str(value.get("token_type", "Bearer")).lower() != "bearer":
                raise ValueError
            access = value["access_token"]
            refresh = value.get("refresh_token") or previous
            expiry = float(value.get("expires_at") or (time.time() + float(value["expires_in"])))
            if (
                not isinstance(access, str)
                or not access
                or not isinstance(refresh, str)
                or (require_refresh and not refresh)
            ):
                raise AccountError(
                    "Google did not provide offline authorization. Reconnect and approve consent.",
                    "reconnect",
                )
            if not time.time() - 86400 < expiry < time.time() + 366 * 86400:
                raise ValueError
            return cls(access, refresh, expiry, frozenset(scopes))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise AccountError(
                "Google returned an invalid token response. Reconnect.", "reconnect"
            ) from None

    def encode(self) -> str:
        return json.dumps(
            {
                "access_token": self.access,
                "refresh_token": self.refresh,
                "expires_at": self.expires_at,
                "scope": " ".join(sorted(self.scopes)),
            }
        )


def token_request_options(
    url: str, headers: dict[str, str], kwargs: dict[str, Any]
) -> tuple[str, dict[str, str], dict[str, Any]]:
    # OAuth2Session.fetch_token treats arbitrary kwargs as form fields, not HTTP options.
    # Its supported request hook applies this at the transport layer instead.
    kwargs["allow_redirects"] = False
    return url, headers, kwargs


class GoogleOAuth:
    def __init__(
        self, browser: Callable[[str], Any] = webbrowser.open, timeout: float = 180
    ) -> None:
        self.browser, self.timeout = browser, timeout

    def authorize(
        self, config: ClientConfig, cancel: Event, progress: Callable[[str], None]
    ) -> Tokens:
        return self._authorize(config, cancel, progress, frozenset((SCOPE,)))

    def authorize_upload(
        self, config: ClientConfig, cancel: Event, progress: Callable[[str], None]
    ) -> Tokens:
        return self._authorize(config, cancel, progress, UPLOAD_SCOPES)

    def _authorize(
        self,
        config: ClientConfig,
        cancel: Event,
        progress: Callable[[str], None],
        scopes: frozenset[str],
    ) -> Tokens:
        check_cancel(cancel)
        state = secrets.token_urlsafe(32)
        result: dict[str, str] = {}
        expected_host = ""

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass  # Callback paths contain authorization codes.

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query, keep_blank_values=True)
                valid = (
                    parsed.path == "/"
                    and not parsed.scheme
                    and not parsed.netloc
                    and self.headers.get("Host") == expected_host
                    and len(query.get("state", [])) == 1
                    and secrets.compare_digest(query["state"][0], state)
                )
                if not valid:
                    self.send_error(400, "Invalid authorization response")
                    return
                if len(query.get("error", [])) == 1 and "code" not in query:
                    result["error"] = "denied"
                elif len(query.get("code", [])) == 1 and query["code"][0] and "error" not in query:
                    result["code"] = query["code"][0]
                else:
                    result["error"] = "invalid"
                body = b"Authorization received. Return to ShortsScheduler. You may close this tab."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        class Listener(HTTPServer):
            def get_request(self) -> Any:
                sock, address = super().get_request()
                sock.settimeout(0.5)
                return sock, address

            def handle_error(self, request: Any, client_address: Any) -> None:
                pass

        try:
            with Listener(("127.0.0.1", 0), Handler) as server:
                server.timeout = 0.2
                expected_host = f"127.0.0.1:{server.server_port}"
                flow = Flow.from_client_config(
                    config.document(), scopes=sorted(scopes), autogenerate_code_verifier=True
                )
                flow.oauth2session.register_compliance_hook(
                    "access_token_request", token_request_options
                )
                flow.redirect_uri = f"http://{expected_host}/"
                url, _ = flow.authorization_url(
                    state=state, access_type="offline", prompt="consent select_account"
                )
                progress(
                    "Waiting for browser authorization. Choose the intended YouTube or Brand Account, then return here."
                )
                if self.browser(url) is False:
                    raise AccountError(
                        "The system browser could not open. Check your default browser and try again."
                    )
                deadline = time.monotonic() + self.timeout
                while not result:
                    check_cancel(cancel)
                    if time.monotonic() >= deadline:
                        raise AccountError(
                            "Authorization timed out. Start Connect YouTube again when ready.",
                            "cancelled",
                        )
                    server.handle_request()
                check_cancel(cancel)
                if "error" in result:
                    raise AccountError(
                        "Authorization was denied or the response was invalid. Nothing was connected.",
                        "cancelled",
                    )
                progress("Exchanging authorization securely…")
                flow.fetch_token(code=result["code"], timeout=(5, 15))
                check_cancel(cancel)
                return Tokens.parse(
                    dict(flow.oauth2session.token),
                    expected=scopes,
                    require_refresh=UPLOAD_SCOPE not in scopes,
                )
        except AccountError:
            raise
        except Exception:
            raise AccountError(
                "Authorization failed. Check the Desktop client configuration, network, and Google consent settings, then retry."
            ) from None

    def refresh(self, config: ClientConfig, tokens: Tokens, cancel: Event) -> Tokens:
        check_cancel(cancel)
        try:
            response = requests.post(
                TOKEN_URL,
                data={
                    "client_id": config.client_id,
                    "client_secret": config.client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh,
                },
                timeout=(5, 15),
                allow_redirects=False,
            )
            with response:
                if response.status_code in (400, 401, 403):
                    raise AccountError(
                        "Authorization expired, was revoked, or cannot be refreshed. Reconnect YouTube (Testing tokens may expire after seven days).",
                        "reconnect",
                    )
                if response.status_code != 200:
                    raise AccountError(
                        "Google authorization is temporarily unavailable. Retry later; stored credentials were kept.",
                        "offline",
                    )
                renewed = Tokens.parse(response.json(), tokens.refresh, expected=tokens.scopes)
                # Persist successful rotation even if cancellation arrived during the request.
                return renewed
        except AccountError:
            raise
        except Exception:
            raise AccountError(
                "Unable to refresh authorization. Check your network and retry; stored credentials were kept.",
                "offline",
            ) from None

    def revoke(self, tokens: Tokens) -> bool:
        try:
            with requests.post(
                REVOKE_URL, data={"token": tokens.refresh}, timeout=(5, 10), allow_redirects=False
            ) as response:
                return response.status_code == 200
        except Exception:
            return False
