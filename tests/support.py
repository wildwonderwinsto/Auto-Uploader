"""Isolated, playable media fixtures and Qt event-loop helpers."""

import shutil
import subprocess
import time
import unittest
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def make_video(path: Path, *, audio: bool = True, size: str = "160x240", duration: int = 2) -> None:
    executable = shutil.which("ffmpeg")
    if not executable or not shutil.which("ffprobe"):
        raise unittest.SkipTest("Real FFmpeg and FFprobe are required for media integration tests")
    command = [
        executable,
        "-v",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate=24:duration={duration}",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    command += ["-c:v", "libvpx-vp9" if path.suffix == ".webm" else "libx264", "-threads", "2"]
    if audio:
        command += ["-c:a", "aac"]
    command += ["-y", str(path)]
    subprocess.run(command, check=True, capture_output=True, timeout=30)


def settle(app: QApplication, page: object, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while page.worker is not None:
        app.processEvents()
        QTest.qWait(5)
        if time.monotonic() > deadline:
            raise AssertionError("Worker failed to finish")
    app.processEvents()


def shutdown(app: QApplication, window: object) -> None:
    """Stop every controller's worker thread before the window and its data folder go away."""
    window.close()
    for name in (
        "videos",
        "accounts",
        "uploads",
        "schedules",
        "automatic",
        "recurring",
        "automation",
        "background",
        "watching",
        "tiktok",
        "dashboard",
    ):
        controller = getattr(window, name, None)
        if controller is not None and hasattr(controller, "stop_for_quit"):
            controller.stop_for_quit()
    window.deleteLater()
    app.processEvents()
