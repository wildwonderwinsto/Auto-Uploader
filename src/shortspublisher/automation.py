"""Scoped standing consent and local enrollment; no HTTP, credentials or human review fabrication."""

from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
import unicodedata
import uuid
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from string import Formatter
from threading import Event
from typing import Any

from .cadence import Rule
from .library import LibraryError, source_status
from .media import checkpoint
from .preparation import (
    PreparationStore,
    YouTubeMetadata,
    media_token,
    metadata_errors,
    review_checks,
)
from .recurring import RecurringService, item_binding, normalized
from .scheduling import ScheduleError, ScheduleService, stamp
from .watching import WatchError, WatchService, directory, sig

CONSENT = (
    "Authorize this exact folder, channel, PUBLIC visibility, metadata rules, declarations, "
    "eligibility, cadence and daily cap for future qualifying files. Automatic mode does not mean "
    "a person reviewed each video. I am responsible for placing only appropriate user-controlled "
    "content here, with these declarations and no unresolved manual restriction or YouTube setting. "
    "Uploads must comply with YouTube Terms of Service and Community Guidelines. No second upload "
    "dialog appears. Review mode requires individual human review first. Existing files are excluded. "
    "Pause or disable this profile to stop new claims and sessions; already started remote operations "
    "are not undone. Global publishing and background service activation remain separate."
)
MAX_WAITING = 100


def title_for(filename: str, template: str) -> str:
    """Only {filename}; no conversion, format spec, attribute access or evaluation."""
    stem = unicodedata.normalize("NFC", Path(filename).stem)
    stem = re.sub(r"[_\s]+|\s*-{2,}\s*", " ", stem).strip(" -_")
    if not stem:
        raise ScheduleError("Filename produces an empty title; review this video.")
    try:
        for _, field, spec, conversion in Formatter().parse(template):
            if field is not None and (field != "filename" or spec or conversion):
                raise ValueError()
        title = template.format(filename=stem).strip()
    except (ValueError, KeyError, IndexError) as exc:
        raise ScheduleError("Only the literal {filename} substitution is supported.") from exc
    if (
        not title
        or len(title) > 100
        or any(c in title for c in "<>")
        or any(ord(c) < 32 for c in title)
    ):
        raise ScheduleError(
            "Generated title is empty, exceeds 100 characters or contains invalid characters. No truncation was applied."
        )
    return title


def settings_metadata(settings: dict[str, Any], filename: str) -> YouTubeMetadata:
    return YouTubeMetadata(
        title_for(filename, settings["title_template"]),
        settings["description"],
        tuple(settings["tags"]),
        settings["made_for_kids"],
        settings["synthetic"],
        "public",
    )


def folder_binding(folder: dict[str, Any]) -> dict[str, Any]:
    return {k: folder[k] for k in ("id", "path", "device", "inode", "consent_at")}


def validate_scope(
    db: sqlite3.Connection, profile: dict[str, Any], path: str = ""
) -> dict[str, Any]:
    row = db.execute("SELECT * FROM watch_folders WHERE id=?", (profile["folder_id"],)).fetchone()
    if not row or row["removed"] or folder_binding(dict(row)) != profile["folder_binding"]:
        raise ScheduleError(
            "Watched-folder consent changed or was removed. Renew profile authorization."
        )
    folder = dict(row)
    root, info = directory(folder["path"])
    if (info.st_dev, info.st_ino) != (folder["device"], folder["inode"]):
        raise ScheduleError("Authorized folder was replaced or moved; publishing is blocked.")
    if path and (Path(path).parent != root or Path(path).is_symlink()):
        raise ScheduleError("Source is outside the authorized folder or is a symbolic link.")
    return folder


def read_profile(db: sqlite3.Connection, identity: str) -> dict[str, Any]:
    row = db.execute("SELECT * FROM automation_profiles WHERE id=?", (identity,)).fetchone()
    if not row:
        raise ScheduleError("Automation profile no longer exists.")
    result = dict(row)
    for key in ("settings", "folder_binding"):
        result[key] = json.loads(result[key])
    return result


