"""Posting through Buffer (TikTok, Instagram, Facebook): validation, hosting, submission, status
and automation, parameterized by a `Platform`.

ShortsPublisher decides *which* video posts and *when*; Buffer performs the post at that exact
time (`schedulingType: automatic`, `mode: customScheduled` + `dueAt`, or `shareNow`). Buffer's own
queue (`addToQueue`) is never used, and each automation keeps at most one unsent Buffer post, so
there are no competing queues. Status comes from Buffer (`scheduled`, `sending`, `sent`, `error`);
a Buffer post ID is not a platform video ID and nothing here invents one.

Every platform has its own tables (`<key>_posts`, …), connection row and operation lock. The
Buffer accounts (app clients), Keychain namespace, media hosting and token lock are shared, and
platforms may share one Buffer sign-in (one Keychain key). Backup Buffer and Cloudinary accounts
(separate sign-ups, each with its own limits) take over automatically when one reaches its limits:
a Buffer backup is used only for the same TikTok/Instagram/Facebook account, connected in it too.
Each post remembers both, so a switch never strands a hosted copy or a status check.
"""

from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout

from .buffer import (
    BufferAPI,
    BufferAuthority,
    BufferClientConfig,
    BufferError,
    BufferOAuth,
    BufferTokens,
    SecretStore,
)
from .cadence import Rule
from .hosting import FREE_PLAN_MAX_BYTES, CloudinaryHost, HostingError
from .library import Library, LibraryError, Video, source_status
from .preparation import PreparationStore, review_checks
from .queues import decode as decode_queue
from .scheduling import ScheduleError
from .source import fingerprint

if TYPE_CHECKING:
    from .watching import WatchService

HASHTAGS_MAX = 5  # the shared caption's hashtag count (Buffer's TikTok and Instagram limit)
# The Buffer app client is saved once, in the row Stage 19 created for TikTok; it serves every
# Buffer platform and is not moved.
CLIENT_ROW = "tiktok"
MAIN = "main"  # the main Buffer account (that saved app client); backups have their own IDs
FULL_PAUSE = timedelta(hours=24)  # a Cloudinary account over its limits is skipped this long
QUEUE_PAUSE = 3600  # seconds a Buffer account that refused a post over a plan limit is skipped
RECONNECT_STATES = (
    "Waiting for TikTok reconnection",
    "Waiting for Instagram reconnection",
    "Waiting for Facebook reconnection",
)
UNSENT = (
    "Uploading to media host",
    "Hosted and ready",
    "Waiting for Buffer limit",
    *RECONNECT_STATES,
    "Sending to Buffer",
    "Scheduled in Buffer",
    "Publishing",
    "Needs reconciliation",
)
CHECK_BACKOFF = (2, 5, 15, 60)  # minutes after the due time; then hourly
HOSTING_RETRY = timedelta(minutes=30)  # wait before copying a failed video to the host again
RETRY_NOTE = " Trying again shortly; if its time passes it moves to the next upload time."
GIVE_UP_HOURS = 48
RETENTION_GRACE = timedelta(hours=24)
INSIGHTS_KEY = "buffer-insights-key"  # optional personal API key, only for view counts
HOSTING_CONSENT = (
    "Videos you post through Buffer (TikTok, Instagram or Facebook) are first copied to your "
    "Cloudinary account and served from a public HTTPS link, because Buffer only accepts a public "
    "video URL. Anyone who obtains that link can watch the copy until it is deleted. Links use "
    "unpredictable names, but that is not privacy. Copies are made only when you post or schedule "
    "through Buffer, or when an approved automation enrolls a new video."
)


@dataclass(frozen=True)
class Platform:
    """What Buffer accepts for automatic posting on one platform, and how to ask for it."""

    key: str  # Buffer's service name, the table prefix and the account_connection row
    name: str
    caption_max: int
    hashtags_max: int | None
    extensions: tuple[str, ...]
    seconds: tuple[int, int]
    min_size: tuple[int, int]  # width, height
    max_bytes: int
    fps: tuple[int, int] | None = None
    aspect: tuple[float, float] | None = None  # displayed width / height, inclusive
    max_width: int | None = None
    ai_label: bool = True  # Buffer accepts an AI-generated content flag
    account: str = "account"  # what the destination is called ("Page" on Facebook)
    daily_max: int = 25  # the most posts a day the platform accepts through Buffer
    excluded_types: tuple[str, ...] = ()  # Buffer channel types that can't post automatically
    metadata: Callable[[bool], dict[str, Any]] = field(default=lambda ai: {})

    @property
    def reconnect_state(self) -> str:
        return f"Waiting for {self.name} reconnection"

    @property
    def automation_consent(self) -> str:
        return (
            "I authorize ShortsPublisher to copy each NEW eligible video in this exact folder to "
            f"my media host and post it PUBLICLY to this exact {self.name} {self.account} through "
            "Buffer, at the approved times, with this caption rule"
            + (" and AI disclosure" if self.ai_label else "")
            + ", until I pause, revoke or remove this automation. Videos already in the folder "
            "are excluded."
        )


TIKTOK = Platform(
    key="tiktok",
    name="TikTok",
    caption_max=2200,
    hashtags_max=HASHTAGS_MAX,
    extensions=(".mp4", ".mov", ".webm"),
    seconds=(3, 600),
    min_size=(360, 360),
    max_bytes=1024**3,
    fps=(23, 60),
    daily_max=25,  # Buffer: 25 TikTok posts per 24 hours
    metadata=lambda ai: {"tiktok": {"isAiGenerated": ai}},
)
# Buffer's Instagram article (2026-10-22): automatic Reels need a Business or Creator account,
# 3 s–15 min, between 4:5 and 9:16, at most 1920 px wide and 300 MB; at most 5 caption hashtags.
INSTAGRAM = Platform(
    key="instagram",
    name="Instagram",
    caption_max=2200,
    hashtags_max=HASHTAGS_MAX,
    extensions=(".mp4", ".mov"),
    seconds=(3, 900),
    min_size=(0, 0),
    max_bytes=300 * 1000**2,
    fps=(23, 60),
    aspect=(9 / 16, 4 / 5),
    max_width=1920,
    daily_max=50,  # Instagram's API allows 50–100 published posts per 24 hours
    # Reels also appear on the profile grid; Buffer requires the choice and it can't change later.
    metadata=lambda ai: {
        "instagram": {"type": "reel", "shouldShareToFeed": True, "isAiGenerated": ai}
    },
)
# Buffer's Facebook article and Meta's Reels spec: Pages only, 3–90 s, 9:16, at least 540×960,
# 24–60 fps, MP4, captions up to 5,000 characters. Facebook's API has no AI-content label.
FACEBOOK = Platform(
    key="facebook",
    name="Facebook",
    caption_max=5000,
    hashtags_max=None,
    extensions=(".mp4",),
    seconds=(3, 90),
    min_size=(540, 960),
    max_bytes=1024**3,
    fps=(24, 60),
    aspect=(9 / 16, 9 / 16),
    ai_label=False,
    account="Page",
    daily_max=35,  # Buffer: 35 Facebook posts per 24 hours
    excluded_types=("group", "profile"),  # Groups are notification-only; profiles can't connect
    metadata=lambda ai: {"facebook": {"type": "reel"}},
)
PLATFORMS = (TIKTOK, INSTAGRAM, FACEBOOK)
BUFFER_PLATFORMS = tuple(p.key for p in PLATFORMS)


