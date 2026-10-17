"""Read-only local FFprobe inspection. No Qt, database, or shell execution."""

import json
import math
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from threading import Event
from typing import Any


class InspectionError(Exception):
    """An actionable failure, suitable for display to the user."""


class Cancelled(Exception):
    pass


def checkpoint(cancel: Event) -> None:
    if cancel.is_set():
        raise Cancelled("Operation cancelled.")


@dataclass(frozen=True)
class Media:
    size: int
    mtime_ns: int
    duration: float
    width: int
    height: int
    aspect: float
    fps: float | None
    video_codec: str
    audio_codecs: tuple[str, ...]
    frames: int
    rotation: float
    notes: tuple[str, ...] = ()


def positive(value: object) -> float | None:
    try:
        result = float(str(value))
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError):
        return None


def ratio(value: object) -> float | None:
    try:
        return positive(float(Fraction(str(value).replace(":", "/"))))
    except (ValueError, ZeroDivisionError):
        return None


def parse_metadata(data: Any, size: int, mtime_ns: int) -> Media:
    """Reject incomplete required metadata; optional metadata stays explicitly unknown."""
    try:
        streams = data["streams"]
        videos = [
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ]
        if not videos:
            raise InspectionError(
                "No playable video stream was found (audio-only files are not videos)."
            )
        video = videos[0]
        width, height = int(video["width"]), int(video["height"])
        frames = int(video["nb_read_frames"])
        codec = video.get("codec_name")
        durations = [
            positive(data.get("format", {}).get("duration")),
            positive(video.get("duration")),
        ]
        duration = max((d for d in durations if d is not None), default=0)
        if min(width, height, frames) <= 0 or duration <= 0 or not codec or codec == "unknown":
            raise InspectionError(
                "Video dimensions, duration, decoded frames, or codec are missing or invalid."
            )
        notes = []
        if len(videos) > 1:
            notes.append(
                "Multiple video streams found; readiness uses the first non-cover video stream."
            )
        sar = ratio(video.get("sample_aspect_ratio"))
        if sar is None:
            sar = 1.0
            notes.append(
                "Pixel aspect ratio is unknown; square pixels assumed. Confirm the displayed shape."
            )
        rotation = float(video.get("tags", {}).get("rotate", 0))
        for item in video.get("side_data_list", []):
            if "rotation" in item:
                rotation = float(item["rotation"])
        if not math.isfinite(rotation):
            raise ValueError("Non-finite rotation")
        aspect = width * sar / height
        if math.isclose(abs(rotation) % 180, 90, abs_tol=0.01):
            aspect = 1 / aspect
        elif not math.isclose(rotation % 180, 0, abs_tol=0.01):
            notes.append("Nonstandard rotation metadata; confirm orientation before publishing.")
        fps = ratio(video.get("avg_frame_rate")) or ratio(video.get("r_frame_rate"))
        audio = tuple(
            str(s.get("codec_name") or "unknown") for s in streams if s.get("codec_type") == "audio"
        )
        return Media(
            size,
            mtime_ns,
            duration,
            width,
            height,
            aspect,
            fps,
            str(codec),
            audio,
            frames,
            rotation,
            tuple(notes),
        )
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise InspectionError("FFprobe returned incomplete or invalid video metadata.") from exc


class Inspector:
    def __init__(self, executable: str | None = None, timeout: float = 120) -> None:
        self.executable = executable
        self.timeout = timeout

    def inspect(self, path: Path, cancel: Event) -> Media:
        checkpoint(cancel)
        try:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode) or before.st_size == 0:
                raise InspectionError("Choose a non-empty, regular video file.")
            with path.open("rb") as source:
                source.read(1)
        except OSError as exc:
            raise InspectionError(
                "The source file is missing or unreadable. Check its location and permissions."
            ) from exc
        executable = self.executable or shutil.which("ffprobe")
        if not executable:
            raise InspectionError(
                "FFprobe is unavailable. Install FFmpeg (macOS: brew install ffmpeg), then restart "
                "from that terminal, or launch with --ffprobe /absolute/path/to/ffprobe. "
                "No inspection has passed."
            )
        # Decodes/counts frames across the file; does not transcode or create media.
        # Only local file protocols are permitted, even if the input is a playlist.
        command = [
            executable,
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-threads",
            "2",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,nb_read_frames,duration,avg_frame_rate,r_frame_rate,sample_aspect_ratio:stream_disposition=attached_pic:stream_tags=rotate:stream_side_data=rotation:format=duration",
            "-of",
            "json",
            "-i",
            str(path),
        ]
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        except OSError as exc:
            raise InspectionError(
                "FFprobe could not start. Check --ffprobe, installation, and executable permissions."
            ) from exc
        started = time.monotonic()
        try:
            while True:
                checkpoint(cancel)
                if time.monotonic() - started > self.timeout:
                    raise InspectionError(
                        f"Inspection timed out after {self.timeout:g} seconds. The file may be large or damaged; no inspection has passed."
                    )
                try:
                    output, errors = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
        checkpoint(cancel)
        if process.returncode or errors.strip():
            detail = errors.decode("utf-8", errors="replace").strip()[:1200]
            raise InspectionError(
                "FFprobe could not read the video cleanly. "
                + (detail or "Unsupported or damaged media.")
            )
        try:
            after = path.stat()
        except OSError as exc:
            raise InspectionError("The source file disappeared during inspection.") from exc
        if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
            after.st_dev,
        ):
            raise InspectionError(
                "The source file changed during inspection. Wait until it finishes saving, then retry."
            )
        try:
            data = json.loads(output)
        except (ValueError, UnicodeError) as exc:
            raise InspectionError(
                "FFprobe returned invalid JSON; no inspection has passed."
            ) from exc
        return parse_metadata(data, after.st_size, after.st_mtime_ns)
