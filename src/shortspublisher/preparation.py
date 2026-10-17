"""YouTube metadata rules and revision-bound local review, without Qt or network calls.

Rules researched 2026-10-15:
https://developers.google.com/youtube/v3/docs/videos
https://support.google.com/youtube/answer/14328491
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .readiness import MAX_SHORTS_SECONDS, evaluate

if TYPE_CHECKING:
    import sqlite3

    from .library import Library, Video


@dataclass(frozen=True)
class YouTubeMetadata:
    title: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    made_for_kids: bool | None = None
    synthetic: bool | None = None
    privacy: str = "private"


def tag_key(tag: str) -> str:
    return unicodedata.normalize("NFC", tag.strip()).casefold()


def unique_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        key = tag_key(tag)
        if key and key not in seen:
            result.append(tag.strip())
            seen.add(key)
    return tuple(result)


def tag_length(tags: tuple[str, ...]) -> int:
    return sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + max(0, len(tags) - 1)


def metadata_errors(data: YouTubeMetadata) -> tuple[str, ...]:
    errors = []
    if not data.title.strip():
        errors.append("Enter a title before completing review.")
    if len(data.title) > 100:
        errors.append("Title exceeds 100 characters.")
    if len(data.description.encode("utf-8")) > 5000:
        errors.append("Description exceeds 5,000 UTF-8 bytes.")
    for field, value in (("Title", data.title), ("Description", data.description)):
        if "<" in value or ">" in value:
            errors.append(f"{field} cannot contain < or > under YouTube's metadata rules.")
    if tag_length(data.tags) > 500:
        errors.append(
            "Tags exceed 500 combined characters, including separators and quotes around tags containing spaces."
        )
    if any(not tag.strip() for tag in data.tags):
        errors.append("Remove empty tags.")
    if len({tag_key(tag) for tag in data.tags}) != len(data.tags):
        errors.append(
            "Remove duplicate tags (the app compares normalized, case-insensitive keywords)."
        )
    if data.made_for_kids is None:
        errors.append("Explicitly choose Yes or No for Made for Kids.")
    if data.synthetic is None:
        errors.append(
            "Explicitly review the altered or synthetic content disclosure and choose Yes or No."
        )
    if data.privacy not in ("private", "unlisted", "public"):
        errors.append("Choose Private, Unlisted, or Public as the future upload preference.")
    return tuple(errors)


@dataclass(frozen=True)
class Preparation:
    video_id: int
    metadata: YouTubeMetadata = YouTubeMetadata()
    revision: int = 0
    modified_at: str = ""
    reviewed_revision: int | None = None
    reviewed_media: str = ""
    reviewed_at: str = ""


@dataclass(frozen=True)
class Review:
    state: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def media_token(video: Video) -> str:
    """Binding to this inspection and the observed local file identity, not a content hash."""
    try:
        info = Path(video.path).stat()
    except OSError:
        return ""
    value = (
        video.inspection_revision,
        asdict(video.media),
        video.error,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_dev,
        info.st_ino,
    )
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def review_checks(
    video: Video, preparation: Preparation, availability: str, *, unsaved: bool = False
) -> Review:
    errors = list(metadata_errors(preparation.metadata))
    if availability:
        errors.append(availability)
    if video.error:
        errors.append("Refresh Inspection must succeed: " + video.error)
    if video.media.duration > MAX_SHORTS_SECONDS:
        errors.append("Video exceeds the 3-minute Shorts maximum.")
    if video.media.aspect > 1.000001:
        errors.append("Shorts require square or vertical display dimensions.")
    if (
        min(video.media.width, video.media.height, video.media.frames, video.media.size) <= 0
        or not math.isfinite(video.media.duration)
        or video.media.duration <= 0
        or not math.isfinite(video.media.aspect)
        or video.media.aspect <= 0
        or not video.media.video_codec
    ):
        errors.append("Valid inspected video frames and dimensions are required.")
    if unsaved or preparation.revision == 0:
        errors.append("Save the current metadata before completing review.")
    technical = evaluate(video.media)
    warnings = technical.messages if "attention" in technical.status else ()
    # Remove eligibility issues from warnings; those are blocking above.
    warnings = tuple(w for w in warnings if not w.startswith(("Longer than", "Landscape")))
    complete = (
        not errors
        and preparation.reviewed_revision == preparation.revision
        and bool(preparation.reviewed_at)
    )
    state = "Review Complete" if complete else "Needs Attention" if errors else "Draft"
    if unsaved or preparation.revision == 0:
        state = "Draft"
    return Review(state, tuple(errors), warnings)


class PreparationStore:
    """YouTube payloads in platform-keyed records; optimistic saves prevent lost updates."""

    def __init__(self, library: Library) -> None:
        self.library = library

    @staticmethod
    def read(db: sqlite3.Connection, video_id: int) -> Preparation:
        from .library import LibraryError

        row = db.execute(
            "SELECT * FROM preparations WHERE video_id=? AND platform='youtube'", (video_id,)
        ).fetchone()
        if row is None:
            return Preparation(video_id)
        try:
            payload = json.loads(row["payload"])
            payload["tags"] = tuple(payload["tags"])
            return Preparation(
                video_id,
                YouTubeMetadata(**payload),
                row["revision"],
                row["modified_at"],
                row["reviewed_revision"],
                row["reviewed_media"],
                row["reviewed_at"],
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise LibraryError(
                "Publishing metadata is unreadable. Existing data has been preserved."
            ) from exc

    def get(self, video_id: int) -> Preparation:
        with closing(self.library.connect()) as db:
            return self.read(db, video_id)

    def save(self, video_id: int, data: YouTubeMetadata, expected_revision: int) -> Preparation:
        from .library import LibraryError, now

        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT id FROM videos WHERE id=?", (video_id,)).fetchone() is None:
                raise LibraryError("This video was removed. Your edits have not been saved.")
            old = self.read(db, video_id)
            if old.revision != expected_revision:
                raise LibraryError(
                    "Metadata changed in another window. Your edits are still here; copy them before reloading to resolve the conflict."
                )
            if old.revision and old.metadata == data:
                return old
            db.execute(
                """INSERT INTO preparations
                (video_id, platform, payload, revision, modified_at)
                VALUES (?, 'youtube', ?, ?, ?)
                ON CONFLICT(video_id, platform) DO UPDATE SET
                payload=excluded.payload, revision=excluded.revision, modified_at=excluded.modified_at,
                reviewed_revision=NULL, reviewed_media='', reviewed_at=''""",
                (video_id, json.dumps(asdict(data)), old.revision + 1, now()),
            )
            return self.read(db, video_id)

    def snapshot(self, video: Video, availability: str) -> Preparation:
        """Invalidate detected source changes durably, even if the old file later returns."""
        preparation = self.get(video.id)
        token = media_token(video)
        if preparation.reviewed_at and (
            availability or video.error or token != preparation.reviewed_media
        ):
            with closing(self.library.connect()) as db, db:
                db.execute(
                    """UPDATE preparations SET reviewed_revision=NULL, reviewed_media='', reviewed_at=''
                    WHERE video_id=? AND platform='youtube' AND revision=? AND reviewed_media=?""",
                    (video.id, preparation.revision, preparation.reviewed_media),
                )
            preparation = self.get(video.id)
        return preparation

    def complete(
        self, video_id: int, expected_revision: int, expected_inspection: int
    ) -> Preparation:
        from .library import LibraryError, now, source_status

        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
            if row is None:
                raise LibraryError("This video was removed.")
            video = self.library.decode(row)
            preparation = self.read(db, video_id)
            if (
                preparation.revision != expected_revision
                or video.inspection_revision != expected_inspection
            ):
                raise LibraryError(
                    "The metadata or inspection changed. Reload and review the current version."
                )
            token = media_token(video)
            result = review_checks(video, preparation, source_status(video))
            if result.errors:
                raise LibraryError("Review blocked: " + " ".join(result.errors))
            if not token or token != media_token(video):
                raise LibraryError(
                    "Source changed during review. Refresh Inspection and review again."
                )
            db.execute(
                """UPDATE preparations SET reviewed_revision=revision, reviewed_media=?, reviewed_at=?
                WHERE video_id=? AND platform='youtube'""",
                (token, now(), video_id),
            )
            return self.read(db, video_id)
