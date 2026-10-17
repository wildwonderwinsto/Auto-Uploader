"""Read-only channels.list adapter with bounded retries and sanitized errors."""

from dataclasses import dataclass
from threading import Event
from typing import Any
from urllib.parse import urlsplit

import requests

from .auth import AccountError, Tokens, check_cancel


@dataclass(frozen=True)
class Channel:
    id: str
    title: str
    handle: str = ""
    description: str = ""
    thumbnail: str = ""


class YouTubeClient:
    def channels(self, tokens: Tokens, cancel: Event) -> list[Channel]:
        channels: dict[str, Channel] = {}
        page = ""
        for _ in range(10):
            params = {"mine": "true", "part": "snippet", "maxResults": "50"}
            if page:
                params["pageToken"] = page
            body = self.request(tokens, params, cancel)
            try:
                for item in body["items"]:
                    snippet = item["snippet"]
                    identity, title = item["id"], snippet["title"]
                    if not isinstance(identity, str) or not identity or not isinstance(title, str):
                        raise ValueError
                    thumb = snippet.get("thumbnails", {}).get("default", {}).get("url", "")
                    parsed = urlsplit(thumb)
                    if parsed.scheme != "https" or parsed.hostname not in (
                        "yt3.ggpht.com",
                        "yt3.googleusercontent.com",
                    ):
                        thumb = ""
                    channels[identity] = Channel(
                        identity,
                        title,
                        str(snippet.get("customUrl", "")),
                        str(snippet.get("description", ""))[:5000],
                        thumb,
                    )
                page = body.get("nextPageToken", "")
                if not isinstance(page, str):
                    raise ValueError
                if not page:
                    return list(channels.values())
            except (KeyError, TypeError, ValueError):
                raise AccountError(
                    "YouTube returned unreadable channel information. Retry later."
                ) from None
        raise AccountError(
            "Too many authorized channels to identify safely. Authorize the intended channel separately.",
            "identity",
        )

    def request(self, tokens: Tokens, params: dict[str, str], cancel: Event) -> dict[str, Any]:
        for attempt in range(3):
            check_cancel(cancel)
            retry = False
            try:
                with requests.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params=params,
                    headers={"Authorization": f"Bearer {tokens.access}"},
                    timeout=(5, 15),
                    allow_redirects=False,
                ) as response:
                    status = response.status_code
                    if status == 200:
                        body = response.json()
                        if not isinstance(body, dict):
                            raise ValueError
                        return body
                    if status == 401:
                        raise AccountError(
                            "YouTube rejected authorization. Reconnect to grant read-only access.",
                            "unauthorized",
                        )
                    if status == 403:
                        try:
                            reasons = {
                                e.get("reason")
                                for e in response.json().get("error", {}).get("errors", [])
                            }
                        except (ValueError, AttributeError, TypeError):
                            reasons = set()
                        if reasons & {"quotaExceeded", "dailyLimitExceeded"}:
                            raise AccountError(
                                "YouTube API quota is exhausted. Retry after quota resets; authorization was kept.",
                                "quota",
                            )
                        if reasons & {"rateLimitExceeded", "userRateLimitExceeded"}:
                            retry = True
                        else:
                            raise AccountError(
                                "YouTube denied access. Enable YouTube Data API v3, check read-only consent and channel permissions, then retry or reconnect.",
                                "permission",
                            )
                    elif status == 429 or 500 <= status <= 599:
                        retry = True
                    else:
                        raise AccountError(
                            "YouTube could not verify the channel. Check configuration and retry."
                        )
            except (requests.RequestException, ValueError):
                retry = True
            if retry and attempt < 2:
                if cancel.wait(0.5 * (2**attempt)):
                    check_cancel(cancel)
            else:
                raise AccountError(
                    "YouTube is temporarily unavailable or rate limited. Retry later; cached details are not live verification.",
                    "offline",
                )
        raise AccountError("YouTube is temporarily unavailable.", "offline")

    def thumbnail(self, channel: Channel, cancel: Event) -> bytes:
        """Optional public image; no Authorization header, cookies, redirects or persistence."""
        try:
            url = urlsplit(channel.thumbnail)
            if (
                url.scheme != "https"
                or url.hostname not in ("yt3.ggpht.com", "yt3.googleusercontent.com")
                or url.username
                or url.password
                or url.port not in (None, 443)
            ):
                return b""
            check_cancel(cancel)
            with requests.get(
                channel.thumbnail, timeout=(3, 5), allow_redirects=False, stream=True
            ) as reply:
                if reply.status_code != 200 or not reply.headers.get("Content-Type", "").startswith(
                    "image/"
                ):
                    return b""
                data = bytearray()
                for chunk in reply.iter_content(8192):
                    check_cancel(cancel)
                    data.extend(chunk)
                    if len(data) > 256_000:
                        return b""
                return bytes(data)
        except Exception:
            return b""
