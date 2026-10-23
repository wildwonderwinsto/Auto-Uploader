"""TikTok publishing through Buffer. The shared Buffer posting logic lives in `social`.

Limits verified against Buffer's TikTok help article (2026-10-17): 3 s–10 min, ≤ 1 GB, ≥ 360 px,
23–60 fps, MP4/MOV/WEBM, captions ≤ 2,200 characters with ≤ 5 hashtags. Buffer's API sets only the
AI-generated content label; privacy, comments, Duet and Stitch are not settable.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .buffer import BufferOAuth, SecretStore
from .library import Library, Video
from .social import (
    CHECK_BACKOFF,
    GIVE_UP_HOURS,
    HASHTAGS_MAX,
    HOSTING_CONSENT,
    INSIGHTS_KEY,
    RETENTION_GRACE,
    TIKTOK,
    UNSENT,
    BufferPostService,
    caption_for,
    caption_from,
    parse,
    readable,
    stamp,
)
from .social import SocialError as TikTokError
from .social import caption_errors as platform_caption_errors
from .social import media_errors as platform_media_errors

CAPTION_MAX = TIKTOK.caption_max
EXTENSIONS = set(TIKTOK.extensions)
MAX_BYTES = TIKTOK.max_bytes
AUTOMATION_CONSENT = TIKTOK.automation_consent

__all__ = [
    "AUTOMATION_CONSENT",
    "CAPTION_MAX",
    "CHECK_BACKOFF",
    "EXTENSIONS",
    "GIVE_UP_HOURS",
    "HASHTAGS_MAX",
    "HOSTING_CONSENT",
    "INSIGHTS_KEY",
    "MAX_BYTES",
    "RETENTION_GRACE",
    "TIKTOK",
    "UNSENT",
    "TikTokError",
    "TikTokService",
    "caption_errors",
    "caption_for",
    "caption_from",
    "media_errors",
    "parse",
    "readable",
    "stamp",
]


def caption_errors(caption: str) -> list[str]:
    return platform_caption_errors(TIKTOK, caption)


def media_errors(video: Video, hosting_limit: int) -> list[str]:
    return platform_media_errors(TIKTOK, video, hosting_limit)


class TikTokService(BufferPostService):
    def __init__(
        self,
        library: Library,
        secrets: SecretStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        *,
        api_session: Any | None = None,
        oauth: BufferOAuth | None = None,
        host_session: Any | None = None,
        anonymous: Any | None = None,
    ) -> None:
        super().__init__(
            TIKTOK,
            library,
            secrets,
            clock,
            api_session=api_session,
            oauth=oauth,
            host_session=host_session,
            anonymous=anonymous,
        )
