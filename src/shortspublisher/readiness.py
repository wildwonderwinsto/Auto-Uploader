"""YouTube-only technical checks, researched 2026-10-15.

https://support.google.com/youtube/answer/15424877?hl=en
Recommendations are warnings, never invented platform requirements.
"""

from dataclasses import dataclass

from .media import Media

MAX_SHORTS_SECONDS = 180
RULES_VERSION = "youtube-2026-10-15"
LIMITATIONS = (
    "Local technical checks only. This does not guarantee YouTube acceptance, copyright "
    "clearance, or successful publishing. FFprobe decoding cannot detect every media defect."
)


@dataclass(frozen=True)
class Readiness:
    status: str
    messages: tuple[str, ...]


def evaluate(media: Media) -> Readiness:
    warnings = list(media.notes)
    if media.duration > MAX_SHORTS_SECONDS:
        warnings.append("Longer than the 3-minute Shorts maximum.")
    if media.aspect > 1.000001:
        warnings.append("Landscape display shape; Shorts require square or vertical video.")
    if not media.audio_codecs:
        warnings.append("No audio stream. Silent video is valid; confirm this is intentional.")
    if "unknown" in media.audio_codecs:
        warnings.append("An audio codec could not be identified.")
    if media.fps is None:
        warnings.append("Frame rate is unavailable; review playback before publishing.")
    if warnings:
        return Readiness("Valid video · Needs attention", tuple(warnings))
    return Readiness(
        "Passes implemented checks",
        (
            "Square or vertical display shape.",
            "Duration is within 3 minutes.",
            "Readable metadata and decoded video frames; FFprobe reported no decoding errors.",
        ),
    )
