"""Official resumable wire protocol. No session URLs or raw HTTP errors escape this module."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import requests

from .auth import AccountError, Tokens

INSERT_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK_SIZE = 8 * 256 * 1024


@dataclass(repr=False)
class UploadReply:
    status: int
    body: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict, repr=False)


def session_url(value: str) -> str:
    try:
        url = urlsplit(value)
        if (
            url.scheme == "https"
            and url.hostname == "www.googleapis.com"
            and url.port in (None, 443)
            and not url.username
            and not url.password
            and not url.fragment
            and url.path == "/upload/youtube/v3/videos"
        ):
            return value
    except ValueError:
        pass
    raise AccountError(
        "The upload server returned an unexpected session location. No media was sent.",
        "reconciliation",
    )


def acknowledged(reply: UploadReply, size: int) -> int:
    value = reply.headers.get("range", "")
    if not value:
        return 0
    match = re.fullmatch(r"bytes=0-(\d+)", value)
    if not match or not 0 < int(match[1]) + 1 <= size:
        raise AccountError(
            "YouTube returned an invalid acknowledged byte range. Reconcile the existing session.",
            "reconciliation",
        )
    return int(match[1]) + 1


def retry_delay(reply: UploadReply, attempt: int) -> float:
    value = reply.headers.get("retry-after", "")
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = 0
    # Long server cooldowns are not shortened: leave the operation paused instead.
    if seconds > 30:
        raise AccountError(
            "YouTube requested a longer cooldown. Wait before explicitly resuming this session.",
            "retry",
        )
    return max(0.5 * 2**attempt, max(0, seconds))


def classify(reply: UploadReply) -> AccountError:
    reasons: set[str] = set()
    try:
        reasons = {str(e.get("reason", "")) for e in reply.body.get("error", {}).get("errors", [])}
    except (TypeError, AttributeError):
        pass
    if reply.status == 401:
        return AccountError(
            "Upload authorization expired or was rejected. Refresh authorization and resume this attempt.",
            "unauthorized",
        )
    if reasons & {"quotaExceeded", "dailyLimitExceeded"}:
        return AccountError(
            "YouTube API quota is exhausted. Wait for quota reset; do not create another attempt.",
            "quota",
        )
    if "uploadLimitExceeded" in reasons:
        return AccountError(
            "The channel's upload limit was reached. Wait for YouTube to allow uploads; do not bypass the limit.",
            "quota",
        )
    if reply.status in (404, 410):
        return AccountError(
            "The session expired or is unavailable. Its remote outcome is unknown. Check YouTube Studio before allowing another attempt.",
            "reconciliation",
        )
    if reply.status in (0, 429, 500, 502, 503, 504) or reasons & {
        "rateLimitExceeded",
        "userRateLimitExceeded",
    }:
        return AccountError(
            "Temporary upload connection/server failure. The existing session was preserved.",
            "retry",
        )
    restrictions = {
        "insufficientPermissions": "The grant lacks permission for this upload. Authorize Uploads for the intended channel.",
        "forbiddenPrivacySetting": "YouTube rejected the requested privacy setting. Check project/channel restrictions; visibility was not changed automatically.",
        "uploadForbidden": "YouTube does not permit uploads for this channel. Resolve the channel restriction before retrying.",
        "youtubeSignupRequired": "The authorized account needs a YouTube channel before uploading.",
        "invalidTitle": "YouTube rejected the title. Correct the saved metadata and review it again.",
        "invalidDescription": "YouTube rejected the description. Correct the saved metadata and review it again.",
    }
    for reason, message in restrictions.items():
        if reason in reasons:
            return AccountError(message, "permission" if reply.status == 403 else "permanent")
    if reply.status == 403:
        return AccountError(
            "YouTube denied the upload. Check upload permission and channel restrictions; the existing attempt was preserved.",
            "permission",
        )
    return AccountError(
        "YouTube rejected this upload request. Inspect the attempt and resolve the problem before allowing another upload.",
        "permanent",
    )


class UploadHTTP:
    def request(
        self,
        method: str,
        url: str,
        tokens: Tokens,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        payload: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> UploadReply:
        try:
            with requests.request(
                method,
                url,
                headers={"Authorization": f"Bearer {tokens.access}", **(headers or {})},
                data=data,
                json=payload,
                params=params,
                timeout=(5, 30),
                allow_redirects=False,
            ) as response:
                try:
                    body = response.json() if response.content else {}
                except ValueError:
                    body = {}
                return UploadReply(
                    response.status_code,
                    body if isinstance(body, dict) else {},
                    {
                        k.lower(): v
                        for k, v in response.headers.items()
                        if k.lower() in ("location", "range", "retry-after")
                    },
                )
        except requests.RequestException:
            return UploadReply(0)

    def initiate(self, tokens: Tokens, snapshot: dict[str, Any]) -> UploadReply:
        # No default visibility: require the immutable approved intent to match the payload.
        payload = snapshot["payload"]
        intent = snapshot.get("requested_privacy")
        if (
            intent not in ("private", "public")
            or payload.get("status", {}).get("privacyStatus") != intent
            or "publishAt" in payload.get("status", {})
        ):
            raise AccountError(
                "Explicit matching Private or Public upload intent is required; scheduling is unsupported."
            )
        return self.request(
            "POST",
            INSERT_URL,
            tokens,
            headers={
                "X-Upload-Content-Length": str(snapshot["size"]),
                "X-Upload-Content-Type": "application/octet-stream",
            },
            payload=payload,
            params={
                "uploadType": "resumable",
                "part": "snippet,status",
                "notifySubscribers": "false",
            },
        )

    def query(self, tokens: Tokens, url: str, size: int) -> UploadReply:
        return self.request(
            "PUT",
            session_url(url),
            tokens,
            headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"},
            data=b"",
        )

    def chunk(self, tokens: Tokens, url: str, data: bytes, offset: int, size: int) -> UploadReply:
        return self.request(
            "PUT",
            session_url(url),
            tokens,
            data=data,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(data)),
                "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{size}",
            },
        )

    def verify(self, tokens: Tokens, remote_id: str) -> UploadReply:
        return self.request(
            "GET",
            "https://www.googleapis.com/youtube/v3/videos",
            tokens,
            params={"id": remote_id, "part": "snippet,status,processingDetails"},
        )