def usable_channels(spec: Platform, channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """This platform's channels that Buffer can post to automatically."""
    return [
        c
        for c in channels
        if str(c.get("service", spec.key)).lower() == spec.key
        and str(c.get("type") or "") not in spec.excluded_types
        and not c.get("isDisconnected")
        and not c.get("isLocked")
    ]


def over_plan(message: str) -> bool:
    """Buffer refused a post because the account reached a plan limit (scheduled posts, posts
    per day), not because of the post itself."""
    text = message.lower()
    return not any(w in text for w in ("character", "length", "caption")) and any(
        w in text for w in ("limit", "quota", "upgrade")
    )


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def readable(filename: str) -> str:
    return re.sub(r"[_\s]+", " ", Path(filename).stem).strip()


def caption_for(template: str, filename: str) -> str:
    return template.replace("{filename}", readable(filename)).strip()


def caption_from(
    title: str, description: str, tags: tuple[str, ...], hashtags: int | None = HASHTAGS_MAX
) -> str:
    """One caption from the shared video details: title, description, then tags as hashtags.

    #Shorts is YouTube's own marker and is left out; hashtags stop at the platform's limit."""

    def tidy(text: str) -> str:
        return re.sub(r"[ \t]*#shorts\b", "", text, flags=re.IGNORECASE).strip()

    parts = [tidy(title), tidy(description)]
    present = {tag.lower() for tag in re.findall(r"#\w+", " ".join(parts))}
    found: list[str] = []
    for tag in tags:
        word = re.sub(r"\W", "", tag)
        mark = f"#{word}"
        if word and mark.lower() not in present and (hashtags is None or len(present) < hashtags):
            present.add(mark.lower())
            found.append(mark)
    parts.append(" ".join(found))
    return "\n\n".join(part for part in parts if part)


def caption_errors(spec: Platform, caption: str) -> list[str]:
    errors = []
    if len(caption) > spec.caption_max:
        errors.append(
            f"Caption is {len(caption)} characters; {spec.name} via Buffer allows {spec.caption_max}."
        )
    if spec.hashtags_max is not None and len(re.findall(r"#\w+", caption)) > spec.hashtags_max:
        errors.append(f"Use at most {spec.hashtags_max} hashtags (Buffer's {spec.name} limit).")
    return errors


def span(low: int, high: int) -> str:
    def words(value: int) -> str:
        return f"{value // 60} minutes" if value >= 60 and value % 60 == 0 else f"{value} seconds"

    if high < 60 or high % 60:
        return f"{low} to {high} seconds"
    return f"{words(low)} to {words(high)}"


def listed(items: tuple[str, ...]) -> str:
    names = [item.lstrip(".").upper() for item in items]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def media_errors(spec: Platform, video: Video, hosting_limit: int) -> list[str]:
    m, errors = video.media, []
    if Path(video.path).suffix.lower() not in spec.extensions:
        errors.append(f"{spec.name} via Buffer accepts {listed(spec.extensions)} files.")
    low, high = spec.seconds
    if not low <= m.duration <= high:
        errors.append(f"{spec.name} automatic posts must be {span(low, high)} long.")
    width, height = spec.min_size
    if m.width < width or m.height < height:
        errors.append(
            f"{spec.name} needs at least {width} pixels in width and height."
            if width == height
            else f"{spec.name} needs at least {width}×{height} pixels."
        )
    if spec.max_width and m.width > spec.max_width:
        errors.append(f"{spec.name} accepts videos at most {spec.max_width} pixels wide.")
    if spec.fps and m.fps is not None and not spec.fps[0] <= m.fps <= spec.fps[1]:
        errors.append(
            f"{spec.name} via Buffer needs {spec.fps[0]}–{spec.fps[1]} frames per second."
        )
    if spec.aspect and not spec.aspect[0] * 0.99 <= m.aspect <= spec.aspect[1] * 1.01:
        low_ratio, high_ratio = spec.aspect
        errors.append(
            f"{spec.name} needs a vertical 9:16 video."
            if low_ratio == high_ratio
            else f"{spec.name} needs a portrait video between 9:16 and 4:5."
        )
    limit = min(spec.max_bytes, hosting_limit)
    if m.size > limit:
        errors.append(
            f"The file is {m.size / 1024**2:.0f} MiB; the current limit is {limit / 1024**2:.0f} MiB."
        )
    return errors


class SocialError(Exception):
    pass


class BufferPostService:
    def __init__(
        self,
        spec: Platform,
        library: Library,
        secrets: SecretStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        *,
        api_session: Any | None = None,
        oauth: BufferOAuth | None = None,
        host_session: Any | None = None,
        anonymous: Any | None = None,
    ) -> None:
        self.spec, self.table = spec, spec.key
        self.library, self.secrets, self.clock = library, secrets, clock
        self.api_session, self.host_session, self.anonymous = api_session, host_session, anonymous
        self.authority = BufferAuthority(
            secrets,
            Path(str(library.path) + ".buffer.lock"),
            self.connection_client,
            lambda: (self.connection() or {}).get("key"),
            oauth or BufferOAuth(api_session, lambda: self.clock().timestamp()),
            lambda: self.clock().timestamp(),
        )
        self.lock = Path(str(library.path) + f".{spec.key}.lock")
        self.visible: dict[str, set[str]] = {}  # channel IDs each fresh sign-in can see

    # Configuration ---------------------------------------------------------------------------

    def client(self) -> BufferClientConfig | None:
        self.library.initialize()
        with closing(self.library.connect()) as db:
            row = db.execute(
                "SELECT client_config FROM account_config WHERE platform=?", (CLIENT_ROW,)
            ).fetchone()
        return BufferClientConfig.parse(json.loads(row[0])) if row else None

    def save_client(self, client_id: str, redirect_uri: str) -> BufferClientConfig:
        config = BufferClientConfig.parse({"client_id": client_id, "redirect_uri": redirect_uri})
        self.library.initialize()
        with closing(self.library.connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO account_config VALUES (?, ?)",
                (
                    CLIENT_ROW,
                    json.dumps(
                        {"client_id": config.client_id, "redirect_uri": config.redirect_uri}
                    ),
                ),
            )
        return config

    # Buffer accounts: the main app client, then backups for when one reaches its limits -------

    def buffer_accounts(self) -> list[dict[str, Any]]:
        """Every saved Buffer account, main first: id, label, client, API backoff, the standing
        sign-in (`key`, empty when not signed in) and its Buffer organizations."""
        main = self.client()
        found: list[dict[str, Any]] = []
        if main:
            with closing(self.library.connect()) as db:
                retry = db.execute("SELECT retry_after FROM buffer_control WHERE id=1").fetchone()
            found.append(
                {
                    "id": MAIN,
                    "label": "Main",
                    "client": main,
                    "retry_after": retry[0],
                    "key": self.working_sign_in(MAIN) or "",
                    "organizations": self.main_organizations(),
                }
            )
        with closing(self.library.connect()) as db:
            rows = db.execute("SELECT * FROM buffer_accounts ORDER BY position").fetchall()
        for row in rows:
            found.append(
                {
                    "id": row["id"],
                    "label": row["label"],
                    "client": BufferClientConfig(row["client_id"], row["redirect_uri"]),
                    "retry_after": row["retry_after"],
                    "key": row["credential_key"],
                    "organizations": json.loads(row["organizations"]),
                }
            )
        return found

    def main_organizations(self) -> list[str]:
        marks = ",".join("?" * len(BUFFER_PLATFORMS))
        with closing(self.library.connect()) as db:
            rows = db.execute(
                f"SELECT * FROM account_connection WHERE platform IN ({marks})", BUFFER_PLATFORMS
            ).fetchall()
        return sorted(
            {
                str(json.loads(r["channel"]).get("organization_id"))
                for r in rows
                if self.account_of(r["credential_key"]) == MAIN
            }
        )

    def standing_key(self, account: str) -> str:
        """A working sign-in of one Buffer account: its own (backups) or a platform's."""
        if account != MAIN:
            with closing(self.library.connect()) as db:
                row = db.execute(
                    "SELECT credential_key FROM buffer_accounts WHERE id=?", (account,)
                ).fetchone()
            if row and row[0]:
                return str(row[0])
        return self.working_sign_in(account) or ""

    def sign_in_backup(self, account: str, code: str, verifier: str) -> dict[str, list[str]]:
        """Sign a backup Buffer account in. Refused when it is a Buffer account already listed
        (the browser was still signed in to it). Returns which connected accounts it can post to."""
        key, tokens = self.store_sign_in(code, verifier, account)
        try:
            channels = BufferAPI(lambda: tokens.access_token, self.api_session).all_channels()
        except BufferError:
            self.forget_key(key)
            raise
        organizations = sorted({str(c["organization_id"]) for c in channels})
        for other in self.buffer_accounts():
            if other["id"] != account and set(other["organizations"]) & set(organizations):
                self.forget_key(key)
                raise BufferError(
                    f"That is the same Buffer account as “{other['label']}”. Sign out of Buffer "
                    "in your browser (or use a private window), then sign in with the other "
                    "Buffer account.",
                    "config",
                )
        with closing(self.library.connect()) as db, db:
            old = db.execute(
                "SELECT credential_key FROM buffer_accounts WHERE id=?", (account,)
            ).fetchone()
            db.execute(
                "UPDATE buffer_accounts SET credential_key=?, organizations=?, retry_after=NULL "
                "WHERE id=?",
                (key, json.dumps(organizations), account),
            )
        if old and old[0]:
            self.forget_key(old[0])
        return self.coverage(channels)

    def coverage(self, channels: list[dict[str, Any]]) -> dict[str, list[str]]:
        """Which connected platforms' accounts a Buffer account also has."""
        result: dict[str, list[str]] = {"found": [], "missing": []}
        with closing(self.library.connect()) as db:
            rows = {
                r["platform"]: json.loads(r["channel"])
                for r in db.execute("SELECT * FROM account_connection")
            }
        for spec in PLATFORMS:
            connected = rows.get(spec.key)
            if not connected:
                continue
            name = self.destination(connected)
            match = any(self.destination(c) == name for c in usable_channels(spec, channels))
            result["found" if match else "missing"].append(f"{spec.name} ({name})")
        return result

    def add_buffer_account(self, label: str, client_id: str, redirect_uri: str) -> str:
        """A backup Buffer account. An empty Client ID or redirect reuses the main one's (one
        public client can sign in to more than one Buffer account)."""
        accounts = self.buffer_accounts()
        if not accounts:
            raise BufferError("Save the main Buffer app client first.", "config")
        main = accounts[0]["client"]
        config = BufferClientConfig.parse(
            {
                "client_id": client_id.strip() or main.client_id,
                "redirect_uri": redirect_uri.strip() or main.redirect_uri,
            }
        )
        label = label.strip() or f"Buffer {len(accounts) + 1}"
        if any(a["label"].lower() == label.lower() for a in accounts):
            raise BufferError(f"A Buffer account is already called “{label}”.", "config")
        identity = "buffer-account-" + uuid.uuid4().hex
        with closing(self.library.connect()) as db, db:
            position = db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM buffer_accounts")
            db.execute(
                "INSERT INTO buffer_accounts(id,label,client_id,redirect_uri,retry_after,position,"
                "added_at) VALUES (?,?,?,?,NULL,?,?)",
                (
                    identity,
                    label[:60],
                    config.client_id,
                    config.redirect_uri,
                    position.fetchone()[0],
                    stamp(self.clock()),
                ),
            )
        return identity

    def remove_buffer_account(self, account: str) -> None:
        if account == MAIN:
            raise BufferError("The main Buffer app client is changed above, not removed.", "config")
        with closing(self.library.connect()) as db:
            keys = [
                r[0]
                for r in db.execute(
                    "SELECT credential_key FROM buffer_sign_ins WHERE account_id=?", (account,)
                )
            ]
        if any(self.key_in_use(key, standing=False) for key in keys):
            raise BufferError(
                "A platform is connected, or a post is still waiting, through this Buffer account. "
                "Reconnect those platforms with another account first.",
                "config",
            )
        with closing(self.library.connect()) as db, db:
            db.execute("DELETE FROM buffer_accounts WHERE id=?", (account,))
        for key in keys:
            self.forget_key(key)

    def account_of(self, key: str) -> str:
        """The Buffer account that issued a stored sign-in (sign-ins from before backups existed
        belong to the main one)."""
        self.library.initialize()
        with closing(self.library.connect()) as db:
            row = db.execute(
                "SELECT account_id FROM buffer_sign_ins WHERE credential_key=?", (key,)
            ).fetchone()
        return str(row[0]) if row else MAIN

    def client_for(self, account: str) -> BufferClientConfig | None:
        if account == MAIN:
            return self.client()
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM buffer_accounts WHERE id=?", (account,)).fetchone()
        return BufferClientConfig(row["client_id"], row["redirect_uri"]) if row else None

    def connection_client(self) -> BufferClientConfig | None:
        current = self.connection()
        return self.client_for(self.account_of(current["key"])) if current else self.client()

    def retry_after(self, account: str) -> str | None:
        """When this Buffer account's API allowance may be used again (each account has its own)."""
        with closing(self.library.connect()) as db:
            if account == MAIN:
                row = db.execute("SELECT retry_after FROM buffer_control WHERE id=1").fetchone()
            else:
                row = db.execute(
                    "SELECT retry_after FROM buffer_accounts WHERE id=?", (account,)
                ).fetchone()
        return row[0] if row else None

    def set_retry(self, account: str, seconds: float | None) -> None:
        until = stamp(self.clock() + timedelta(seconds=seconds or 900))
        with closing(self.library.connect()) as db, db:
            if account == MAIN:
                db.execute("UPDATE buffer_control SET retry_after=? WHERE id=1", (until,))
            else:
                db.execute("UPDATE buffer_accounts SET retry_after=? WHERE id=?", (until, account))

    def connection(self) -> dict[str, Any] | None:
        self.library.initialize()
        with closing(self.library.connect()) as db:
            row = db.execute(
                "SELECT * FROM account_connection WHERE platform=?", (self.table,)
            ).fetchone()
        if not row:
            return None
        return {
            "key": row["credential_key"],
            **json.loads(row["channel"]),
            "verified_at": row["verified_at"],
        }

    def set_connection_status(self, status: str) -> None:
        """Applies to every Buffer platform on the same sign-in: once one of them may have spent
        the refresh token, none of them may use it again."""
        current = self.connection()
        if current:
            self.mark_key(current["key"], status)

    def mark_key(self, key: str, status: str) -> None:
        with closing(self.library.connect()) as db, db:
            for row in self.sharing(db, key):
                channel = json.loads(row["channel"])
                if channel.get("status") != status:
                    channel["status"] = status
                    db.execute(
                        "UPDATE account_connection SET channel=? WHERE platform=?",
                        (json.dumps(channel), row["platform"]),
                    )

    @staticmethod
    def sharing(db: sqlite3.Connection, key: str) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(BUFFER_PLATFORMS))
        return db.execute(
            f"SELECT * FROM account_connection WHERE credential_key=? AND platform IN ({marks})",
            (key, *BUFFER_PLATFORMS),
        ).fetchall()

    def key_in_use(self, key: str, *, standing: bool = True) -> bool:
        """A platform is connected through this sign-in, a post sent with it is unsettled, or
        (standing) it is a backup Buffer account's own sign-in."""
        marks = ",".join("?" * len(UNSENT))
        with closing(self.library.connect()) as db:
            if (
                standing
                and db.execute(
                    "SELECT 1 FROM buffer_accounts WHERE credential_key=?", (key,)
                ).fetchone()
            ):
                return True
            return bool(self.sharing(db, key)) or any(
                db.execute(
                    f"SELECT 1 FROM {p}_posts WHERE buffer_key=? AND state IN ({marks}) LIMIT 1",
                    (key, *UNSENT),
                ).fetchone()
                for p in BUFFER_PLATFORMS
            )

    def forget_key(self, key: str) -> None:
        """Delete a Buffer sign-in from Keychain once nothing refers to it."""
        if key and not self.key_in_use(key):
            self.secrets.delete(key)
            with closing(self.library.connect()) as db, db:
                db.execute("DELETE FROM buffer_sign_ins WHERE credential_key=?", (key,))

    def shared_sign_in(self, account: str | None = None) -> dict[str, Any] | None:
        """A working Buffer sign-in another platform already uses (of one Buffer account, if
        given), offered for reuse."""
        self.library.initialize()
        with closing(self.library.connect()) as db:
            marks = ",".join("?" * len(BUFFER_PLATFORMS))
            rows = db.execute(
                f"SELECT * FROM account_connection WHERE platform IN ({marks}) AND platform!=? ORDER BY platform",
                (*BUFFER_PLATFORMS, self.table),
            ).fetchall()
        for row in rows:
            channel = json.loads(row["channel"])
            if channel.get("status") == "Connected" and (
                account is None or self.account_of(row["credential_key"]) == account
            ):
                return {"key": row["credential_key"], "platform": row["platform"], **channel}
        return None

    def token(self) -> str:
        current = self.connection()
        if current and current.get("status") == "Reconnect required":
            # After an uncertain or rejected renewal the stored refresh token may already be
            # spent; using it again would revoke the grant. Only a fresh sign-in may continue.
            raise BufferError(f"Reconnect {self.spec.name} in Accounts to continue.", "reconnect")
        try:
            return self.authority.access_token()
        except BufferError as exc:
            if exc.kind == "reconnect":
                self.set_connection_status("Reconnect required")
            raise

    def api(self) -> BufferAPI:
        return BufferAPI(self.token, self.api_session)

    # Optional view counts ---------------------------------------------------------------------

    def insights_key(self) -> str:
        return self.secrets.get(INSIGHTS_KEY) or ""

    def save_insights_key(self, key: str) -> None:
        key = key.strip()
        if not key:
            raise SocialError("Paste your Buffer personal API key first.")
        self.secrets.put(INSIGHTS_KEY, key)

    def remove_insights_key(self) -> None:
        self.secrets.delete(INSIGHTS_KEY)

    def statistics(self, cancel: Event) -> dict[str, dict[str, int]]:
        """View counts for sent posts, keyed by Buffer post ID. Read-only; empty without the
        optional personal API key. Buffer refreshes these once a day."""
        key = self.insights_key()
        if not key:
            return {}
        api = BufferAPI(lambda: key, self.api_session)
        found: dict[str, dict[str, int]] = {}
        sent = [p for p in self.posts() if p["state"] == "Sent by Buffer" and p["buffer_post_id"]]
        for post in sent[:100]:
            if cancel.is_set():
                break
            metrics = api.post_metrics(post["buffer_post_id"])
            counts = {
                name: metrics[source]
                for name, source in (
                    ("views", "views"),
                    ("likes", "reactions"),
                    ("comments", "comments"),
                )
                if source in metrics
            }
            if "views" not in counts and "impressions" in metrics:
                counts["views"] = metrics["impressions"]
            found[post["buffer_post_id"]] = counts
        return found

    def store_sign_in(self, code: str, verifier: str, account: str) -> tuple[str, BufferTokens]:
        config = self.client_for(account)
        if not config:
            raise BufferError("Save the Buffer app client first.", "config")
        tokens: BufferTokens = self.authority.oauth.exchange(config, code, verifier)
        key = "buffer-" + uuid.uuid4().hex
        self.authority.save(key, tokens)
        with closing(self.library.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO buffer_sign_ins VALUES (?, ?)", (key, account))
        return key, tokens

    def finish_connect(
        self, code: str, verifier: str, account: str = MAIN
    ) -> tuple[str, list[dict[str, Any]]]:
        """Exchange the code, store tokens under a fresh key, return this platform's channels."""
        key, tokens = self.store_sign_in(code, verifier, account)
        api = BufferAPI(lambda: tokens.access_token, self.api_session)
        try:
            every = api.all_channels()
        except BufferError:
            self.forget_key(key)
            raise
        self.visible[key] = {str(c.get("id")) for c in every}
        return key, self.usable_types(
            [c for c in every if str(c.get("service", "")).lower() == self.spec.key]
        )

    def reuse_sign_in(self) -> tuple[str, list[dict[str, Any]]]:
        """This platform's channels through the Buffer sign-in another platform already uses."""
        shared = self.shared_sign_in()
        if not shared:
            raise BufferError("There is no working Buffer sign-in to reuse. Sign in.", "auth")
        return shared["key"], self.usable_types(self.api_for(shared["key"]).channels(self.spec.key))

    def working_sign_in(self, account: str | None = None) -> str | None:
        """The Keychain key of a Buffer sign-in on this Mac that still works: this platform's
        own, or one another Buffer platform uses (of one Buffer account, if given)."""
        current = self.connection()
        if (
            current
            and current.get("status") == "Connected"
            and (account is None or self.account_of(current["key"]) == account)
        ):
            return str(current["key"])
        shared = self.shared_sign_in(account)
        return str(shared["key"]) if shared else None

    def account_channels(self, key: str) -> list[dict[str, Any]]:
        """Every channel, of every service, in the Buffer account this sign-in belongs to."""
        return self.api_for(key).all_channels()

    def finish_connect_all(
        self, code: str, verifier: str, account: str = MAIN
    ) -> tuple[str, list[dict[str, Any]]]:
        """Exchange the code; return the new sign-in's key and every channel of the account."""
        key, tokens = self.store_sign_in(code, verifier, account)
        try:
            every = BufferAPI(lambda: tokens.access_token, self.api_session).all_channels()
        except BufferError:
            self.forget_key(key)
            raise
        self.visible[key] = {str(c.get("id")) for c in every}
        return key, every

    def api_for(self, key: str) -> BufferAPI:
        """The Buffer API through a given stored sign-in, with the same renewal safety."""
        authority = BufferAuthority(
            self.secrets,
            self.authority.lock_path,
            lambda: self.client_for(self.account_of(key)),
            lambda: key,
            self.authority.oauth,
            self.authority.clock,
        )

        def token() -> str:
            with closing(self.library.connect()) as db:
                spent = any(
                    json.loads(r["channel"]).get("status") == "Reconnect required"
                    for r in self.sharing(db, key)
                )
            if spent:
                # Same rule as token(): a possibly spent refresh token is never used again.
                raise BufferError("Reconnect this Buffer account in Accounts.", "reconnect")
            try:
                return authority.access_token()
            except BufferError as exc:
                if exc.kind == "reconnect":
                    self.mark_key(key, "Reconnect required")
                    # A backup account's possibly spent sign-in is dropped: sign it in again.
                    with closing(self.library.connect()) as db, db:
                        db.execute(
                            "UPDATE buffer_accounts SET credential_key='' WHERE credential_key=?",
                            (key,),
                        )
                    self.forget_key(key)
                raise

        return BufferAPI(token, self.api_session)

    def usable_types(self, channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return usable_channels(self.spec, channels)

    def select_channel(self, key: str, channel: dict[str, Any]) -> None:
        if channel.get("isDisconnected") or channel.get("isLocked"):
            raise BufferError(
                f"This {self.spec.name} channel is disconnected or locked in Buffer. Reconnect it in Buffer first.",
                "config",
            )
        old = self.connection()
        record = {
            "id": channel["id"],
            "name": channel.get("name") or "",
            "displayName": channel.get("displayName") or "",
            "avatar": channel.get("avatar") or "",
            "organization_id": channel["organization_id"],
            "status": "Connected",
        }
        if channel.get("type"):
            record["type"] = channel["type"]
        replaced = {old["key"]} if old and old["key"] != key else set()
        with closing(self.library.connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO account_connection VALUES (?, ?, ?, ?)",
                (self.table, key, json.dumps(record), stamp(self.clock())),
            )
            # A fresh sign-in also serves the other Buffer platforms whose channels it can see:
            # one Buffer account, one sign-in.
            seen = self.visible.pop(key, set())
            marks = ",".join("?" * len(BUFFER_PLATFORMS))
            for row in db.execute(
                f"SELECT * FROM account_connection WHERE platform IN ({marks}) AND credential_key!=?",
                (*BUFFER_PLATFORMS, key),
            ).fetchall():
                other = json.loads(row["channel"])
                if str(other.get("id")) in seen:
                    other["status"] = "Connected"
                    db.execute(
                        "UPDATE account_connection SET credential_key=?, channel=? WHERE platform=?",
                        (key, json.dumps(other), row["platform"]),
                    )
                    replaced.add(row["credential_key"])
        for spent in replaced:
            self.forget_key(spent)

    def discard_key(self, key: str) -> None:
        self.forget_key(key)  # a reused sign-in stays while another platform needs it

    def disconnect(self) -> None:
        current = self.connection()
        with closing(self.library.connect()) as db, db:
            db.execute("DELETE FROM account_connection WHERE platform=?", (self.table,))
        if current:
            self.forget_key(current["key"])  # kept while another platform still uses it

    def hosting(self) -> dict[str, Any] | None:
        self.library.initialize()
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM media_hosting WHERE id=1").fetchone()
        return dict(row) if row else None

    def save_hosting(
        self,
        cloud_name: str,
        api_key: str,
        api_secret: str,
        *,
        consent: bool,
        retention: str = "delete_after_sent",
        max_bytes: int = FREE_PLAN_MAX_BYTES,
    ) -> dict[str, Any]:
        """Verify with Cloudinary (usage call, no upload) before anything is saved."""
        if not consent:
            raise HostingError(
                "Confirm that hosted copies are publicly reachable by link.", "consent"
            )
        if retention not in ("keep", "delete_after_sent"):
            raise HostingError("Choose a retention policy.", "config")
        old = self.hosting()
        if not api_secret and old:
            api_secret = self.secrets.get(old["credential_key"]) or ""
        host = CloudinaryHost(
            cloud_name, api_key, api_secret, self.host_session, anonymous=self.anonymous
        )
        usage = host.usage()  # wrong credentials fail here, before anything is stored
        key = old["credential_key"] if old else "cloudinary-" + uuid.uuid4().hex
        self.secrets.put(key, host.secret)
        when = stamp(self.clock())
        with closing(self.library.connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO media_hosting(id,provider,cloud_name,api_key,credential_key,"
                "max_bytes,retention,consent_at,updated_at) VALUES (1,'cloudinary',?,?,?,?,?,?,?)",
                (host.cloud, host.key, key, max_bytes, retention, when, when),
            )
        return usage

    def check_hosting(self, cloud_name: str, api_key: str, api_secret: str) -> dict[str, Any]:
        """Check entered (possibly unsaved) values; an empty secret means the saved one."""
        old = self.hosting()
        if not api_secret and old:
            api_secret = self.secrets.get(old["credential_key"]) or ""
        if not (cloud_name or api_key) and old:
            cloud_name, api_key = old["cloud_name"], old["api_key"]
        host = CloudinaryHost(
            cloud_name, api_key, api_secret, self.host_session, anonymous=self.anonymous
        )
        return host.usage()

    def remove_hosting(self) -> None:
        """Forget the main Cloudinary account; the first backup, if any, becomes the main one."""
        old = self.hosting()
        with closing(self.library.connect()) as db, db:
            backup = db.execute("SELECT * FROM media_hosts ORDER BY position LIMIT 1").fetchone()
            if backup and old:
                db.execute(
                    "UPDATE media_hosting SET cloud_name=?, api_key=?, credential_key=?, "
                    "full_until=?, updated_at=? WHERE id=1",
                    (
                        backup["cloud_name"],
                        backup["api_key"],
                        backup["credential_key"],
                        backup["full_until"],
                        stamp(self.clock()),
                    ),
                )
                db.execute(
                    "DELETE FROM media_hosts WHERE credential_key=?", (backup["credential_key"],)
                )
            else:
                db.execute("DELETE FROM media_hosting WHERE id=1")
        if old:
            self.secrets.delete(old["credential_key"])

    # Backup Cloudinary accounts: used in order once the main one is over its limits ----------

    def hosts(self) -> list[dict[str, Any]]:
        """Every Cloudinary account, main first: credential_key, cloud_name, api_key, full_until."""
        main = self.hosting()
        if not main:
            return []
        found = [
            {
                "credential_key": main["credential_key"],
                "cloud_name": main["cloud_name"],
                "api_key": main["api_key"],
                "full_until": main.get("full_until"),
                "main": True,
            }
        ]
        with closing(self.library.connect()) as db:
            for row in db.execute("SELECT * FROM media_hosts ORDER BY position"):
                found.append({**dict(row), "main": False})
        return found

    def add_host(self, cloud_name: str, api_key: str, api_secret: str) -> dict[str, Any]:
        """A backup account, verified with Cloudinary (usage call, no upload) before it is saved.
        The main account's consent, retention and size limit apply to it."""
        if not self.hosting():
            raise HostingError("Save the main Cloudinary account first.", "config")
        host = CloudinaryHost(
            cloud_name, api_key, api_secret, self.host_session, anonymous=self.anonymous
        )
        if any(h["cloud_name"] == host.cloud for h in self.hosts()):
            raise HostingError(
                f"The Cloudinary account {host.cloud} is already in the list. A backup must be "
                "a separate Cloudinary sign-up: another API key of the same account shares its "
                "limits.",
                "config",
            )
        usage = host.usage()
        key = "cloudinary-" + uuid.uuid4().hex
        self.secrets.put(key, host.secret)
        with closing(self.library.connect()) as db, db:
            position = db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM media_hosts")
            db.execute(
                "INSERT INTO media_hosts VALUES (?,?,?,?,NULL,?)",
                (key, host.cloud, host.key, position.fetchone()[0], stamp(self.clock())),
            )
        return usage

    def remove_host(self, credential_key: str) -> None:
        marks = ",".join("?" * len(UNSENT))
        with closing(self.library.connect()) as db:
            waiting = any(
                db.execute(
                    f"SELECT 1 FROM {p}_posts WHERE host_key=? AND state IN ({marks}) LIMIT 1",
                    (credential_key, *UNSENT),
                ).fetchone()
                for p in BUFFER_PLATFORMS
            )
        if waiting:
            raise HostingError(
                "A post waiting to be sent uses a copy in this Cloudinary account. Remove the "
                "account after it is sent or cancel the post.",
                "config",
            )
        with closing(self.library.connect()) as db, db:
            db.execute("DELETE FROM media_hosts WHERE credential_key=?", (credential_key,))
        self.secrets.delete(credential_key)

    def check_hosts(self) -> list[dict[str, Any]]:
        """Usage of every account (no upload). An account at 100 % is skipped for a day; one
        under its limits is used again at once."""
        found = []
        for entry in self.hosts():
            try:
                usage = self.host(entry["credential_key"]).usage()
            except HostingError as exc:
                found.append({**entry, "error": str(exc)})
                continue
            full = float(usage.get("used_percent") or 0) >= 100
            self.mark_full(entry["credential_key"], full)
            found.append({**entry, "usage": usage, "full": full})
        return found

    def mark_full(self, credential_key: str, full: bool = True) -> None:
        until = stamp(self.clock() + FULL_PAUSE) if full else None
        with closing(self.library.connect()) as db, db:
            db.execute(
                "UPDATE media_hosting SET full_until=? WHERE credential_key=?",
                (until, credential_key),
            )
            db.execute(
                "UPDATE media_hosts SET full_until=? WHERE credential_key=?",
                (until, credential_key),
            )

    def host(self, credential_key: str = "") -> CloudinaryHost:
        """The main Cloudinary account, or the one holding a given copy."""
        hosts = self.hosts()
        if not hosts:
            raise HostingError("Set up video hosting in Settings first.", "config")
        config = next((h for h in hosts if h["credential_key"] == credential_key), None)
        if credential_key and not config:
            raise HostingError("That Cloudinary account was removed from Settings.", "config")
        config = config or hosts[0]
        secret = self.secrets.get(config["credential_key"]) or ""
        return CloudinaryHost(
            config["cloud_name"],
            config["api_key"],
            secret,
            self.host_session,
            lambda: self.clock().timestamp(),
            self.anonymous,
        )

    def upload_order(self, preferred: str) -> list[str]:
        """Accounts not over their limits, the one already holding this copy first."""
        now = self.clock()
        keys = [
            h["credential_key"]
            for h in self.hosts()
            if not h["full_until"] or parse(h["full_until"]) <= now
        ]
        return sorted(keys, key=lambda k: k != preferred)

    # Drafts ----------------------------------------------------------------------------------

    def draft(self, video_id: int) -> dict[str, Any]:
        self.library.initialize()
        with closing(self.library.connect()) as db:
            row = db.execute(
                f"SELECT * FROM {self.table}_drafts WHERE video_id=?", (video_id,)
            ).fetchone()
        if not row:
            return {"caption": "", "ai_generated": None}
        return {
            "caption": row["caption"],
            "ai_generated": None if row["ai_generated"] is None else bool(row["ai_generated"]),
        }

    def save_draft(self, video_id: int, caption: str, ai_generated: bool | None) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute(
                f"INSERT OR REPLACE INTO {self.table}_drafts VALUES (?,?,?,?)",
                (video_id, caption, ai_generated, stamp(self.clock())),
            )

    # Posts -----------------------------------------------------------------------------------

    def posts(self, video_id: int | None = None) -> list[dict[str, Any]]:
        self.library.initialize()
        with closing(self.library.connect()) as db:
            rows = db.execute(
                f"SELECT * FROM {self.table}_posts"
                + (" WHERE video_id=?" if video_id is not None else "")
                + " ORDER BY created_at DESC",
                (video_id,) if video_id is not None else (),
            ).fetchall()
        return [dict(r) for r in rows]

    def update(self, db: sqlite3.Connection, post_id: str, **values: Any) -> None:
        values["updated_at"] = stamp(self.clock())
        db.execute(
            f"UPDATE {self.table}_posts SET {','.join(f'{k}=?' for k in values)} WHERE id=?",
            (*values.values(), post_id),
        )

    def write(self, post_id: str, **values: Any) -> None:
        with closing(self.library.connect()) as db, db:
            self.update(db, post_id, **values)

    def get(self, post_id: str) -> dict[str, Any]:
        with closing(self.library.connect()) as db:
            row = db.execute(f"SELECT * FROM {self.table}_posts WHERE id=?", (post_id,)).fetchone()
        if not row:
            raise SocialError(f"That {self.spec.name} post record no longer exists.")
        return dict(row)

    def publish(
        self,
        video_id: int,
        caption: str,
        ai_generated: bool | None,
        due: datetime | None,
        cancel: Event,
    ) -> str:
        """Manual Publish Now (due=None) or exact-time Schedule; both explicit user actions."""
        if due is not None and due <= self.clock() + timedelta(minutes=2):
            raise SocialError("Choose a time at least two minutes from now.")
        try:
            with FileLock(str(self.lock), timeout=60):
                post_id = self.reserve(video_id, caption, ai_generated, due, "manual", None, cancel)
                self.host_and_submit(post_id, cancel)
                return post_id
        except Timeout:
            raise SocialError(
                f"Another {self.spec.name} operation is running. Try again shortly."
            ) from None

    def reserve(
        self,
        video_id: int,
        caption: str,
        ai_generated: bool | None,
        due: datetime | None,
        origin: str,
        profile_id: str | None,
        cancel: Event,
    ) -> str:
        name = self.spec.name
        connection = self.connection()
        if not connection:
            raise SocialError(f"Connect {name} via Buffer in Accounts first.")
        if connection.get("status") != "Connected":
            raise SocialError(f"Reconnect {name} in Accounts before publishing.")
        hosting = self.hosting()
        if not hosting:
            raise SocialError(f"Set up video hosting in Settings before posting to {name}.")
        if ai_generated is None:
            if self.spec.ai_label:
                raise SocialError("Choose whether the video contains AI-generated content.")
            ai_generated = False  # this platform's API has no AI label; nothing is sent
        video = self.library.get(video_id)
        errors = caption_errors(self.spec, caption) + media_errors(
            self.spec, video, hosting["max_bytes"]
        )
        if errors:
            raise SocialError(" ".join(errors))
        try:
            with open(video.path, "rb") as file:
                sha = fingerprint(file, cancel)
        except OSError as exc:
            raise SocialError(f"Cannot read the original video: {exc.strerror}") from exc
        post_id, when = uuid.uuid4().hex, stamp(self.clock())
        label = self.destination(connection) or name
        with closing(self.library.connect()) as db:
            # The same account reached through another Buffer account has another channel ID.
            twin = db.execute(
                f"SELECT state FROM {self.table}_posts WHERE source_sha256=? AND released=0 "
                "AND channel_id!=? AND channel_name=?",
                (sha, connection["id"], label),
            ).fetchone()
        if twin:
            raise SocialError(
                f"This exact video already has a {name} post for this {self.spec.account} "
                f"({twin[0]}). Duplicate posts are blocked; see its {name} history."
            )
        try:
            with closing(self.library.connect()) as db, db:
                db.execute(
                    f"""INSERT INTO {self.table}_posts(id,source_sha256,video_id,source_path,channel_id,channel_name,origin,
                    profile_id,caption,ai_generated,mode,due_at,state,hosted_id,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'Uploading to media host',?,?,?)""",
                    (
                        post_id,
                        sha,
                        video.id,
                        video.path,
                        connection["id"],
                        label,
                        origin,
                        profile_id,
                        caption,
                        int(ai_generated),
                        "shareNow" if due is None else "customScheduled",
                        stamp(due) if due else None,
                        CloudinaryHost.new_id(),
                        when,
                        when,
                    ),
                )
        except sqlite3.IntegrityError:
            existing = next(
                (
                    p
                    for p in self.posts()
                    if p["source_sha256"] == sha
                    and p["channel_id"] == connection["id"]
                    and not p["released"]
                ),
                None,
            )
            raise SocialError(
                f"This exact video already has a {name} post for this {self.spec.account}"
                + (f" ({existing['state']})" if existing else "")
                + f". Duplicate posts are blocked; see its {name} history."
            ) from None
        return post_id

    def host_and_submit(self, post_id: str, cancel: Event) -> None:
        post = self.get(post_id)
        if post["state"] == "Uploading to media host":
            hosting = self.hosting()
            if not hosting:
                raise SocialError("Set up video hosting in Settings first.")
            hosted = None
            for key in self.upload_order(post["host_key"]):
                # Recorded first, so an interrupted copy can be found and removed.
                self.write(post_id, host_key=key)
                host = self.host(key)
                try:
                    hosted = host.upload(
                        Path(post["source_path"]),
                        post["source_sha256"],
                        hosting["max_bytes"],
                        cancel,
                        post["hosted_id"],
                    )
                    break
                except HostingError as exc:
                    if exc.kind == "quota":
                        # Refused before anything was stored: the next account may take it.
                        self.mark_full(key)
                        continue
                    if exc.kind == "uncertain" or (
                        post["profile_id"] and exc.kind not in ("source", "size")
                    ):
                        # Nothing reached Buffer, and the same public_id with overwrite=false
                        # makes another upload safe: stay pending and try again later, like a
                        # YouTube schedule (a queue or auto upload moves to its next time).
                        self.write(post_id, error=str(exc) + RETRY_NOTE)
                        raise SocialError(str(exc)) from exc
                    self.write(post_id, state="Hosting failed", error=str(exc))
                    raise SocialError(str(exc)) from exc
            if hosted is None:
                message = (
                    "Every Cloudinary account is over its plan's limits. Add another account in "
                    "Settings → Video hosting, or wait for the limits to reset."
                )
                if post["profile_id"]:
                    self.write(post_id, error=message + RETRY_NOTE)
                    raise SocialError(message)
                self.write(post_id, state="Hosting failed", error=message)
                raise SocialError(message)
            self.write(post_id, hosted_url=hosted.url, state="Hosted and ready", error="")
            try:
                host.verify(hosted.url, hosted.size)
            except HostingError as exc:
                self.write(post_id, error=str(exc) + " Retrying before Buffer is contacted.")
                raise SocialError(str(exc)) from exc
        self.submit(post_id)

    @staticmethod
    def destination(connection: dict[str, Any]) -> str:
        """The connected account's name, as posts and automations record it."""
        return str(connection.get("displayName") or connection.get("name") or "")

    def same_destination(self, channel_id: str, name: str, connection: dict[str, Any]) -> bool:
        """The same account, even when reached through another Buffer account (whose channel ID
        for it differs)."""
        return channel_id == connection["id"] or bool(name and name == self.destination(connection))

    def retarget(self, post: dict[str, Any], connection: dict[str, Any]) -> bool:
        """Before anything was sent: follow the account into the Buffer account it is connected
        through now. A different account is never posted to instead."""
        if post["channel_id"] == connection["id"]:
            return True
        if not self.same_destination(post["channel_id"], post["channel_name"], connection):
            self.write(
                post["id"],
                state="Failed",
                error=f"{self.spec.name} is now connected to a different {self.spec.account} "
                f"than {post['channel_name']}. Nothing was posted.",
            )
            return False
        try:
            self.write(post["id"], channel_id=connection["id"])
        except sqlite3.IntegrityError:
            self.write(
                post["id"],
                state="Failed",
                error="This video already has a post for this account. Nothing was posted.",
            )
            return False
        return True

    def post_api(self, post: dict[str, Any]) -> BufferAPI:
        """The Buffer API through the sign-in a post was sent with, even after a switch."""
        current = self.connection()
        key = post["buffer_key"]
        if not key or (current and current["key"] == key):
            return self.api()
        return self.api_for(key)

    def submit(self, post_id: str) -> None:
        post = self.get(post_id)
        if post["state"] not in (
            "Hosted and ready",
            "Waiting for Buffer limit",
            self.spec.reconnect_state,
        ):
            return
        connection = self.connection()
        if connection and not self.retarget(post, connection):
            return
        if post["mode"] == "customScheduled" and parse(post["due_at"]) <= self.clock() + timedelta(
            seconds=60
        ):
            # Never post late in a burst: the approved time passed before Buffer accepted it.
            self.write(
                post_id,
                state="Missed — not sent",
                released=1,
                error="The approved time passed before the post reached Buffer. Nothing was posted.",
            )
            return
        assert connection
        post = self.get(post_id)  # retargeting may have changed the channel
        payload: dict[str, Any] = {
            "text": post["caption"],
            "schedulingType": "automatic",
            "mode": post["mode"],
            "needsApproval": False,
            "assets": [{"video": {"url": post["hosted_url"]}}],
            "metadata": self.spec.metadata(bool(post["ai_generated"])),
            "source": "ShortsPublisher",
        }
        if post["mode"] == "customScheduled":
            payload["dueAt"] = post["due_at"].replace("+00:00", "Z")
        resets, refused = [], False
        for route in self.routes(post, connection):
            retry = self.retry_after(route["account"])
            if retry and parse(retry) > self.clock():
                resets.append(retry)
                continue
            if not route["channel"]:
                try:
                    found = self.find_channel(route["key"], post["channel_name"])
                except BufferError as exc:
                    if exc.kind == "rate":
                        self.set_retry(route["account"], exc.retry_after)
                    continue  # this backup can't be used now; try the next one
                if not found:
                    continue
                route["channel"], route["org"] = found
            if self.send(post_id, payload, route):
                return
            refused = True
            resets.append(self.retry_after(route["account"]) or "")
        soonest = min((r for r in resets if r), default="")
        message = (
            "Every Buffer account has reached its limit"
            + (f"; the first resets {soonest}" if soonest else "")
            + ". Add another Buffer account in Settings to keep posting sooner."
        )
        self.write(post_id, state="Waiting for Buffer limit", error=message)
        if refused:
            raise SocialError(message)

    def routes(self, post: dict[str, Any], connection: dict[str, Any]) -> list[dict[str, Any]]:
        """Where a post can be sent: the platform's connection, then every other Buffer account
        signed in on this Mac (its channel for the same account is looked up only when needed)."""
        own = self.account_of(connection["key"])
        found = [
            {
                "account": own,
                "key": connection["key"],
                "org": connection["organization_id"],
                "channel": post["channel_id"],
                "own": True,
            }
        ]
        for account in self.buffer_accounts():
            if account["id"] != own and account["key"]:
                found.append(
                    {"account": account["id"], "key": account["key"], "channel": "", "own": False}
                )
        return found

    def find_channel(self, key: str, name: str) -> tuple[str, str] | None:
        """This platform's channel for the same account in another Buffer account."""
        for channel in usable_channels(self.spec, self.api_for(key).channels(self.spec.key)):
            if name and self.destination(channel) == name:
                return str(channel["id"]), str(channel["organization_id"])
        return None

    def send(self, post_id: str, payload: dict[str, Any], route: dict[str, Any]) -> bool:
        """createPost through one Buffer account. False: that account refused it over a limit
        (definitely not created), so the next account may take it."""
        try:
            self.write(post_id, channel_id=route["channel"])
        except sqlite3.IntegrityError:
            message = "This video already has a post for this account. Nothing was posted."
            self.write(post_id, state="Failed", error=message)
            raise SocialError(message) from None
        # Durable intent before the network call: a crash here becomes reconciliation, not a resend.
        self.write(
            post_id,
            state="Sending to Buffer",
            submitted_at=stamp(self.clock()),
            error="",
            buffer_key=route["key"],
            buffer_org=route["org"],
        )
        api = self.api() if route["own"] else self.api_for(route["key"])
        try:
            result = api.create_post({**payload, "channelId": route["channel"]})
        except BufferError as exc:
            if exc.kind == "rate" or (exc.kind == "api" and over_plan(str(exc))):
                # Rejected before processing (429) or refused outright: nothing was created.
                self.set_retry(route["account"], exc.retry_after or QUEUE_PAUSE)
                self.write(post_id, state="Waiting for Buffer limit", error=str(exc))
                return False
            if not route["own"] and exc.kind in ("auth", "reconnect"):
                self.write(post_id, state="Hosted and ready", error=str(exc))
                return False  # a backup that needs signing in again is skipped
            if exc.kind in ("auth", "reconnect"):
                if exc.kind == "reconnect":
                    self.set_connection_status("Reconnect required")
                self.write(post_id, state=self.spec.reconnect_state, error=str(exc))
            elif exc.kind == "uncertain":
                self.write(
                    post_id,
                    state="Needs reconciliation",
                    error=str(exc),
                    next_check_at=stamp(self.clock() + timedelta(minutes=2)),
                )
            else:
                self.write(post_id, state="Failed", error=str(exc))
            raise SocialError(str(exc)) from exc
        self.apply(post_id, result)
        return True

    def apply(self, post_id: str, remote: dict[str, Any]) -> None:
        status = str(remote.get("status", ""))
        kind = str(remote.get("schedulingType") or "")
        if kind and kind != "automatic":
            state = "Manual completion required"
        else:
            state = {
                "scheduled": "Scheduled in Buffer",
                "sending": "Publishing",
                "sent": "Sent by Buffer",
                "error": "Failed",
            }.get(status, "Needs attention")
        error = ""
        if state == "Failed":
            error = str((remote.get("error") or {}).get("message") or "Buffer reported an error.")
        elif state == "Manual completion required":
            error = (
                f"Buffer will ask for this post to be finished in the {self.spec.name} app; "
                "it is not automatic."
            )
        elif state == "Needs attention":
            error = f"Buffer reported an unexpected status: {status or 'unknown'}."
        post = self.get(post_id)
        values: dict[str, Any] = {
            "buffer_post_id": str(remote.get("id") or post["buffer_post_id"]),
            "buffer_status": status,
            "scheduling_type": kind,
            "state": state,
            "error": error,
            "external_link": str(remote.get("externalLink") or ""),
            "sent_at": remote.get("sentAt") or post["sent_at"],
        }
        values["next_check_at"] = self.next_check(post, state) if state in UNSENT else None
        self.write(post_id, **values)

    def next_check(self, post: dict[str, Any], state: str) -> str:
        now = self.clock()
        due = parse(post["due_at"]) if post["due_at"] else parse(post["submitted_at"] or stamp(now))
        if state == "Scheduled in Buffer" and due > now:
            # Buffer usually finishes within two minutes of the due time, so one check normally
            # suffices; that keeps 15 posts a day per platform inside Buffer Free's API allowance.
            return stamp(due + timedelta(minutes=3))
        checks = post["checks"]
        delay = timedelta(minutes=CHECK_BACKOFF[checks] if checks < len(CHECK_BACKOFF) else 60)
        return stamp(now + delay)

    def refresh(self, cancel: Event) -> None:
        now = self.clock()
        connection = self.connection()
        for post in self.posts():
            if cancel.is_set() or not connection:
                return
            if post["state"] == "Sending to Buffer" and parse(post["updated_at"]) < now - timedelta(
                minutes=2
            ):
                self.write(
                    post["id"],
                    state="Needs reconciliation",
                    error="ShortsPublisher stopped while sending; checking Buffer before any retry.",
                    next_check_at=stamp(now),
                )
                post = self.get(post["id"])
            if (
                post["state"] not in UNSENT
                or not post["next_check_at"]
                or parse(post["next_check_at"]) > now
            ):
                continue
            due = parse(post["due_at"] or post["submitted_at"] or post["created_at"])
            sent_with = post["buffer_key"] or connection["key"]
            try:
                if post["state"] == "Needs reconciliation":
                    self.reconcile_post(post, connection)
                elif post["buffer_post_id"]:
                    remote = self.post_api(post).post(post["buffer_post_id"])
                    with closing(self.library.connect()) as db, db:
                        self.update(db, post["id"], checks=post["checks"] + 1)
                    if remote is None:
                        self.write(
                            post["id"],
                            state="Removed in Buffer",
                            next_check_at=None,
                            error="Buffer no longer has this post; it may have been deleted there.",
                        )
                    else:
                        self.apply(post["id"], remote)
                post = self.get(post["id"])
                if post["state"] in UNSENT and now > due + timedelta(hours=GIVE_UP_HOURS):
                    self.write(
                        post["id"],
                        state="Needs attention",
                        next_check_at=None,
                        error="Buffer has not reported a result for two days. Check the post in Buffer.",
                    )
                if post["state"] not in UNSENT and sent_with != connection["key"]:
                    self.forget_key(sent_with)  # an earlier sign-in, once nothing waits on it
            except BufferError as exc:
                if exc.kind == "rate":
                    self.set_retry(self.account_of(sent_with), exc.retry_after)
                    return
                if sent_with != connection["key"] and exc.kind in ("auth", "reconnect"):
                    # Sent through a Buffer sign-in this platform no longer uses.
                    self.write(
                        post["id"],
                        state="Needs attention",
                        next_check_at=None,
                        error="The Buffer account this was sent through no longer accepts the "
                        "sign-in. Check the post in that Buffer account.",
                    )
                    continue
                if exc.kind == "reconnect":
                    self.set_connection_status("Reconnect required")
                    return
                self.write(
                    post["id"],
                    error=f"Status check failed: {exc}",
                    next_check_at=stamp(now + timedelta(minutes=15)),
                )

    def reconcile_post(self, post: dict[str, Any], connection: dict[str, Any]) -> None:
        """An uncertain createPost is resolved by finding our unique hosted URL in Buffer."""
        matches = [
            p
            for p in self.post_api(post).recent_posts(
                post["buffer_org"] or connection["organization_id"], post["channel_id"]
            )
            if any(
                post["hosted_url"] and post["hosted_url"] == (a or {}).get("source")
                for a in p.get("assets") or []
            )
        ]
        if len(matches) == 1:
            self.apply(post["id"], matches[0])
        else:
            self.write(
                post["id"],
                checks=post["checks"] + 1,
                next_check_at=stamp(self.clock() + timedelta(minutes=15)),
                error=(
                    "Buffer shows more than one matching post; check Buffer before taking action."
                    if matches
                    else "No matching post found in Buffer yet. If it never appears, choose Allow Another Attempt."
                ),
            )

    def cancel(self, post_id: str) -> None:
        post = self.get(post_id)
        if post["state"] not in (
            "Scheduled in Buffer",
            "Hosted and ready",
            "Waiting for Buffer limit",
            self.spec.reconnect_state,
            "Hosting failed",
        ):
            raise SocialError("Only posts that have not started publishing can be cancelled.")
        if post["buffer_post_id"]:
            api = self.post_api(post)
            remote = api.post(post["buffer_post_id"])
            if remote and remote.get("status") not in ("scheduled", "draft"):
                self.apply(post_id, remote)
                raise SocialError(
                    "Buffer has already started this post; it can no longer be cancelled."
                )
            api.delete_post(post["buffer_post_id"])
        self.write(post_id, state="Cancelled", released=1, next_check_at=None, error="")
        self.remove_hosted(post_id)

    def release(self, post_id: str) -> None:
        """Explicit acknowledgement that a failed/uncertain post may be attempted again."""
        post = self.get(post_id)
        if post["state"] not in (
            "Failed",
            "Needs reconciliation",
            "Needs attention",
            "Hosting failed",
            "Removed in Buffer",
            "Manual completion required",
        ):
            raise SocialError("This post does not need a new attempt.")
        self.write(post_id, released=1, next_check_at=None)

    def remove_hosted(self, post_id: str) -> None:
        post = self.get(post_id)
        if not post["hosted_id"] or post["hosted_deleted_at"]:
            return
        try:
            self.host(post["host_key"]).destroy(post["hosted_id"])
            self.write(post_id, hosted_deleted_at=stamp(self.clock()))
        except HostingError as exc:
            self.write(post_id, error=f"Hosted copy not yet removed: {exc}")

    def cleanup(self) -> None:
        hosting = self.hosting()
        if not hosting or hosting["retention"] != "delete_after_sent":
            return
        for post in self.posts():
            # Only after Buffer reported the post sent, plus a grace period; never on acceptance.
            if (
                post["state"] == "Sent by Buffer"
                and post["sent_at"]
                and not post["hosted_deleted_at"]
                and parse(post["sent_at"]) + RETENTION_GRACE <= self.clock()
            ):
                self.remove_hosted(post["id"])

    # Automation ------------------------------------------------------------------------------

    def profiles(self) -> list[dict[str, Any]]:
        self.library.initialize()
        with closing(self.library.connect()) as db:
            rows = db.execute(
                f"SELECT * FROM {self.table}_profiles WHERE removed=0 ORDER BY created_at"
            ).fetchall()
        return [{**dict(r), "settings": json.loads(r["settings"])} for r in rows]

    def decisions(self, profile_id: str) -> list[dict[str, Any]]:
        with closing(self.library.connect()) as db:
            rows = db.execute(
                f"SELECT * FROM {self.table}_decisions WHERE profile_id=? ORDER BY updated_at DESC",
                (profile_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def preview(self, values: dict[str, Any]) -> dict[str, Any]:
        name = self.spec.name
        connection = self.connection()
        if not connection or connection.get("status") != "Connected":
            raise SocialError(f"Connect {name} via Buffer in Accounts first.")
        if not self.hosting():
            raise SocialError(f"Set up video hosting in Settings before posting to {name}.")
        with closing(self.library.connect()) as db:
            folder = db.execute(
                "SELECT * FROM watch_folders WHERE id=? AND removed=0", (values.get("folder_id"),)
            ).fetchone()
        if not folder:
            raise SocialError("Choose an authorized watched folder.")
        settings = dict(values["settings"])
        if not self.spec.ai_label:
            settings["ai_generated"] = False  # nothing is sent; kept for one record shape
        elif settings.get("ai_generated") is None:
            raise SocialError("Choose whether these videos contain AI-generated content.")
        if not 1 <= int(settings.get("daily_cap", 0)) <= self.spec.daily_max:
            raise SocialError(
                f"{name} accepts at most {self.spec.daily_max} posts a day through Buffer."
            )
        template = settings.get("caption_template", "")
        if set(re.findall(r"\{[^}]*\}", template)) - {"{filename}"}:
            raise SocialError("The caption template supports only {filename}.")
        errors = caption_errors(self.spec, caption_for(template, "x" * 40 + ".mp4"))
        if errors:
            raise SocialError(" ".join(errors))
        rule = Rule.decode(settings["rule"])
        opportunities = [stamp(t) for t in rule.next(self.clock(), 5)]
        title = values.get("name", "").strip() or f"{name} automation"
        account = connection.get("displayName") or connection.get("name")
        summary = (
            f"{title}\nFolder: {folder['path']}\n{name} {self.spec.account}: "
            f"{account} (via Buffer)\nVisibility: PUBLIC "
            "(Buffer posts publicly; privacy is not selectable through Buffer's API)\n"
            f"Caption: {template}\n"
            + (
                f"AI-generated content disclosed: {'Yes' if settings['ai_generated'] else 'No'}\n"
                if self.spec.ai_label
                else ""
            )
            + f"At most {settings['daily_cap']} posts per local day ({name}'s own limit) · eligible filenames: {settings.get('pattern') or '*'}\n"
            f"Background service allowed: {'Yes' if settings.get('background') else 'No'}\n"
            "Next opportunities:\n" + "\n".join(opportunities)
        )
        return {
            "id": values.get("id") or "",
            "name": title,
            "folder_id": folder["id"],
            "folder_path": folder["path"],
            "channel_id": connection["id"],
            "channel_name": account or name,
            "settings": {**settings, "pattern": settings.get("pattern") or "*"},
            "summary": summary,
            "opportunities": opportunities,
        }

    def approve(self, proposal: dict[str, Any], scanner: WatchService, cancel: Event) -> str:
        folder = scanner.preview(proposal["folder_path"], cancel)
        when = stamp(self.clock())
        identity = proposal["id"] or uuid.uuid4().hex
        consent = json.dumps(
            {
                "text": self.spec.automation_consent,
                "hosting": HOSTING_CONSENT,
                "summary": proposal["summary"],
            }
        )
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                f"SELECT revision,folder_id FROM {self.table}_profiles WHERE id=?", (identity,)
            ).fetchone()
            revision = (old[0] if old else 0) + 1
            # An edit applies to the videos it was still waiting to send; anything already
            # on its way to Buffer, or in it, keeps the settings it was sent with.
            waiting = (
                {
                    r[0]
                    for r in db.execute(
                        f"""SELECT name FROM {self.table}_decisions WHERE profile_id=? AND
                        profile_revision=? AND state IN ('Waiting for next opportunity','Needs review')""",
                        (identity, old[0]),
                    )
                }
                if old and old[1] == proposal["folder_id"]
                else set()
            )
            db.execute(
                f"""INSERT INTO {self.table}_profiles(id,revision,name,folder_id,channel_id,channel_name,settings,consent,enabled,revoked,removed,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,1,0,0,'',?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,name=excluded.name,
                folder_id=excluded.folder_id,channel_id=excluded.channel_id,channel_name=excluded.channel_name,settings=excluded.settings,
                consent=excluded.consent,enabled=1,revoked=0,removed=0,status='',updated_at=excluded.updated_at""",
                (
                    identity,
                    revision,
                    proposal["name"],
                    proposal["folder_id"],
                    proposal["channel_id"],
                    proposal["channel_name"],
                    json.dumps(proposal["settings"]),
                    consent,
                    when,
                    when,
                ),
            )
            for name, observed in folder["candidates"].items():
                if name in waiting:
                    continue
                db.execute(
                    f"""INSERT OR IGNORE INTO {self.table}_decisions(profile_id,profile_revision,name,state,reason,source_sha256,created_at,updated_at)
                    VALUES(?,?,?,'Excluded existing','Present at authorization; future files only',?,?,?)""",
                    (identity, revision, name, observed, when, when),  # already JSON
                )
        return identity

    def control(self, identity: str, action: str) -> None:
        column = {
            "pause": "enabled=0",
            "resume": "enabled=1",
            "revoke": "enabled=0,revoked=1",
            "remove": "enabled=0,removed=1",
        }[action]
        with closing(self.library.connect()) as db, db:
            row = db.execute(
                f"SELECT revoked FROM {self.table}_profiles WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                raise SocialError(f"That {self.spec.name} automation no longer exists.")
            if action == "resume" and row[0]:
                raise SocialError("Revoked automations need renewed approval.")
            db.execute(
                f"UPDATE {self.table}_profiles SET {column},updated_at=? WHERE id=?",
                (stamp(self.clock()), identity),
            )

    def decide(
        self,
        p: dict[str, Any],
        name: str,
        state: str,
        reason: str,
        sha: str = "",
        video_id: int | None = None,
        post_id: str | None = None,
    ) -> None:
        when = stamp(self.clock())
        table = f"{self.table}_decisions"
        with closing(self.library.connect()) as db, db:
            db.execute(
                f"""INSERT INTO {table}(profile_id,profile_revision,name,source_sha256,video_id,state,reason,post_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(profile_id,profile_revision,name) DO UPDATE SET
                source_sha256=excluded.source_sha256,video_id=excluded.video_id,state=excluded.state,reason=excluded.reason,
                post_id=COALESCE(excluded.post_id,{table}.post_id),updated_at=excluded.updated_at
                WHERE {table}.state!='Excluded existing'""",
                (p["id"], p["revision"], name, sha, video_id, state, reason, post_id, when, when),
            )

    def reconcile(self, scanner: WatchService, cancel: Event, *, background: bool = False) -> None:
        """Runs after each folder scan, in the desktop or background worker. Never raises."""
        try:
            with FileLock(str(self.lock), timeout=0):
                self.library.initialize()
                for p in self.profiles():
                    if cancel.is_set():
                        return
                    if (
                        p["enabled"]
                        and not p["revoked"]
                        and (not background or p["settings"].get("background"))
                    ):
                        try:
                            self.enroll(p, scanner, background)
                            self.advance(p, cancel)
                        except (
                            SocialError,
                            BufferError,
                            HostingError,
                            ScheduleError,
                            LibraryError,
                            OSError,
                        ) as exc:
                            self.status(p["id"], str(exc))
                if self.connection() and self.hosting():
                    self.advance_queues(cancel, background=background)
                if self.connection():
                    self.refresh(cancel)
                    self.cleanup()
        except Timeout:
            return
        except Exception as exc:  # The local scanner must keep working whatever Buffer does.
            with closing(self.library.connect()) as db, db:
                db.execute(
                    f"UPDATE {self.table}_profiles SET status=? WHERE enabled=1",
                    (f"Needs attention: {exc}"[:300],),
                )

    def status(self, identity: str, text: str) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute(
                f"UPDATE {self.table}_profiles SET status=? WHERE id=?", (text[:300], identity)
            )

    def enroll(self, p: dict[str, Any], scanner: WatchService, background: bool) -> None:
        with closing(self.library.connect()) as db:
            folder = db.execute(
                "SELECT * FROM watch_folders WHERE id=?", (p["folder_id"],)
            ).fetchone()
        if (
            not folder
            or folder["removed"]
            or not folder["enabled"]
            or (background and not folder["background"])
        ):
            raise SocialError(
                "The watched folder is paused, removed or not allowed in the background."
            )
        known = {
            d["name"]: d for d in self.decisions(p["id"]) if d["profile_revision"] == p["revision"]
        }
        for file in scanner.files(p["folder_id"]):
            old = known.get(file["name"])
            if old and old["state"] == "Excluded existing":
                # Conservative: a later file reusing an excluded name is never posted automatically.
                continue
            if old and old["state"] != "Needs review":
                continue
            if file["state"] in (
                "Waiting for file to finish",
                "Ready for inspection",
                "Importing",
                "Ignored existing file",
            ):
                continue
            if file["state"] != "Imported successfully" or not file["video_id"]:
                self.decide(
                    p, file["name"], "Skipped", file["message"] or file["state"], file["sha256"]
                )
                continue
            if not fnmatch.fnmatchcase(file["name"], p["settings"]["pattern"]):
                self.decide(
                    p,
                    file["name"],
                    "Not eligible",
                    "Filename does not match the approved pattern.",
                    file["sha256"],
                    file["video_id"],
                )
                continue
            hosting = self.hosting()
            video = self.library.get(file["video_id"])
            caption = caption_for(p["settings"]["caption_template"], file["name"])
            errors = caption_errors(self.spec, caption) + media_errors(
                self.spec, video, hosting["max_bytes"] if hosting else self.spec.max_bytes
            )
            with closing(self.library.connect()) as db:
                dup = db.execute(
                    f"SELECT state FROM {self.table}_posts WHERE source_sha256=? AND released=0 "
                    "AND (channel_id=? OR channel_name=?)",
                    (file["sha256"], p["channel_id"], p["channel_name"]),
                ).fetchone()
            if dup:
                self.decide(
                    p,
                    file["name"],
                    "Duplicate — skipped",
                    f"Already posted or pending for this {self.spec.name} {self.spec.account} ({dup[0]}).",
                    file["sha256"],
                    file["video_id"],
                )
            elif errors:
                self.decide(
                    p,
                    file["name"],
                    "Needs review",
                    " ".join(errors),
                    file["sha256"],
                    file["video_id"],
                )
            else:
                self.decide(
                    p,
                    file["name"],
                    "Waiting for next opportunity",
                    "",
                    file["sha256"],
                    file["video_id"],
                )

    def retry_pending(
        self, post: Any, rule: Rule, used: list[str], cap: int, cancel: Event
    ) -> None:
        """A video that could not be copied to the host waits a while, then tries again; once
        its time has passed it takes the next free upload time instead of being dropped."""
        if post["state"] == "Uploading to media host" and post["error"]:
            now = self.clock()
            if parse(post["updated_at"]) > now - HOSTING_RETRY:
                return
            if post["due_at"] and parse(post["due_at"]) < now + timedelta(minutes=5):
                slot = self.next_slot(rule, [d for d in used if d != post["due_at"]], cap)
                if slot is None:
                    return
                self.write(post["id"], due_at=stamp(slot))
        self.host_and_submit(post["id"], cancel)

    def next_slot(self, rule: Rule, used: list[str], cap: int) -> datetime | None:
        """The first posting time after the last one used (and at least five minutes away) on
        a day that still has room under `cap`."""
        zone = ZoneInfo(rule.zone)
        counts: dict[Any, int] = {}
        for due in used:
            day = parse(due).astimezone(zone).date()
            counts[day] = counts.get(day, 0) + 1
        latest = max((parse(d) for d in used), default=self.clock())
        after = max(self.clock() + timedelta(minutes=5), latest)
        return next(
            (t for t in rule.next(after, 32) if counts.get(t.astimezone(zone).date(), 0) < cap),
            None,
        )

    def advance_queues(self, cancel: Event, *, background: bool = False) -> None:
        """Send each queue's next waiting video to Buffer once the one before it has posted.

        One at a time, so a change to the queue or to a video's details still applies to every
        video not sent yet. Held while automatic publishing is paused for the whole app."""
        with closing(self.library.connect()) as db:
            paused = db.execute("SELECT paused FROM publishing_control WHERE id=1").fetchone()
            queues = [
                decode_queue(r)
                for r in db.execute("SELECT * FROM queues WHERE enabled=1 AND removed=0")
            ]
        for queue in queues:
            if self.spec.key not in queue["platforms"] or (background and not queue["background"]):
                continue
            if cancel.is_set():
                return
            try:
                self.advance_queue(queue, bool(paused and paused[0]), cancel)
            except (SocialError, BufferError, HostingError, LibraryError, OSError):
                continue  # recorded on the post or item; tried again on the next pass

    def advance_queue(self, queue: dict[str, Any], paused: bool, cancel: Event) -> None:
        with closing(self.library.connect()) as db:
            pending = db.execute(
                f"SELECT * FROM {self.table}_posts WHERE profile_id=? AND released=0 AND state IN ({','.join('?' * len(UNSENT))})",
                (queue["id"], *UNSENT),
            ).fetchone()
            used = [
                r[0]
                for r in db.execute(
                    f"SELECT due_at FROM {self.table}_posts WHERE profile_id=? AND due_at IS NOT NULL AND state NOT IN ('Cancelled','Missed — not sent')",
                    (queue["id"],),
                )
            ]
            items = db.execute(
                "SELECT * FROM queue_items WHERE queue_id=? AND platform=? AND state='Waiting' "
                "ORDER BY position,id",
                (queue["id"], self.spec.key),
            ).fetchall()
        if pending:
            if pending["state"] in (
                "Uploading to media host",
                "Hosted and ready",
                "Waiting for Buffer limit",
                self.spec.reconnect_state,
            ):
                self.retry_pending(
                    pending, Rule.decode(queue["rule"]), used, self.spec.daily_max, cancel
                )
            return  # the next video waits until this one has posted
        if paused or not items:
            return
        slot = self.next_slot(Rule.decode(queue["rule"]), used, self.spec.daily_max)
        if slot is None:
            return
        for item in items:
            try:
                video = self.library.get(item["video_id"])
            except LibraryError:
                self.queue_item(item["id"], "Needs attention", "Removed from the library.")
                continue
            with closing(self.library.connect()) as db:
                prep = PreparationStore.read(db, video.id)
            if review_checks(video, prep, source_status(video)).state != "Review Complete":
                # Edited since it was queued: it waits (and the next ready video goes first)
                # until its details are marked ready again.
                self.queue_item(
                    item["id"], "Waiting", "Mark this video ready in Review to send it."
                )
                continue
            ai = self.draft(video.id)["ai_generated"] if self.spec.ai_label else False
            if ai is None:
                self.queue_item(
                    item["id"], "Waiting", "Choose the AI-generated content label in Details."
                )
                continue
            caption = caption_from(
                prep.metadata.title,
                prep.metadata.description,
                tuple(prep.metadata.tags),
                hashtags=self.spec.hashtags_max,
            )
            try:
                post_id = self.reserve(
                    video.id, caption, bool(ai), slot, "manual", queue["id"], cancel
                )
            except SocialError as exc:
                self.queue_item(item["id"], "Needs attention", str(exc))
                continue
            self.queue_item(item["id"], "Sent", "", post_id)
            try:
                self.host_and_submit(post_id, cancel)
            except SocialError:
                pass  # recorded on the post; retried on the next pass
            return

    def queue_item(self, identity: str, state: str, reason: str, post: str | None = None) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute(
                "UPDATE queue_items SET state=?,reason=?,post_id=coalesce(?,post_id),updated_at=? "
                "WHERE id=?",
                (state, reason, post, stamp(self.clock()), identity),
            )

    def advance(self, p: dict[str, Any], cancel: Event) -> None:
        """Submit at most one post per automation: the oldest waiting video, at the next free slot."""
        connection = self.connection()
        if connection and not self.same_destination(p["channel_id"], p["channel_name"], connection):
            self.status(
                p["id"],
                f"Not posting: {self.spec.name} is now connected to a different "
                f"{self.spec.account} than the approved {p['channel_name']}.",
            )
            return
        with closing(self.library.connect()) as db:
            paused = db.execute("SELECT paused FROM publishing_control WHERE id=1").fetchone()
            pending = db.execute(
                f"SELECT * FROM {self.table}_posts WHERE profile_id=? AND released=0 AND state IN ({','.join('?' * len(UNSENT))})",
                (p["id"], *UNSENT),
            ).fetchone()
            used = [
                r[0]
                for r in db.execute(
                    f"SELECT due_at FROM {self.table}_posts WHERE profile_id=? AND due_at IS NOT NULL AND state NOT IN ('Cancelled','Missed — not sent')",
                    (p["id"],),
                )
            ]
        if pending:
            if pending["state"] in (
                "Uploading to media host",
                "Hosted and ready",
                "Waiting for Buffer limit",
                self.spec.reconnect_state,
            ):
                self.retry_pending(
                    pending,
                    Rule.decode(p["settings"]["rule"]),
                    used,
                    p["settings"]["daily_cap"],
                    cancel,
                )
            return
        if paused and paused[0]:
            self.status(
                p["id"], "Paused app-wide: automatic publishing is paused (Turn On in the sidebar)."
            )
            return
        waiting = [
            d
            for d in reversed(self.decisions(p["id"]))
            if d["state"] == "Waiting for next opportunity"
            and d["profile_revision"] == p["revision"]
        ]
        if not waiting:
            self.status(p["id"], "Waiting for new videos.")
            return
        slot = self.next_slot(Rule.decode(p["settings"]["rule"]), used, p["settings"]["daily_cap"])
        if slot is None:
            self.status(p["id"], "Daily limit reached for the coming days.")
            return
        d = waiting[0]
        caption = caption_for(p["settings"]["caption_template"], d["name"])
        try:
            post_id = self.reserve(
                d["video_id"],
                caption,
                bool(p["settings"]["ai_generated"]),
                slot,
                "automation",
                p["id"],
                cancel,
            )
        except SocialError as exc:
            self.decide(p, d["name"], "Needs review", str(exc), d["source_sha256"], d["video_id"])
            return
        self.decide(
            p,
            d["name"],
            "Enrolled",
            f"Posting at {stamp(slot)}",
            d["source_sha256"],
            d["video_id"],
            post_id,
        )
        self.status(p["id"], f"Next post: {d['name']}")
        try:
            self.host_and_submit(post_id, cancel)
        except SocialError:
            pass  # recorded on the post; retried on the next reconciliation
