"""Public media hosting for Buffer, which accepts only a public HTTPS URL (no upload endpoint).

Cloudinary (Buffer's suggested host) is used with the user's own account. Uploads read the
original read-only, stream an independent copy and hash exactly the bytes sent. Delivery URLs
are unauthenticated: anyone with the link can view the copy until it is deleted.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, BinaryIO

import requests

from .source import signature

FREE_PLAN_MAX_BYTES = 100 * 1024**2  # Cloudinary Free plan per-video upload limit


class HostingError(Exception):
    """kind: config, consent, source, size, network, uncertain, api, access."""

    def __init__(self, message: str, kind: str = "api") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Hosted:
    public_id: str
    url: str
    size: int
    sha256: str


class HashingReader:
    """Hashes exactly the bytes the HTTP client reads, so the hosted copy is provably the source."""

    def __init__(self, file: BinaryIO, cancel: Event) -> None:
        self.file, self.cancel = file, cancel
        self.digest = hashlib.sha256()
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        if self.cancel.is_set():
            raise HostingError("Hosting upload cancelled before completion.", "uncertain")
        block = self.file.read(size)
        self.digest.update(block)
        self.count += len(block)
        return block

    def __getattr__(self, name: str) -> Any:
        return getattr(self.file, name)


def cloudinary_fields(cloud_name: str, api_key: str, api_secret: str) -> tuple[str, str, str]:
    """Normalize what people paste from the Cloudinary console.

    Accepts the "API environment variable" (``CLOUDINARY_URL=cloudinary://KEY:SECRET@CLOUD``) in
    any field, trims whitespace and rejects values that are clearly the wrong field."""
    for value in (cloud_name, api_key, api_secret):
        match = re.search(r"cloudinary://([^:@\s]+):([^@\s]+)@([A-Za-z0-9_-]+)", value or "")
        if match:
            return match.group(3), match.group(1), match.group(2)
    cloud, key, secret = (
        (cloud_name or "").strip(),
        (api_key or "").strip(),
        (api_secret or "").strip(),
    )
    if not cloud or not key or not secret:
        raise HostingError("Enter the Cloudinary cloud name, API key and API secret.", "config")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", cloud):
        raise HostingError(
            "The cloud name is the short name shown at the top of the Cloudinary console "
            "(letters, numbers and hyphens only — not a URL or email).",
            "config",
        )
    if not key.isdigit():
        raise HostingError(
            "The API key is the number shown next to “API Key” (digits only).", "config"
        )
    if any(c.isspace() for c in secret) or len(secret) < 10:
        raise HostingError(
            "The API secret looks incomplete. Copy it again with the copy button.", "config"
        )
    return cloud, key, secret


class CloudinaryHost:
    def __init__(
        self,
        cloud_name: str,
        api_key: str,
        api_secret: str,
        session: Any | None = None,
        clock: Callable[[], float] = time.time,
        anonymous: Any | None = None,
    ) -> None:
        self.cloud, self.key, self.secret = cloudinary_fields(cloud_name, api_key, api_secret)
        self.session = session or requests.Session()
        self.anonymous = anonymous or requests  # no credentials, like Buffer's fetcher
        self.clock = clock

    def base(self) -> str:
        return f"https://api.cloudinary.com/v1_1/{self.cloud}"

    def sign(self, params: dict[str, str]) -> str:
        # Cloudinary's default request-signing scheme (provider API requirement).
        text = "&".join(f"{k}={params[k]}" for k in sorted(params)) + self.secret
        return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()

    def signed(self, params: dict[str, str]) -> dict[str, str]:
        return {**params, "api_key": self.key, "signature": self.sign(params)}

    def usage(self) -> dict[str, Any]:
        """Credential check and cost feedback without uploading anything."""
        try:
            response = self.session.get(
                f"{self.base()}/usage", auth=(self.key, self.secret), timeout=30
            )
        except requests.RequestException:
            raise HostingError(
                "Could not reach Cloudinary. Check the connection.", "network"
            ) from None
        if response.status_code in (401, 403):
            raise HostingError(
                "Cloudinary rejected these credentials. Check the API key and secret (Cloudinary "
                "console → Settings → API Keys) and that the cloud name belongs to that account.",
                "config",
            )
        if response.status_code == 404:
            raise HostingError("Cloudinary has no account with that cloud name.", "config")
        if response.status_code != 200:
            raise HostingError("Cloudinary could not report account usage.", "api")
        data = response.json()
        credits = data.get("credits") or {}
        return {
            "plan": data.get("plan", ""),
            "credits_used": credits.get("usage"),
            "credits_limit": credits.get("limit"),
            "used_percent": credits.get("used_percent"),
        }

    @staticmethod
    def new_id() -> str:
        return "shortspublisher/" + secrets.token_urlsafe(24)  # unpredictable; no filename

    def upload(
        self, path: Path, expected_sha256: str, max_bytes: int, cancel: Event, public_id: str
    ) -> Hosted:
        """public_id is persisted by the caller first, so an interrupted copy can be removed."""
        try:
            with open(path, "rb") as file:
                before = signature(os.fstat(file.fileno()))
                if before[0] > max_bytes:
                    raise HostingError(
                        f"This video is {before[0] / 1024**2:.0f} MiB; the hosting limit is {max_bytes / 1024**2:.0f} MiB.",
                        "size",
                    )
                reader = HashingReader(file, cancel)
                params = self.signed(
                    {
                        "public_id": public_id,
                        "timestamp": str(int(self.clock())),
                        "overwrite": "false",
                    }
                )
                try:
                    response = self.session.post(
                        f"{self.base()}/video/upload",
                        data=params,
                        files={"file": ("video", reader, "application/octet-stream")},
                        timeout=(30, 900),
                    )
                except requests.RequestException:
                    raise HostingError(
                        "The upload to Cloudinary was interrupted; a partial copy may exist.",
                        "uncertain",
                    ) from None
                if signature(os.fstat(file.fileno())) != before:
                    raise HostingError("The source video changed while uploading.", "source")
        except OSError as exc:
            raise HostingError(f"Cannot read the original video: {exc.strerror}", "source") from exc
        if response.status_code in (401, 403):
            raise HostingError("Cloudinary rejected these credentials.", "config")
        if response.status_code != 200:
            try:
                message = response.json().get("error", {}).get("message", "")
            except ValueError:
                message = ""
            raise HostingError(f"Cloudinary did not accept the video. {message}".strip(), "api")
        data = response.json()
        hosted = Hosted(
            str(data.get("public_id", "")),
            str(data.get("secure_url", "")),
            int(data.get("bytes", 0)),
            reader.digest.hexdigest(),
        )
        if (
            hosted.public_id != public_id
            or not hosted.url.startswith("https://")
            or hosted.sha256 != expected_sha256
            or reader.count != before[0]
        ):
            # Never pass on a copy that cannot be proven identical to the approved source.
            self.destroy(public_id)
            raise HostingError(
                "The hosted copy could not be proven identical to the source and was removed.",
                "source",
            )
        return hosted

    def verify(self, url: str, size: int) -> None:
        """Buffer fetches anonymously; check the same way, without credentials."""
        try:
            response = self.anonymous.head(url, allow_redirects=False, timeout=30)
        except requests.RequestException:
            raise HostingError("The hosted video could not be reached yet.", "access") from None
        length = response.headers.get("Content-Length")
        kind = response.headers.get("Content-Type", "")
        if response.status_code != 200 or not kind.startswith("video/"):
            raise HostingError(
                "The hosted video is not publicly reachable as a direct video link.", "access"
            )
        if length is not None and length.isdigit() and int(length) != size:
            raise HostingError("The hosted video size does not match the upload.", "access")

    def destroy(self, public_id: str) -> None:
        try:
            response = self.session.post(
                f"{self.base()}/video/destroy",
                data=self.signed(
                    {
                        "public_id": public_id,
                        "timestamp": str(int(self.clock())),
                        "invalidate": "true",
                    }
                ),
                timeout=30,
            )
        except requests.RequestException:
            raise HostingError(
                "Could not reach Cloudinary to remove the copy.", "network"
            ) from None
        result = response.json().get("result") if response.status_code == 200 else None
        if result not in ("ok", "not found"):
            raise HostingError("Cloudinary did not confirm removal of the hosted copy.", "api")