def approved_preparation(
    db: sqlite3.Connection, library: Any, identity: str, video_id: int, channel_id: str
) -> tuple[Any, Any, str]:
    """Explicit profile provenance only. Never authorizes manual Publish Now or generic schedules."""
    row = db.execute("SELECT * FROM automation_decisions WHERE id=?", (identity,)).fetchone()
    if not row or row["video_id"] != video_id or row["state"] not in ("Enrolling", "Queued"):
        raise ScheduleError("No automation-approved snapshot authorizes this source.")
    profile = read_profile(db, row["profile_id"])
    if (
        not profile["enabled"]
        or profile["revoked"]
        or profile["removed"]
        or profile["revision"] != row["profile_revision"]
        or profile["channel_id"] != channel_id
        or not profile["consent_at"]
    ):
        raise ScheduleError(
            "Automation is paused, revoked or changed. No new upload is authorized."
        )
    video_row = db.execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
    if not video_row:
        raise ScheduleError("Automation source was removed from the library.")
    video = library.decode(video_row)
    validate_scope(db, profile, video.path)
    prep = PreparationStore.read(db, video_id)
    if (
        prep.revision != row["preparation_revision"]
        or normalized(asdict(prep.metadata)) != json.loads(row["metadata"])
        or media_token(video) != row["media_token"]
        or source_status(video)
        or review_checks(video, prep, source_status(video)).errors
    ):
        raise ScheduleError(
            "Automation source or metadata changed. Hold for explicit review and reapproval."
        )
    if profile["settings"]["mode"] == "review" and (
        review_checks(video, prep, source_status(video)).state != "Review Complete"
        or prep.reviewed_media != media_token(video)
    ):
        raise ScheduleError("This profile requires current individual human review.")
    return video, prep, row["media_token"]


