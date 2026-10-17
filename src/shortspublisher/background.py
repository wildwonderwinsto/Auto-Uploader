"""Explicit user LaunchAgent lifecycle. No shell, network IPC, or credential payloads."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from .background_state import SERVICE_CONSENT, now, read
from .library import Library


class ServiceError(Exception):
    pass


class LaunchAgent:
    label = "com.shortspublisher.publisher"

    def __init__(self, library: Library):
        self.library = library
        self.root = library.path.absolute().parent
        self.directory = self.root / "background"
        self.login_directory = Path.home() / "Library" / "LaunchAgents"
        self.runtime = Path(sys.executable).absolute()

    @property
    def target(self) -> str:
        return f"gui/{os.getuid()}/{self.label}"

    @property
    def plist(self) -> Path:
        return self.directory / (self.label + ".plist")

    @property
    def login_plist(self) -> Path:
        return self.login_directory / (self.label + ".plist")

    def command(self) -> list[str]:
        return [
            str(self.runtime),
            str(Path(__file__).with_name("worker_entry.py").resolve()),
            "--data-dir",
            str(self.root),
        ]

    def configuration(self) -> dict[str, Any]:
        if sys.platform != "darwin" or os.getuid() == 0:
            raise ServiceError(
                "Background publishing requires a logged-in macOS user, without root."
            )
        if not Path(self.command()[1]).is_file():
            raise ServiceError("The background worker entry point has moved or is missing.")
        if not self.runtime.is_file() or not os.access(self.runtime, os.X_OK):
            raise ServiceError("The configured Python runtime is missing or not executable.")
        if (
            not self.root.is_dir()
            or self.root.is_symlink()
            or self.root.stat().st_uid != os.getuid()
            or self.root.stat().st_mode & 0o022
        ):
            raise ServiceError(
                "Use an existing data directory owned by you and not writable by other users."
            )
        if not re.fullmatch(r"com\.shortspublisher\.(publisher|test\.[a-zA-Z0-9_-]+)", self.label):
            raise ServiceError("Invalid managed service identifier.")
        return {
            "Label": self.label,
            "ProgramArguments": self.command(),
            "RunAtLoad": True,
            "KeepAlive": False,
            "ProcessType": "Background",
            "WorkingDirectory": str(self.root),
            "Umask": 0o077,
            "ExitTimeOut": 60,
        }

    def launchctl(
        self, *args: str, allow_failure: bool = False
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["/bin/launchctl", *args], capture_output=True, text=True, timeout=8
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ServiceError(
                "macOS service management was unavailable or timed out. Refresh status before retrying."
            ) from exc
        if result.returncode and not allow_failure:
            raise ServiceError(
                f"macOS rejected the service operation (exit {result.returncode}). Check login session and Background Items permissions."
            )
        return result

    def owned_file(self, path: Path) -> bool:
        if not path.exists() and not path.is_symlink():
            return False
        if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise ServiceError(
                "The service configuration has unsafe ownership or permissions; it was not changed."
            )
        try:
            data = plistlib.loads(path.read_bytes())
        except Exception as exc:
            raise ServiceError(
                "The existing service configuration is unreadable; it was not overwritten."
            ) from exc
        if data.get("Label") != self.label or data.get("ProgramArguments") != self.command():
            raise ServiceError(
                "Another workspace or runtime owns this service identifier. Remove it from that workspace first."
            )
        return True

    def write(self, path: Path) -> None:
        self.owned_file(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (
            path.parent.is_symlink()
            or path.parent.stat().st_uid != os.getuid()
            or path.parent.stat().st_mode & 0o022
        ):
            raise ServiceError("Unsafe service directory; installation stopped.")
        payload = plistlib.dumps(self.configuration())
        descriptor, temporary = tempfile.mkstemp(prefix=".publisher-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def os_status(self) -> dict[str, Any]:
        response = self.launchctl("print", self.target, allow_failure=True)
        if response.returncode:
            # launchctl reports a missing service using 113. Other failures are not 'stopped'.
            if response.returncode != 113:
                raise ServiceError(
                    "Cannot establish macOS service state. No ownership takeover is authorized."
                )
            return {"loaded": False, "pid": 0}
        # Refuse control of a same-label service not loaded from our managed configuration.
        paths = (str(self.plist), str(self.login_plist))
        path = re.search(r"^\s*path = (.+)$", response.stdout, re.MULTILINE)
        if not path or path.group(1) not in paths:
            raise ServiceError("This macOS service belongs to a different installation.")
        self.owned_file(Path(path.group(1)))
        match = re.search(r"^\s*pid = (\d+)$", response.stdout, re.MULTILINE)
        return {"loaded": True, "pid": int(match.group(1)) if match else 0}

    def status(self) -> dict[str, Any]:
        self.library.initialize()
        state = read(self.library)
        os_state = self.os_status()
        owner = state["owner"]
        installed = self.owned_file(self.plist) or self.owned_file(self.login_plist)
        state.update(os_state, installed=installed)
        age = float("inf")
        if owner["heartbeat"]:
            age = (datetime.now(UTC) - datetime.fromisoformat(owner["heartbeat"])).total_seconds()
        if os_state["pid"]:
            state["status"] = "Starting"
            if owner["pid"] == os_state["pid"] and owner["kind"] == "background":
                state["status"] = owner["state"] if -5 <= age <= 15 else "Stale / unresponsive"
        else:
            state["status"] = (
                "Unavailable"
                if state["requested"]
                else "Stopped"
                if state["enabled"]
                else "Disabled"
                if installed
                else "Not Installed"
            )
        return state

    def configure(
        self,
        *,
        enabled: bool | None = None,
        requested: bool | None = None,
        login: bool | None = None,
        consent: bool = False,
    ) -> None:
        with closing(self.library.connect()) as db, db:
            for field, value in (("enabled", enabled), ("requested", requested), ("login", login)):
                if value is not None:
                    db.execute(f"UPDATE background_config SET {field}=? WHERE id=1", (int(value),))
            if consent:
                db.execute(
                    "UPDATE background_config SET consent_at=?,wording=? WHERE id=1",
                    (now(), SERVICE_CONSENT),
                )

    def perform(self, action: str, *, consent: bool = False, login: bool = False) -> dict[str, Any]:
        if action not in {"enable", "start", "stop", "restart", "disable", "remove", "status"}:
            raise ServiceError("Unknown background-service action.")
        self.library.initialize()
        if action == "status":
            return self.status()
        # One lifecycle operation per user/identifier, including across different workspaces.
        lock_path = Path.home() / "Library" / "Caches" / (self.label + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock_path), timeout=1, mode=0o600):
            self.os_status()
            self.owned_file(self.login_plist)
            if action == "enable":
                if not consent:
                    raise ServiceError("Explicit background-service consent is required.")
                self.write(self.plist)
                if login:
                    self.write(self.login_plist)
                elif self.owned_file(self.login_plist):
                    self.login_plist.unlink()
                self.configure(enabled=True, login=login, consent=True)
                # Registration alone does not start: Start is a separate explicit action.
            elif action in ("start", "restart"):
                config = read(self.library)
                if not config["enabled"] or not config["consent_at"]:
                    raise ServiceError("Enable Background Publishing and review its consent first.")
                if action == "restart":
                    self.stop()
                self.configuration()
                if not self.owned_file(self.plist):
                    raise ServiceError(
                        "The managed service configuration is missing. Enable it again."
                    )
                self.configure(requested=True)
                current = self.os_status()
                if not current["loaded"]:
                    path = self.login_plist if config["login"] else self.plist
                    self.launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
                elif not current["pid"]:
                    self.launchctl("kickstart", self.target)
                # Return Starting until heartbeat confirms completed initialization.
            else:
                self.stop()
                if action in ("disable", "remove"):
                    self.configure(enabled=False, login=False)
                    if self.owned_file(self.login_plist):
                        self.login_plist.unlink()
                if action == "remove" and self.owned_file(self.plist):
                    self.plist.unlink()
            return self.status()

    def stop(self) -> None:
        self.configure(requested=False)
        current = self.os_status()
        if current["pid"]:
            self.launchctl("kill", "SIGTERM", self.target, allow_failure=True)
        deadline = time.monotonic() + 40
        while self.os_status()["pid"]:
            if time.monotonic() >= deadline:
                raise ServiceError(
                    "Still stopping after the active request or Keychain operation. No forced takeover; refresh status."
                )
            time.sleep(0.2)
        if self.os_status()["loaded"]:
            self.launchctl("bootout", self.target)
