"""Upload defaults (YouTube Studio's name): the details and platform settings every new video
starts with, so they aren't typed again for each one; plus reusing another video's details.

The title is never part of either: each video gets its own. Defaults live in a small JSON file
beside the library, so saving them changes no database schema. They only fill videos imported
by hand that have no details yet; watched folders keep their own auto upload settings."""

from __future__ import annotations

import json
import os
from contextlib import closing
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING

from .preparation import PreparationStore, YouTubeMetadata, unique_tags

if TYPE_CHECKING:
    from .library import Library

FILE = "upload-defaults.json"
AI_PLATFORMS = ("tiktok", "instagram")  # the Buffer platforms with an AI-generated label


@dataclass(frozen=True)
class Details:
    """What reusing or defaults carry from video to video: everything but the title."""

    description: str = ""
    tags: tuple[str, ...] = ()
    privacy: str = "private"
    made_for_kids: bool | None = None
    synthetic: bool | None = None
    ai: dict[str, bool | None] = field(default_factory=dict)  # per Buffer platform

    @classmethod
    def of(cls, metadata: YouTubeMetadata, ai: dict[str, bool | None]) -> Details:
        return cls(
            metadata.description,
            unique_tags(metadata.tags),
            metadata.privacy,
            metadata.made_for_kids,
            metadata.synthetic,
            {p: ai.get(p) for p in AI_PLATFORMS},
        )

    def onto(self, metadata: YouTubeMetadata) -> YouTubeMetadata:
        """These details with the video's own title kept."""
        return replace(
            metadata,
            description=self.description,
            tags=self.tags,
            privacy=self.privacy,
            made_for_kids=self.made_for_kids,
            synthetic=self.synthetic,
        )

    @property
    def empty(self) -> bool:
        return self.onto(YouTubeMetadata()) == YouTubeMetadata() and not any(
            v is not None for v in self.ai.values()
        )


class DefaultsStore:
    def __init__(self, library: Library) -> None:
        self.library = library
        self.path = library.path.parent / FILE

    def read(self) -> Details | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            ai = data.get("ai", {})
            return Details(
                str(data.get("description", "")),
                unique_tags(tuple(str(t) for t in data.get("tags", ()))),
                data.get("privacy") if data.get("privacy") in PRIVACY else "private",
                flag(data.get("made_for_kids")),
                flag(data.get("synthetic")),
                {p: flag(ai.get(p)) for p in AI_PLATFORMS},
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, AttributeError):
            return None  # an unreadable file is treated as no defaults, never as an error

    def save(self, details: Details) -> None:
        temporary = self.path.with_suffix(".tmp")
        payload = {**asdict(details), "tags": list(details.tags)}
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def of_video(self, video_id: int) -> Details:
        """Another video's saved details, to reuse."""
        metadata = PreparationStore(self.library).get(video_id).metadata
        return Details.of(metadata, self.ai(video_id))

    def ai(self, video_id: int) -> dict[str, bool | None]:
        with closing(self.library.connect()) as db:
            result: dict[str, bool | None] = {}
            for platform in AI_PLATFORMS:
                row = db.execute(
                    f"SELECT ai_generated FROM {platform}_drafts WHERE video_id=?", (video_id,)
                ).fetchone()
                result[platform] = None if not row or row[0] is None else bool(row[0])
            return result

    def apply(self, video_id: int, stamp: str) -> bool:
        """Fill a newly imported video that has no details yet. Returns whether it did."""
        details = self.read()
        if details is None or details.empty:
            return False
        store = PreparationStore(self.library)
        current = store.get(video_id)
        if current.revision:
            return False  # it already has details of its own
        store.save(video_id, details.onto(current.metadata), 0)
        with closing(self.library.connect()) as db, db:
            for platform, value in details.ai.items():
                if value is not None:
                    db.execute(
                        f"INSERT OR IGNORE INTO {platform}_drafts VALUES (?,?,?,?)",
                        (video_id, "", value, stamp),
                    )
        return True


PRIVACY = ("private", "unlisted", "public")


def flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