class AutomationService:
    def __init__(self, schedules: ScheduleService):
        self.schedules = schedules
        self.library = schedules.library
        self.clock = schedules.clock
        self.recurring = RecurringService(schedules)

    @staticmethod
    def event(
        db: sqlite3.Connection, profile: dict[str, Any], action: str, record: Any, when: str
    ) -> None:
        db.execute(
            "INSERT INTO automation_events(profile_id,revision,created_at,action,record) VALUES(?,?,?,?,?)",
            (profile["id"], profile["revision"], when, action, json.dumps(record)),
        )

    def preview(
        self,
        folder_id: str,
        channel: str,
        name: str,
        settings: dict[str, Any],
        *,
        identity: str = "",
        revision: int = 0,
    ) -> dict[str, Any]:
        data = normalized(settings)
        required = {
            "mode",
            "title_template",
            "description",
            "tags",
            "made_for_kids",
            "synthetic",
            "rule",
            "limit",
            "background",
            "pattern",
            "max_seconds",
            "max_bytes",
        }
        if set(data) != required or data["mode"] not in ("review", "automatic"):
            raise ScheduleError(
                "Choose review or explicitly approved automatic mode and complete every rule."
            )
        if type(data["background"]) is not bool or any(
            type(data[k]) is not bool for k in ("made_for_kids", "synthetic")
        ):
            raise ScheduleError(
                "Audience and synthetic-content declarations must be explicitly Yes or No."
            )
        if (
            not isinstance(data["pattern"], str)
            or not data["pattern"]
            or "/" in data["pattern"]
            or type(data["max_seconds"]) is not int
            or not 1 <= data["max_seconds"] <= 180
            or type(data["max_bytes"]) is not int
            or not 1 <= data["max_bytes"] <= 20 * 1024**3
        ):
            raise ScheduleError(
                "Choose a filename pattern, 1–180 seconds and a positive size limit up to 20 GiB."
            )
        errors = metadata_errors(settings_metadata(data, "Example video.mp4"))
        if errors:
            raise ScheduleError(" ".join(errors))
        rule = Rule.decode(data["rule"])
        rule.validate()
        if not name.strip() or len(name.strip()) > 100:
            raise ScheduleError("Name the automation using 1–100 characters.")
        with closing(self.library.connect()) as db:
            folder_row = db.execute(
                "SELECT * FROM watch_folders WHERE id=?", (folder_id,)
            ).fetchone()
            if not folder_row or folder_row["removed"]:
                raise ScheduleError("Select an explicitly configured watched folder first.")
            folder = dict(folder_row)
            scope = {"folder_id": folder_id, "folder_binding": folder_binding(folder)}
            validate_scope(db, scope)
            if data["background"] and not folder["background"]:
                raise ScheduleError(
                    "This folder has no background discovery permission. Reconfigure it explicitly first."
                )
            account = db.execute(
                "SELECT channel FROM account_connection WHERE platform='youtube'"
            ).fetchone()
            if not account or json.loads(account[0])["id"] != channel:
                raise ScheduleError("Connect and confirm the exact intended channel first.")
            limit = db.execute("SELECT daily_limit FROM publishing_control WHERE id=1").fetchone()[
                0
            ]
            if type(data["limit"]) is not int or not 1 <= data["limit"] <= limit:
                raise ScheduleError(f"YouTube allows at most {limit} uploads a day.")
            if identity:
                old = read_profile(db, identity)
                if old["revision"] != revision or old["removed"]:
                    raise ScheduleError(
                        "This auto upload changed elsewhere. Reload and edit again."
                    )
        return dict(
            id=identity,
            expected_revision=revision,
            name=name.strip(),
            **scope,
            channel_id=channel,
            channel_title=json.loads(account[0])["title"],
            settings=data,
            opportunities=[stamp(t) for t in rule.next(self.clock())],
        )

    def approve(
        self,
        proposal: dict[str, Any],
        cancel: Event,
        *,
        consent: bool,
        declarations: bool,
        restrictions: bool,
    ) -> str:
        if not all((consent, declarations, restrictions)):
            raise ScheduleError(
                "Explicit scope, declarations and unresolved-restriction confirmations are required."
            )
        fresh = self.preview(
            proposal["folder_id"],
            proposal["channel_id"],
            proposal["name"],
            proposal["settings"],
            identity=proposal["id"],
            revision=proposal["expected_revision"],
        )
        if fresh != proposal:
            raise ScheduleError("Automation confirmation changed. Review a fresh summary.")
        # Capture all present names, even not yet observed by the scanner. They never inherit
        # future-file authority, except videos an edited auto upload was still waiting to upload.
        root, _ = directory(proposal["folder_binding"]["path"])
        present = {}
        for entry in root.iterdir():
            checkpoint(cancel)
            try:
                present[entry.name] = sig(entry)
            except (OSError, WatchError):
                present[entry.name] = ""
        when = stamp(self.clock())
        identity = proposal["id"] or uuid.uuid4().hex
        settings = proposal["settings"]
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            validate_scope(db, proposal)
            old = read_profile(db, identity) if proposal["id"] else None
            if old and (old["revision"] != proposal["expected_revision"] or old["removed"]):
                raise ScheduleError("Profile changed while confirming; reload.")
            waiting = self.waiting(db, old) if old else set()
            if old and old["folder_id"] != proposal["folder_id"]:
                waiting = set()  # another folder: only its future files
            excluded = {k: v for k, v in present.items() if k not in waiting}
            plan_id = old["plan_id"] if old else uuid.uuid4().hex
            if datetime_from(proposal["opportunities"][0]) <= self.clock():
                raise ScheduleError("First opportunity passed; confirm a fresh preview.")
            account = db.execute(
                "SELECT channel FROM account_connection WHERE platform='youtube'"
            ).fetchone()
            limit = db.execute("SELECT daily_limit FROM publishing_control WHERE id=1").fetchone()[
                0
            ]
            if (
                not account
                or json.loads(account[0])["id"] != proposal["channel_id"]
                or settings["limit"] > limit
            ):
                raise ScheduleError("The channel changed during approval; review it again.")
            revision = old["revision"] + 1 if old else 1
            if old:
                # Edited: the same plan takes the new times. Unstarted videos leave it and are
                # enrolled again, with the new details, under the new approval.
                db.execute(
                    """UPDATE recurring_items SET state='Removed',removed_at=?,
                    reason='Auto upload edited; uploads with the new settings instead'
                    WHERE plan_id=? AND state IN ('Queued','Needs Attention') AND job_id IS NULL""",
                    (when, plan_id),
                )
                db.execute(
                    """UPDATE recurring_plans SET name=?,channel_id=?,channel_title=?,rule=?,enabled=1,
                    background=?,revision=revision+1,approved_revision=approved_revision+1,
                    consent_at=?,consent=?,updated_at=?,next_slot=?,watermark=?,approved_limit=?
                    WHERE id=?""",
                    (
                        proposal["name"],
                        proposal["channel_id"],
                        proposal["channel_title"],
                        json.dumps(settings["rule"]),
                        int(settings["background"]),
                        when,
                        CONSENT,
                        when,
                        proposal["opportunities"][0],
                        when,
                        settings["limit"],
                        plan_id,
                    ),
                )
            else:
                db.execute(
                    """INSERT INTO recurring_plans VALUES(?,?,'youtube',?,?,?,1,?,1,1,?,?,?,?,?,?,?)""",
                    (
                        plan_id,
                        proposal["name"],
                        proposal["channel_id"],
                        proposal["channel_title"],
                        json.dumps(settings["rule"]),
                        int(settings["background"]),
                        when,
                        CONSENT,
                        when,
                        when,
                        proposal["opportunities"][0],
                        when,
                        settings["limit"],
                    ),
                )
            db.execute(
                """INSERT INTO automation_profiles(id,name,platform,folder_id,folder_binding,channel_id,settings,revision,enabled,consent_at,consent,plan_id,created_at,updated_at)
                VALUES(?,?,'youtube',?,?,?,?,?,1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,folder_id=excluded.folder_id,folder_binding=excluded.folder_binding,channel_id=excluded.channel_id,
                settings=excluded.settings,revision=excluded.revision,enabled=1,revoked=0,removed=0,consent_at=excluded.consent_at,
                consent=excluded.consent,plan_id=excluded.plan_id,updated_at=excluded.updated_at,status=''""",
                (
                    identity,
                    proposal["name"],
                    proposal["folder_id"],
                    json.dumps(proposal["folder_binding"]),
                    proposal["channel_id"],
                    json.dumps(settings),
                    revision,
                    when,
                    CONSENT,
                    plan_id,
                    when,
                    when,
                ),
            )
            for name, observed_signature in excluded.items():
                db.execute(
                    """INSERT INTO automation_decisions(id,profile_id,profile_revision,folder_id,name,state,reason,snapshot,created_at,updated_at)
                    VALUES(?,?,?,?,?,'Excluded existing','Present at authorization; future files only',?,?,?)""",
                    (
                        uuid.uuid4().hex,
                        identity,
                        revision,
                        proposal["folder_id"],
                        name,
                        observed_signature,
                        when,
                        when,
                    ),
                )
            profile = read_profile(db, identity)
            self.event(db, profile, "Standing scope authorized", proposal, when)
            checkpoint(cancel)
        return identity

    @staticmethod
    def waiting(db: sqlite3.Connection, p: dict[str, Any]) -> set[str]:
        """Files this profile has not uploaded or started yet: an edit applies to them."""
        return {
            r[0]
            for r in db.execute(
                """SELECT d.name FROM automation_decisions d
                LEFT JOIN recurring_items i ON i.id=d.item_id
                WHERE d.profile_id=? AND d.profile_revision=? AND (
                    (d.item_id IS NULL AND d.state IN ('Needs review','Queue full'))
                    OR (i.state IN ('Queued','Needs Attention') AND i.job_id IS NULL))""",
                (p["id"], p["revision"]),
            )
        }

    @staticmethod
    def owned(
        db: sqlite3.Connection, p: dict[str, Any], name: str, video_id: int, prep: Any
    ) -> bool:
        """Whether an earlier approval of this profile wrote these exact saved details."""
        row = db.execute(
            """SELECT metadata,preparation_revision FROM automation_decisions
            WHERE profile_id=? AND name=? AND video_id=? AND profile_revision<? AND metadata!=''
            ORDER BY profile_revision DESC LIMIT 1""",
            (p["id"], name, video_id, p["revision"]),
        ).fetchone()
        return bool(
            row
            and row["preparation_revision"] == prep.revision
            and json.loads(row["metadata"]) == normalized(asdict(prep.metadata))
        )

    def control(self, identity: str, revision: int, action: str) -> None:
        if action not in ("pause", "resume", "disable", "remove"):
            raise ScheduleError("Unknown automation control.")
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            p = read_profile(db, identity)
            if p["revision"] != revision or p["removed"]:
                raise ScheduleError("Profile changed elsewhere. Reload.")
            if action == "resume" and p["revoked"]:
                raise ScheduleError("Disabled authorization requires a new explicit confirmation.")
            if action == "resume":
                validate_scope(db, p)
            self.recurring.skip_past(
                db, self.recurring.plan(db, p["plan_id"]), "Automation pause/resume; no catch-up."
            )
            db.execute(
                "UPDATE recurring_plans SET enabled=?,revision=revision+1 WHERE id=?",
                (int(action == "resume"), p["plan_id"]),
            )
            if action == "remove":
                # A deleted auto upload leaves nothing waiting behind it.
                db.execute(
                    """UPDATE recurring_items SET state='Removed',removed_at=?,
                    reason='Auto upload deleted before this video uploaded'
                    WHERE plan_id=? AND state IN ('Queued','Needs Attention') AND job_id IS NULL""",
                    (stamp(self.clock()), p["plan_id"]),
                )
            db.execute(
                "UPDATE automation_profiles SET enabled=?,revoked=?,removed=?,updated_at=? WHERE id=?",
                (
                    int(action == "resume"),
                    int(action in ("disable", "remove") or p["revoked"]),
                    int(action == "remove"),
                    stamp(self.clock()),
                    identity,
                ),
            )
            self.event(db, p, action, {}, stamp(self.clock()))

    def state(self) -> dict[str, Any]:
        with closing(self.library.connect()) as db:
            db.execute("BEGIN")
            profiles = [
                read_profile(db, r[0])
                for r in db.execute("SELECT id FROM automation_profiles ORDER BY created_at,id")
            ]
            decisions = [
                dict(r)
                for r in db.execute("SELECT * FROM automation_decisions ORDER BY created_at,id")
            ]
        return {"profiles": profiles, "decisions": decisions}

    def reconcile(self, scanner: WatchService, cancel: Event, *, background: bool = False) -> None:
        for p in self.state()["profiles"]:
            if (
                not p["enabled"]
                or p["revoked"]
                or p["removed"]
                or (background and not p["settings"]["background"])
            ):
                continue
            try:
                with closing(self.library.connect()) as db:
                    folder = validate_scope(db, p)
                if not folder["enabled"] or (background and not folder["background"]):
                    continue
                for file in scanner.files(p["folder_id"]):
                    checkpoint(cancel)
                    with closing(self.library.connect()) as db:
                        old = db.execute(
                            "SELECT * FROM automation_decisions WHERE profile_id=? AND profile_revision=? AND name=?",
                            (p["id"], p["revision"], file["name"]),
                        ).fetchone()
                    if old and old["state"] not in ("Needs review", "Queue full"):
                        continue
                    if file["state"] in (
                        "Waiting for file to finish",
                        "Ready for inspection",
                        "Importing",
                        "Ignored existing file",
                    ):
                        continue
                    try:
                        self.enroll(p, file, scanner, cancel, background=background)
                    except (ScheduleError, LibraryError, WatchError, OSError) as exc:
                        self.decision(p, file, "Needs review", str(exc))
                with closing(self.library.connect()) as db, db:
                    db.execute(
                        "UPDATE automation_profiles SET status='Reconciled local eligibility' WHERE id=?",
                        (p["id"],),
                    )
            except (ScheduleError, WatchError, OSError) as exc:
                with closing(self.library.connect()) as db, db:
                    db.execute(
                        "UPDATE automation_profiles SET status=? WHERE id=?", (str(exc), p["id"])
                    )

    def decision(self, p: dict[str, Any], file: dict[str, Any], state: str, reason: str) -> None:
        when = stamp(self.clock())
        with closing(self.library.connect()) as db, db:
            db.execute(
                """INSERT INTO automation_decisions(id,profile_id,profile_revision,folder_id,name,source_hash,video_id,state,reason,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(profile_id,profile_revision,name) DO UPDATE SET
                state=excluded.state,reason=excluded.reason,updated_at=excluded.updated_at WHERE automation_decisions.item_id IS NULL AND automation_decisions.state!='Excluded existing'""",
                (
                    uuid.uuid4().hex,
                    p["id"],
                    p["revision"],
                    p["folder_id"],
                    file["name"],
                    file["sha256"],
                    file["video_id"],
                    state,
                    reason,
                    when,
                    when,
                ),
            )

    def enroll(
        self,
        p: dict[str, Any],
        file: dict[str, Any],
        scanner: WatchService,
        cancel: Event,
        *,
        background: bool,
    ) -> None:
        if file["state"] != "Imported successfully" or not file["video_id"]:
            self.decision(
                p,
                file,
                "Duplicate — skipped" if file["sha256"] else "Needs review",
                file["message"] or file["state"],
            )
            return
        settings = p["settings"]
        if not fnmatch.fnmatchcase(file["name"], settings["pattern"]):
            raise ScheduleError("Filename does not match the approved eligibility pattern.")
        video = self.library.get(file["video_id"])
        path = Path(video.path)
        with closing(self.library.connect()) as db:
            validate_scope(db, p, str(path))
            # Waiting is not authorization. Avoid rereading unchanged bytes just to
            # report the same human-review hold; always hash again before enrollment.
            old = db.execute(
                "SELECT * FROM automation_decisions WHERE profile_id=? AND profile_revision=? AND name=?",
                (p["id"], p["revision"], file["name"]),
            ).fetchone()
            prep = PreparationStore.read(db, video.id)
            if (
                settings["mode"] == "review"
                and old
                and old["state"] == "Needs review"
                and old["reason"] == "Individual review required"
                and not old["item_id"]
                and old["video_id"] == video.id
                and old["source_hash"] == file["sha256"]
                and old["preparation_revision"] == prep.revision
                and old["media_token"] == media_token(video)
                and json.loads(old["metadata"]) == normalized(asdict(prep.metadata))
                and sig(path) == file["signature"]
                and not source_status(video)
                and review_checks(video, prep, "").state != "Review Complete"
            ):
                checkpoint(cancel)
                return
        if (
            video.media.duration > settings["max_seconds"]
            or video.media.size > settings["max_bytes"]
        ):
            raise ScheduleError("Video exceeds the approved duration or size limit.")
        with closing(self.library.connect()) as db:
            for excluded in db.execute(
                "SELECT snapshot FROM automation_decisions WHERE profile_id=? AND profile_revision=? AND state='Excluded existing'",
                (p["id"], p["revision"]),
            ):
                if (
                    excluded[0]
                    and json.loads(excluded[0])[-2:] == json.loads(file["signature"])[-2:]
                ):
                    raise ScheduleError(
                        "This source existed at authorization; renaming it does not grant future-file permission."
                    )
        digest = scanner.hash_file(path, file["signature"], cancel)
        if digest != file["sha256"]:
            raise ScheduleError("Source fingerprint changed since discovery.")
        proposed = settings_metadata(settings, file["name"])
        errors = metadata_errors(proposed)
        if errors:
            raise ScheduleError(" ".join(errors))
        when = stamp(self.clock())
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = read_profile(db, p["id"])
            if current != p and any(
                current[k] != p[k]
                for k in ("revision", "settings", "enabled", "revoked", "removed", "folder_binding")
            ):
                raise ScheduleError("Profile changed during enrollment.")
            if (
                not current["enabled"]
                or current["revoked"]
                or (background and not settings["background"])
            ):
                raise ScheduleError("Profile no longer authorizes enrollment.")
            folder = validate_scope(db, current, str(path))
            if not folder["enabled"] or sig(path) != file["signature"]:
                raise ScheduleError("Source or folder permission changed during enrollment.")
            observed = db.execute(
                "SELECT * FROM watch_files WHERE folder_id=? AND name=?",
                (p["folder_id"], file["name"]),
            ).fetchone()
            if (
                not observed
                or observed["signature"] != file["signature"]
                or observed["sha256"] != digest
            ):
                raise ScheduleError("Discovery identity changed during enrollment.")
            old = db.execute(
                "SELECT * FROM automation_decisions WHERE profile_id=? AND profile_revision=? AND name=?",
                (p["id"], p["revision"], file["name"]),
            ).fetchone()
            if old and (old["item_id"] or old["state"] == "Excluded existing"):
                return
            identity = old["id"] if old else uuid.uuid4().hex
            prep = PreparationStore.read(db, video.id)
            carried = bool(
                prep.revision and not old and self.owned(db, p, file["name"], video.id, prep)
            )
            if carried and prep.metadata != proposed:
                # Details this auto upload wrote itself (never edited by hand) follow its edit.
                db.execute(
                    "UPDATE preparations SET payload=?,revision=revision+1,modified_at=? "
                    "WHERE video_id=? AND platform='youtube'",
                    (json.dumps(asdict(proposed)), when, video.id),
                )
                prep = PreparationStore.read(db, video.id)
            if prep.revision == 0:
                db.execute(
                    "INSERT INTO preparations(video_id,platform,payload,revision,modified_at) VALUES(?,'youtube',?,1,?)",
                    (video.id, json.dumps(asdict(proposed)), when),
                )
                prep = PreparationStore.read(db, video.id)
            elif (
                settings["mode"] == "automatic"
                and not carried
                and (
                    not old
                    or prep.revision != old["preparation_revision"]
                    or prep.metadata != proposed
                )
            ):
                raise ScheduleError(
                    "Saved metadata was edited or already exists; individual review is required. Automation does not overwrite it."
                )
            errors = review_checks(video, prep, source_status(video)).errors
            if errors:
                raise ScheduleError(" ".join(errors))
            db.execute(
                """INSERT INTO automation_decisions(id,profile_id,profile_revision,folder_id,name,source_hash,video_id,state,reason,metadata,preparation_revision,media_token,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'Needs review','Individual review required',?,?,?,?,?)
                ON CONFLICT(profile_id,profile_revision,name) DO UPDATE SET metadata=excluded.metadata,preparation_revision=excluded.preparation_revision,
                media_token=excluded.media_token,updated_at=excluded.updated_at""",
                (
                    identity,
                    p["id"],
                    p["revision"],
                    p["folder_id"],
                    file["name"],
                    digest,
                    video.id,
                    json.dumps(asdict(prep.metadata)),
                    prep.revision,
                    media_token(video),
                    when,
                    when,
                ),
            )
            if settings["mode"] == "review" and (
                review_checks(video, prep, source_status(video)).state != "Review Complete"
                or prep.reviewed_media != media_token(video)
            ):
                checkpoint(cancel)
                return
            if (
                db.execute(
                    "SELECT count(*) FROM recurring_items WHERE plan_id=? AND state='Queued'",
                    (p["plan_id"],),
                ).fetchone()[0]
                >= MAX_WAITING
            ):
                db.execute(
                    "UPDATE automation_decisions SET state='Queue full',reason='100 waiting entries; retry on reconciliation' WHERE id=?",
                    (identity,),
                )
                return
            # Different byte identities generating an indistinguishable title need a human decision.
            for previous in db.execute(
                """SELECT d.metadata FROM automation_decisions d
                LEFT JOIN recurring_items i ON i.id=d.item_id
                WHERE d.profile_id=? AND d.item_id IS NOT NULL AND d.source_hash!=?
                AND coalesce(i.state,'')!='Removed'""",
                (p["id"], digest),
            ):
                if (
                    unicodedata.normalize("NFKC", json.loads(previous[0])["title"]).casefold()
                    == unicodedata.normalize("NFKC", prep.metadata.title).casefold()
                ):
                    raise ScheduleError(
                        "Another enrolled video has this generated title. Review the ambiguity manually."
                    )
            self.recurring.conflict(db, video.id, p["channel_id"], digest)
            db.execute("UPDATE automation_decisions SET state='Enrolling' WHERE id=?", (identity,))
            snapshot = self.schedules._snapshot(db, video.id, p["channel_id"], automation=identity)
            snapshot["sha256"] = digest
            snapshot = normalized(snapshot)
            plan = self.recurring.plan(db, p["plan_id"])
            item_id = uuid.uuid4().hex
            position = db.execute(
                "SELECT coalesce(max(position),0)+1 FROM recurring_items WHERE plan_id=?",
                (p["plan_id"],),
            ).fetchone()[0]
            db.execute(
                """INSERT INTO recurring_items(id,plan_id,video_id,channel_id,source_hash,snapshot,position,state,approved_at,binding)
                VALUES(?,?,?,?,?,?,?,'Queued',?,?)""",
                (
                    item_id,
                    p["plan_id"],
                    video.id,
                    p["channel_id"],
                    digest,
                    json.dumps(snapshot),
                    position,
                    p["consent_at"],
                    item_binding(plan, video.id, snapshot),
                ),
            )
            db.execute(
                "UPDATE automation_decisions SET state='Queued',reason='Approved by automation profile; not individual human review',snapshot=?,item_id=? WHERE id=?",
                (json.dumps(snapshot), item_id, identity),
            )
            self.event(
                db,
                p,
                "Enrolled exact source",
                {"decision": identity, "item": item_id, "sha256": digest},
                when,
            )
            checkpoint(cancel)


def datetime_from(value: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(value)
