"""Consented local discovery, stable-source inspection and persistent byte deduplication.

No Qt, credentials, network, publishing mutations or automatic review. Notifications are hints;
this service always reconciles. A per-database OS lock coordinates scanners across processes.
"""

from __future__ import annotations

import json
import os
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from threading import Event
from typing import Any

from filelock import FileLock, Timeout

from .auth import AccountError
from .library import Library, LibraryService, now
from .media import InspectionError, Media, checkpoint
from .source import fingerprint, signature

EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}
POLL_SECONDS = 10
QUIET_SECONDS = 15


class WatchError(Exception):
    pass


def sig(path: Path) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise WatchError("Only regular files are eligible; symbolic links are not followed.")
    return json.dumps(signature(info))


def directory(path: str) -> tuple[Path, os.stat_result]:
    original = Path(path).expanduser().absolute()
    if any(part.is_symlink() for part in (original, *original.parents)):
        raise WatchError("Choose a real folder, not a symbolic-link location.")
    resolved = original.resolve(strict=True)
    info = resolved.stat()
    if not stat.S_ISDIR(info.st_mode) or resolved == Path(resolved.anchor):
        raise WatchError("Choose an accessible video folder, not a filesystem root.")
    return resolved, info


class WatchService:
    def __init__(self, imports: LibraryService, clock: Callable[[], float] = time.time) -> None:
        self.imports = imports
        self.library: Library = imports.library
        self.clock = clock
        self.lock = FileLock(str(self.library.path) + ".watch.lock")
        # Buffer platforms' automations run after YouTube's, each on its own; tests inject
        # controlled services.
        self.tiktok: Callable[[], Any] = lambda: self.production("tiktok")
        self.instagram: Callable[[], Any] = lambda: self.production("instagram")
        self.facebook: Callable[[], Any] = lambda: self.production("facebook")

    def production(self, platform: str) -> Any:
        from datetime import UTC, datetime

        from .credentials import KeychainSecrets
        from .social import PLATFORMS, BufferPostService

        # Every Buffer platform keeps its secrets in Stage 19's Keychain namespace.
        return BufferPostService(
            next(p for p in PLATFORMS if p.key == platform),
            self.library,
            KeychainSecrets("ShortsPublisher.TikTok"),
            lambda: datetime.fromtimestamp(self.clock(), UTC),
        )

    def folders(self) -> list[dict[str, Any]]:
        with closing(self.library.connect()) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM watch_folders WHERE removed=0 ORDER BY created_at,id"
                )
            ]

    def files(self, folder_id: str) -> list[dict[str, Any]]:
        with closing(self.library.connect()) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM watch_files WHERE folder_id=? ORDER BY updated_at DESC,name",
                    (folder_id,),
                )
            ]

    def preview(self, path: str, cancel: Event) -> dict[str, Any]:
        root, info = directory(path)
        candidates = {}
        with os.scandir(root) as entries:
            for entry in entries:
                checkpoint(cancel)
                if (
                    Path(entry.name).suffix.lower() in EXTENSIONS
                    and not entry.name.startswith(".")
                    and entry.is_file(follow_symlinks=False)
                ):
                    candidates[entry.name] = sig(Path(entry.path))
        return {
            "original_path": path,
            "path": str(root),
            "device": info.st_dev,
            "inode": info.st_ino,
            "candidates": candidates,
        }

    def add(
        self,
        proposal: dict[str, Any],
        *,
        consent: bool,
        existing: bool,
        background: bool = False,
        quiet: float = QUIET_SECONDS,
    ) -> str:
        if not consent:
            raise WatchError("Explicit local monitoring permission is required.")
        if not 1 <= quiet <= 3600:
            raise WatchError("Quiet period must be between 1 and 3600 seconds.")
        root, info = directory(proposal["path"])
        if (info.st_dev, info.st_ino) != (proposal["device"], proposal["inode"]):
            raise WatchError("The folder changed. Choose it again before enabling monitoring.")
        stamp = now()
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM watch_folders WHERE path=?", (str(root),)).fetchone()
            if old and not old["removed"]:
                raise WatchError(
                    "This folder is already configured. Use Resume or Folder Settings."
                )
            identity = old["id"] if old else uuid.uuid4().hex
            if old:
                db.execute(
                    """UPDATE watch_folders SET original_path=?,device=?,inode=?,enabled=1,removed=0,
                    background=?,consent_at=?,initial_mode=?,quiet_seconds=?,revision=revision+1,updated_at=?,error=''
                    WHERE id=?""",
                    (
                        proposal["original_path"],
                        info.st_dev,
                        info.st_ino,
                        int(background),
                        stamp,
                        "existing" if existing else "new",
                        quiet,
                        stamp,
                        identity,
                    ),
                )
            else:
                db.execute(
                    """INSERT INTO watch_folders(id,original_path,path,name,device,inode,enabled,background,
                    consent_at,initial_mode,quiet_seconds,revision,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,1,?,?,?,?,1,?,?)""",
                    (
                        identity,
                        proposal["original_path"],
                        str(root),
                        root.name,
                        info.st_dev,
                        info.st_ino,
                        int(background),
                        stamp,
                        "existing" if existing else "new",
                        quiet,
                        stamp,
                        stamp,
                    ),
                )
            if existing and old:
                db.execute(
                    "UPDATE watch_files SET state='Waiting for file to finish',stable_since=?,observations=1,failures=0,retry_at=0 WHERE folder_id=? AND state='Ignored existing file'",
                    (self.clock(), identity),
                )
            if not existing:
                for name, observed in proposal["candidates"].items():
                    db.execute(
                        """INSERT INTO watch_files(folder_id,name,signature,stable_since,observations,state,updated_at)
                        VALUES(?,?,?,?,1,'Ignored existing file',?) ON CONFLICT(folder_id,name) DO UPDATE SET
                        state='Ignored existing file',signature=excluded.signature,updated_at=excluded.updated_at
                        WHERE watch_files.state NOT IN ('Imported successfully','Already in your library','Duplicate content','Known publishing history')""",
                        (identity, name, observed, self.clock(), stamp),
                    )
            return identity

    def control(self, identity: str, revision: int, action: str) -> None:
        if action not in ("pause", "resume", "remove"):
            raise WatchError("Unknown folder action.")
        with closing(self.library.connect()) as db, db:
            cursor = db.execute(
                """UPDATE watch_folders SET enabled=?,removed=?,revision=revision+1,updated_at=?
                WHERE id=? AND revision=? AND removed=0""",
                (int(action == "resume"), int(action == "remove"), now(), identity, revision),
            )
            if cursor.rowcount != 1:
                raise WatchError("Folder settings changed elsewhere. Reload and try again.")

    def evidence(self, digest: str) -> list[dict[str, Any]]:
        """Channel-specific stored evidence only. Never infer publication eligibility."""
        with closing(self.library.connect()) as db:
            results = []
            for row in db.execute(
                "SELECT id,channel_id,state,remote_id,released,snapshot FROM upload_attempts"
            ):
                if json.loads(row["snapshot"]).get("sha256") == digest:
                    results.append(
                        {
                            "kind": "upload",
                            "id": row["id"],
                            "channel": row["channel_id"],
                            "state": row["state"],
                            "remote_id": row["remote_id"],
                            "blocked": True,
                        }
                    )
            for row in db.execute(
                "SELECT id,channel_id,state FROM scheduled_jobs WHERE source_hash=?", (digest,)
            ):
                results.append(
                    {
                        "kind": "plan",
                        "id": row["id"],
                        "channel": row["channel_id"],
                        "state": row["state"],
                        "blocked": True,
                    }
                )
            return results

    def hash_file(self, path: Path, expected: str, cancel: Event) -> str:
        # O_NOFOLLOW rejects replacement of the final component by a symlink.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as file:
            if json.dumps(signature(os.fstat(file.fileno()))) != expected:
                raise WatchError("Source changed; waiting for another stable observation.")
            digest = fingerprint(file, cancel)
            checkpoint(cancel)
            if sig(path) != expected:
                raise WatchError("Source changed; waiting for another stable observation.")
            return digest

    def index_library(self, cancel: Event) -> set[int]:
        """Backfill without modifying legacy IDs/paths. Unavailable sources block unknown intake."""
        videos = self.library.all()
        for video in videos:
            checkpoint(cancel)
            path = Path(video.path)
            try:
                observed = sig(path)
                with closing(self.library.connect()) as db:
                    known = db.execute(
                        "SELECT 1 FROM content_videos WHERE video_id=? AND signature=?",
                        (video.id, observed),
                    ).fetchone()
                if known:
                    continue
                digest = self.hash_file(path, observed, cancel)
                with closing(self.library.connect()) as db, db:
                    db.execute(
                        "INSERT OR IGNORE INTO content_registry VALUES(?,?,?)",
                        (digest, json.loads(observed)[0], now()),
                    )
                    db.execute(
                        "INSERT OR REPLACE INTO content_videos VALUES(?,?,?,?)",
                        (digest, video.id, video.path, observed),
                    )
            except (OSError, WatchError, AccountError):
                # Existing approved snapshots provide reliable *historical* byte identity.
                with closing(self.library.connect()) as db:
                    known = db.execute(
                        "SELECT 1 FROM content_videos WHERE video_id=?", (video.id,)
                    ).fetchone()
                    snapshots = db.execute(
                        "SELECT snapshot FROM upload_attempts WHERE video_id=?", (video.id,)
                    ).fetchall()
                    plan = db.execute(
                        "SELECT 1 FROM scheduled_jobs WHERE video_id=? AND source_hash!=''",
                        (video.id,),
                    ).fetchone()
                if known or plan or any(json.loads(r[0]).get("sha256") for r in snapshots):
                    continue
                raise WatchError(
                    "An existing library source cannot be fingerprinted. Restore it or remove its library entry before importing unknown content."
                ) from None
        return {v.id for v in videos}

    def scan(
        self,
        cancel: Event,
        *,
        background: bool = False,
        progress: Callable[[str], None] = lambda message: None,
    ) -> dict[str, Any]:
        try:
            with self.lock.acquire(timeout=0):
                folders = [
                    f
                    for f in self.folders()
                    if f["enabled"] and (not background or f["background"])
                ]
                if not folders:
                    # Video queues still send their next TikTok, Instagram and Facebook post.
                    for platform in (self.tiktok, self.instagram, self.facebook):
                        platform().reconcile(self, cancel, background=background)
                    return {"imported": 0, "message": "No enabled folders."}
                imported = 0
                indexed: set[int] | None = None
                for folder in folders:
                    checkpoint(cancel)
                    try:
                        root, info = directory(folder["path"])
                        if (info.st_dev, info.st_ino) != (folder["device"], folder["inode"]):
                            raise WatchError(
                                "Folder identity changed. Remove and choose the folder again."
                            )
                        names = []
                        with os.scandir(root) as entries:
                            for entry in entries:
                                checkpoint(cancel)
                                if Path(
                                    entry.name
                                ).suffix.lower() in EXTENSIONS and not entry.name.startswith("."):
                                    names.append(entry.name)
                        for name in sorted(names):
                            checkpoint(cancel)
                            if not self.authorized(folder, background):
                                break
                            path = root / name
                            try:
                                observed = sig(path)
                                ready = self.observe(folder, name, observed)
                                if not ready:
                                    continue
                                self.stage(folder["id"], name, "Ready for inspection")
                                progress(name + " · Ready for inspection; checking known content")
                                if indexed is None:
                                    indexed = self.index_library(cancel)
                                self.stage(folder["id"], name, "Importing")
                                progress(name + " · Importing; verifying content and media")
                                imported += self.ingest(
                                    folder, path, observed, indexed, cancel, background
                                )
                            except (OSError, InspectionError, WatchError, AccountError) as exc:
                                checkpoint(cancel)
                                self.failed(
                                    folder["id"],
                                    name,
                                    str(exc),
                                    "Unsupported or invalid video"
                                    if isinstance(exc, InspectionError)
                                    else "Missing file"
                                    if isinstance(exc, FileNotFoundError)
                                    else "Needs attention",
                                )
                        with closing(self.library.connect()) as db, db:
                            for row in db.execute(
                                "SELECT name,state FROM watch_files WHERE folder_id=?",
                                (folder["id"],),
                            ).fetchall():
                                if (
                                    row["name"] not in names
                                    and row["state"] != "Ignored existing file"
                                ):
                                    db.execute(
                                        "UPDATE watch_files SET state='Missing file',updated_at=? WHERE folder_id=? AND name=?",
                                        (now(), folder["id"], row["name"]),
                                    )
                            db.execute(
                                "UPDATE watch_folders SET scanned_at=?,result=?,error='' WHERE id=? AND revision=?",
                                (
                                    now(),
                                    f"Checked {len(names)} candidate files",
                                    folder["id"],
                                    folder["revision"],
                                ),
                            )
                    except (OSError, WatchError) as exc:
                        with closing(self.library.connect()) as db, db:
                            db.execute(
                                "UPDATE watch_folders SET error=? WHERE id=?",
                                (str(exc), folder["id"]),
                            )
                from datetime import UTC, datetime

                from .automation import AutomationService
                from .scheduling import ScheduleService

                AutomationService(
                    ScheduleService(self.library, lambda: datetime.fromtimestamp(self.clock(), UTC))
                ).reconcile(self, cancel, background=background)
                for platform in (self.tiktok, self.instagram, self.facebook):
                    # Each never raises: one platform's outage can't stop another or the scanner.
                    platform().reconcile(self, cancel, background=background)
                return {
                    "imported": imported,
                    "message": f"{imported} new videos imported. Publishing requires separate profile or video authorization.",
                }
        except Timeout:
            return {
                "imported": 0,
                "message": "Another scanner is working. Results will refresh shortly.",
            }

    def authorized(self, folder: dict[str, Any], background: bool) -> bool:
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM watch_folders WHERE id=?", (folder["id"],)).fetchone()
            return bool(
                row
                and row["enabled"]
                and not row["removed"]
                and row["revision"] == folder["revision"]
                and (not background or row["background"])
            )

    def observe(self, folder: dict[str, Any], name: str, observed: str) -> bool:
        tick = self.clock()
        with closing(self.library.connect()) as db, db:
            old = db.execute(
                "SELECT * FROM watch_files WHERE folder_id=? AND name=?", (folder["id"], name)
            ).fetchone()
            if old and old["state"] == "Ignored existing file":
                return False
            # Preserve new-only exclusions through same-inode renames within this approved root.
            for row in db.execute(
                "SELECT signature FROM watch_files WHERE folder_id=? AND state='Ignored existing file'",
                (folder["id"],),
            ):
                if json.loads(row[0])[-2:] == json.loads(observed)[-2:]:
                    return False
            if not old or old["signature"] != observed:
                db.execute(
                    """INSERT INTO watch_files(folder_id,name,signature,stable_since,observations,state,updated_at)
                    VALUES(?,?,?,?,1,'Waiting for file to finish',?) ON CONFLICT(folder_id,name) DO UPDATE SET
                    signature=excluded.signature,stable_since=excluded.stable_since,observations=1,
                    state=excluded.state,sha256='',video_id=NULL,failures=0,retry_at=0,message='',updated_at=excluded.updated_at""",
                    (folder["id"], name, observed, tick, now()),
                )
                return False
            if old["state"] in (
                "Imported successfully",
                "Already in your library",
                "Duplicate content",
                "Known publishing history",
            ):
                return False
            db.execute(
                "UPDATE watch_files SET observations=observations+1 WHERE folder_id=? AND name=?",
                (folder["id"], name),
            )
            return bool(
                json.loads(observed)[0] > 0
                and tick >= old["retry_at"]
                and tick - old["stable_since"] >= folder["quiet_seconds"]
            )

    def stage(self, folder_id: str, name: str, state: str) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute(
                "UPDATE watch_files SET state=?,updated_at=? WHERE folder_id=? AND name=?",
                (state, now(), folder_id, name),
            )

    def failed(
        self, folder_id: str, name: str, message: str, state: str = "Needs attention"
    ) -> None:
        with closing(self.library.connect()) as db, db:
            row = db.execute(
                "SELECT failures FROM watch_files WHERE folder_id=? AND name=?", (folder_id, name)
            ).fetchone()
            count = (row[0] if row else 0) + 1
            db.execute(
                """INSERT INTO watch_files(folder_id,name,signature,stable_since,observations,state,failures,retry_at,message,updated_at)
                VALUES(?,?,'',?,0,?,?,?,?,?) ON CONFLICT(folder_id,name) DO UPDATE SET
                state=excluded.state,failures=excluded.failures,retry_at=excluded.retry_at,message=excluded.message,updated_at=excluded.updated_at""",
                (
                    folder_id,
                    name,
                    self.clock(),
                    state,
                    count,
                    self.clock() + min(3600, 30 * 2 ** min(count - 1, 7)),
                    message,
                    now(),
                ),
            )

    def ingest(
        self,
        folder: dict[str, Any],
        path: Path,
        observed: str,
        indexed: set[int],
        cancel: Event,
        background: bool,
    ) -> int:
        digest = self.hash_file(path, observed, cancel)
        history = self.evidence(digest)
        with closing(self.library.connect()) as db:
            known = db.execute(
                "SELECT 1 FROM content_registry WHERE sha256=?", (digest,)
            ).fetchone()
        media: Media | None = None
        if not known and not history:
            media = self.imports.inspector.inspect(path, cancel)
        checkpoint(cancel)
        if sig(path) != observed:
            raise WatchError("File changed during inspection. Waiting for completion.")
        root, info = directory(folder["path"])
        if path.parent != root or (info.st_dev, info.st_ino) != (folder["device"], folder["inode"]):
            raise WatchError("Folder changed during inspection.")
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT * FROM watch_folders WHERE id=?", (folder["id"],)
            ).fetchone()
            if (
                not current
                or not current["enabled"]
                or current["removed"]
                or current["revision"] != folder["revision"]
                or (background and not current["background"])
            ):
                return 0
            history = self.evidence(
                digest
            )  # Recheck while the final write lock prevents new history.
            ids = {r[0] for r in db.execute("SELECT id FROM videos")}
            if ids - indexed:
                raise WatchError(
                    "Library changed during scan. Retrying with current duplicate records."
                )
            if sig(path) != observed:
                raise WatchError("Source changed before import commit.")
            links = db.execute(
                "SELECT video_id,path FROM content_videos WHERE sha256=? ORDER BY video_id",
                (digest,),
            ).fetchall()
            prior = db.execute(
                "SELECT 1 FROM content_registry WHERE sha256=?", (digest,)
            ).fetchone()
            video_id = next((r[0] for r in links if r[0] in ids), None)
            created = False
            state = (
                "Duplicate content"
                if prior
                else "Known publishing history"
                if history
                else "Imported successfully"
            )
            if video_id is not None:
                state = (
                    "Already in your library"
                    if any(r[1] == str(path) for r in links)
                    else "Duplicate content"
                )
            if not prior and not history:
                assert media is not None
                existing_path = db.execute(
                    "SELECT id FROM videos WHERE path=?", (str(path),)
                ).fetchone()
                if existing_path:
                    raise WatchError(
                        "This path belongs to an existing library video. Refresh it manually; its metadata was not replaced."
                    )
                video, created = self.library.save_in_transaction(db, str(path), media)
                video_id = video.id
                indexed.add(video.id)
            db.execute(
                "INSERT OR IGNORE INTO content_registry VALUES(?,?,?)",
                (digest, json.loads(observed)[0], now()),
            )
            if created:
                db.execute(
                    "INSERT INTO content_videos VALUES(?,?,?,?)",
                    (digest, video_id, str(path), observed),
                )
            message = (
                "Publishing history exists; review its channel-specific records before any future publication."
                if history
                else ""
            )
            if prior and video_id is None:
                message = "Previously known content. Its old library entry is unavailable; use manual import if needed."
            db.execute(
                "UPDATE watch_files SET state=?,sha256=?,video_id=?,failures=0,retry_at=0,message=?,updated_at=? WHERE folder_id=? AND name=?",
                (state, digest, video_id, message, now(), folder["id"], path.name),
            )
            return int(created)
