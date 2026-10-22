"""TikTok publishing through Buffer: validation, hosting, submission, status and automation.

ShortsPublisher decides *which* video posts and *when*; Buffer performs the post at that exact
time (`schedulingType: automatic`, `mode: customScheduled` + `dueAt`, or `shareNow`). Buffer's own
queue (`addToQueue`) is never used, and each automation keeps at most one unsent Buffer post, so
there are no competing queues. Status comes from Buffer (`scheduled`, `sending`, `sent`, `error`);
a Buffer post ID is not a TikTok video ID and nothing here invents one.
"""

from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import closing
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
from .library import Library, LibraryError, Video
from .scheduling import ScheduleError
from .source import fingerprint

if TYPE_CHECKING:
    from .watching import WatchService

CAPTION_MAX = 2200  # Buffer's TikTok caption limit
HASHTAGS_MAX = 5  # Buffer's TikTok hashtag limit
EXTENSIONS = {".mp4", ".mov", ".webm"}
MAX_BYTES = 1024**3  # Buffer: 1 GB per TikTok video
UNSENT = (
    "Uploading to media host",
    "Hosted and ready",
    "Waiting for Buffer limit",
    "Waiting for TikTok reconnection",
    "Sending to Buffer",
    "Scheduled in Buffer",
    "Publishing",
    "Needs reconciliation",
)
CHECK_BACKOFF = (2, 5, 15, 60)  # minutes after the due time; then hourly
GIVE_UP_HOURS = 48
RETENTION_GRACE = timedelta(hours=24)
INSIGHTS_KEY = "buffer-insights-key"  # optional personal API key, only for view counts
HOSTING_CONSENT = (
    "Videos you publish to TikTok are first copied to your Cloudinary account and served from a "
    "public HTTPS link, because Buffer only accepts a public video URL. Anyone who obtains that "
    "link can watch the copy until it is deleted. Links use unpredictable names, but that is not "
    "privacy. Copies are made only when you publish or schedule to TikTok, or when an approved "
    "TikTok automation enrolls a new video."
)
AUTOMATION_CONSENT = (
    "I authorize ShortsPublisher to copy each NEW eligible video in this exact folder to my media "
    "host and post it PUBLICLY to this exact TikTok account through Buffer, at the approved times, "
    "with this caption rule and AI disclosure, until I pause, revoke or remove this automation. "
    "Videos already in the folder are excluded."
)


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds")


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


def readable(filename: str) -> str:
    return re.sub(r"[_\s]+", " ", Path(filename).stem).strip()


def caption_for(template: str, filename: str) -> str:
    return template.replace("{filename}", readable(filename)).strip()


def caption_from(title: str, description: str, tags: tuple[str, ...]) -> str:
    """One caption from the shared video details: title, description, then tags as hashtags.

    #Shorts is YouTube's own marker and is left out; hashtags stop at Buffer's limit of five."""

    def tidy(text: str) -> str:
        return re.sub(r"[ \t]*#shorts\b", "", text, flags=re.IGNORECASE).strip()

    parts = [tidy(title), tidy(description)]
    present = {tag.lower() for tag in re.findall(r"#\w+", " ".join(parts))}
    hashtags: list[str] = []
    for tag in tags:
        word = re.sub(r"\W", "", tag)
        mark = f"#{word}"
        if word and mark.lower() not in present and len(present) < HASHTAGS_MAX:
            present.add(mark.lower())
            hashtags.append(mark)
    parts.append(" ".join(hashtags))
    return "\n\n".join(part for part in parts if part)


def caption_errors(caption: str) -> list[str]:
    errors = []
    if len(caption) > CAPTION_MAX:
        errors.append(
            f"Caption is {len(caption)} characters; TikTok via Buffer allows {CAPTION_MAX}."
        )
    if len(re.findall(r"#\w+", caption)) > HASHTAGS_MAX:
        errors.append(f"Use at most {HASHTAGS_MAX} hashtags (Buffer's TikTok limit).")
    return errors


def media_errors(video: Video, hosting_limit: int) -> list[str]:
    m, errors = video.media, []
    if Path(video.path).suffix.lower() not in EXTENSIONS:
        errors.append("TikTok via Buffer accepts MP4, MOV or WEBM files.")
    if not 3 <= m.duration <= 600:
        errors.append("TikTok automatic posts must be 3 seconds to 10 minutes long.")
    if min(m.width, m.height) < 360:
        errors.append("TikTok needs at least 360 pixels in width and height.")
    if m.fps is not None and not 23 <= m.fps <= 60:
        errors.append("TikTok via Buffer needs 23–60 frames per second.")
    limit = min(MAX_BYTES, hosting_limit)
    if m.size > limit:
        errors.append(
            f"The file is {m.size / 1024**2:.0f} MiB; the current limit is {limit / 1024**2:.0f} MiB."
        )
    return errors


class TikTokError(Exception):
    pass


class TikTokService:
    def __init__(
        self,
        library: Library,
        secrets: SecretStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        *,
        api_session: Any | None = None,
        oauth: BufferOAuth | None = None,
        host_session: Any | None = None,
        anonymous: Any | None = None,
    ) -> None:
        self.library, self.secrets, self.clock = library, secrets, clock
        self.api_session, self.host_session, self.anonymous = api_session, host_session, anonymous
        self.authority = BufferAuthority(
            secrets,
            Path(str(library.path) + ".buffer.lock"),
            self.client,
            lambda: (self.connection() or {}).get("key"),
            oauth or BufferOAuth(api_session, lambda: self.clock().timestamp()),
            lambda: self.clock().timestamp(),
        )
        self.lock = Path(str(library.path) + ".tiktok.lock")

    # Configuration ---------------------------------------------------------------------------

    def client(self) -> BufferClientConfig | None:
        self.library.initialize()
        with closing(self.library.connect()) as db:
            row = db.execute(
                "SELECT client_config FROM account_config WHERE platform='tiktok'"
            ).fetchone()
        return BufferClientConfig.parse(json.loads(row[0])) if row else None

    def save_client(self, client_id: str, redirect_uri: str) -> BufferClientConfig:
        config = BufferClientConfig.parse({"client_id": client_id, "redirect_uri": redirect_uri})
        self.library.initialize()
        with closing(self.library.connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO account_config VALUES ('tiktok', ?)",
                (json.dumps({"client_id": config.client_id, "redirect_uri": config.redirect_uri}),),
            )
        return config

    def connection(self) -> dict[str, Any] | None:
        self.library.initialize()
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM account_connection WHERE platform='tiktok'").fetchone()
        if not row:
            return None
        return {
            "key": row["credential_key"],
            **json.loads(row["channel"]),
            "verified_at": row["verified_at"],
        }

    def set_connection_status(self, status: str) -> None:
        current = self.connection()
        if current and current.get("status") != status:
            channel = {k: v for k, v in current.items() if k not in ("key", "verified_at")}
            channel["status"] = status
            with closing(self.library.connect()) as db, db:
                db.execute(
                    "UPDATE account_connection SET channel=? WHERE platform='tiktok'",
                    (json.dumps(channel),),
                )

    def token(self) -> str:
        current = self.connection()
        if current and current.get("status") == "Reconnect required":
            # After an uncertain or rejected renewal the stored refresh token may already be
            # spent; using it again would revoke the grant. Only a fresh sign-in may continue.
            raise BufferError("Reconnect TikTok in Accounts to continue.", "reconnect")
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
            raise TikTokError("Paste your Buffer personal API key first.")
        self.secrets.put(INSIGHTS_KEY, key)

    def remove_insights_key(self) -> None:
        self.secrets.delete(INSIGHTS_KEY)

    def statistics(self, cancel: Event) -> dict[str, dict[str, int]]:
        """View counts for posted TikToks, keyed by Buffer post ID. Read-only; empty without
        the optional personal API key. Buffer refreshes these once a day."""
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

    def finish_connect(self, code: str, verifier: str) -> tuple[str, list[dict[str, Any]]]:
        """Exchange the code, store tokens under a fresh key, return TikTok channels to choose."""
        config = self.client()
        if not config:
            raise BufferError("Save the Buffer app client first.", "config")
        tokens: BufferTokens = self.authority.oauth.exchange(config, code, verifier)
        key = "buffer-" + uuid.uuid4().hex
        self.authority.save(key, tokens)
        api = BufferAPI(lambda: tokens.access_token, self.api_session)
        try:
            channels = api.tiktok_channels()
        except BufferError:
            self.secrets.delete(key)
            raise
        return key, channels

    def select_channel(self, key: str, channel: dict[str, Any]) -> None:
        if channel.get("isDisconnected") or channel.get("isLocked"):
            raise BufferError(
                "This TikTok channel is disconnected or locked in Buffer. Reconnect it in Buffer first.",
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
        with closing(self.library.connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO account_connection VALUES ('tiktok', ?, ?, ?)",
                (key, json.dumps(record), stamp(self.clock())),
            )
        if old and old["key"] != key:
            self.secrets.delete(old["key"])

    def discard_key(self, key: str) -> None:
        self.secrets.delete(key)

    def disconnect(self) -> None:
        current = self.connection()
        with closing(self.library.connect()) as db, db:
            db.execute("DELETE FROM account_connection WHERE platform='tiktok'")
        if current:
            self.secrets.delete(current["key"])

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
                """INSERT OR REPLACE INTO media_hosting VALUES (1,'cloudinary',?,?,?,?,?,?,?)""",
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
        old = self.hosting()
        with closing(self.library.connect()) as db, db:
            db.execute("DELETE FROM media_hosting WHERE id=1")
        if old:
            self.secrets.delete(old["credential_key"])

    def host(self) -> CloudinaryHost:
        config = self.hosting()
        if not config:
            raise HostingError("Set up TikTok media hosting in Settings first.", "config")
        secret = self.secrets.get(config["credential_key"]) or ""
        return CloudinaryHost(
            config["cloud_name"],
            config["api_key"],
            secret,
            self.host_session,
            lambda: self.clock().timestamp(),
            self.anonymous,
        )

    # Drafts ----------------------------------------------------------------------------------

    def draft(self, video_id: int) -> dict[str, Any]:
        self.library.initialize()
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM tiktok_drafts WHERE video_id=?", (video_id,)).fetchone()
        if not row:
            return {"caption": "", "ai_generated": None}
        return {
            "caption": row["caption"],
            "ai_generated": None if row["ai_generated"] is None else bool(row["ai_generated"]),
        }

    def save_draft(self, video_id: int, caption: str, ai_generated: bool | None) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO tiktok_drafts VALUES (?,?,?,?)",
                (video_id, caption, ai_generated, stamp(self.clock())),
            )

    # Posts -----------------------------------------------------------------------------------

    def posts(self, video_id: int | None = None) -> list[dict[str, Any]]:
        self.library.initialize()
        with closing(self.library.connect()) as db:
            rows = db.execute(
                "SELECT * FROM tiktok_posts"
                + (" WHERE video_id=?" if video_id is not None else "")
                + " ORDER BY created_at DESC",
                (video_id,) if video_id is not None else (),
            ).fetchall()
        return [dict(r) for r in rows]

    def update(self, db: sqlite3.Connection, post_id: str, **values: Any) -> None:
        values["updated_at"] = stamp(self.clock())
        db.execute(
            f"UPDATE tiktok_posts SET {','.join(f'{k}=?' for k in values)} WHERE id=?",
            (*values.values(), post_id),
        )

    def write(self, post_id: str, **values: Any) -> None:
        with closing(self.library.connect()) as db, db:
            self.update(db, post_id, **values)

    def get(self, post_id: str) -> dict[str, Any]:
        with closing(self.library.connect()) as db:
            row = db.execute("SELECT * FROM tiktok_posts WHERE id=?", (post_id,)).fetchone()
        if not row:
            raise TikTokError("That TikTok post record no longer exists.")
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
            raise TikTokError("Choose a time at least two minutes from now.")
        try:
            with FileLock(str(self.lock), timeout=60):
                post_id = self.reserve(video_id, caption, ai_generated, due, "manual", None, cancel)
                self.host_and_submit(post_id, cancel)
                return post_id
        except Timeout:
            raise TikTokError("Another TikTok operation is running. Try again shortly.") from None

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
        connection = self.connection()
        if not connection:
            raise TikTokError("Connect TikTok via Buffer in Accounts first.")
        if connection.get("status") != "Connected":
            raise TikTokError("Reconnect TikTok in Accounts before publishing.")
        hosting = self.hosting()
        if not hosting:
            raise TikTokError("Set up TikTok media hosting in Settings first.")
        if ai_generated is None:
            raise TikTokError("Choose whether the video contains AI-generated content.")
        video = self.library.get(video_id)
        errors = caption_errors(caption) + media_errors(video, hosting["max_bytes"])
        if errors:
            raise TikTokError(" ".join(errors))
        try:
            with open(video.path, "rb") as file:
                sha = fingerprint(file, cancel)
        except OSError as exc:
            raise TikTokError(f"Cannot read the original video: {exc.strerror}") from exc
        post_id, when = uuid.uuid4().hex, stamp(self.clock())
        try:
            with closing(self.library.connect()) as db, db:
                db.execute(
                    """INSERT INTO tiktok_posts(id,source_sha256,video_id,source_path,channel_id,channel_name,origin,
                    profile_id,caption,ai_generated,mode,due_at,state,hosted_id,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'Uploading to media host',?,?,?)""",
                    (
                        post_id,
                        sha,
                        video.id,
                        video.path,
                        connection["id"],
                        connection.get("displayName") or connection.get("name") or "TikTok",
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
            raise TikTokError(
                "This exact video already has a TikTok post for this account"
                + (f" ({existing['state']})" if existing else "")
                + ". Duplicate posts are blocked; see its TikTok history."
            ) from None
        return post_id

    def host_and_submit(self, post_id: str, cancel: Event) -> None:
        post = self.get(post_id)
        if post["state"] == "Uploading to media host":
            host = self.host()
            hosting = self.hosting()
            assert hosting
            try:
                hosted = host.upload(
                    Path(post["source_path"]),
                    post["source_sha256"],
                    hosting["max_bytes"],
                    cancel,
                    post["hosted_id"],
                )
            except HostingError as exc:
                self.write(post_id, state="Hosting failed", error=str(exc))
                raise TikTokError(str(exc)) from exc
            self.write(post_id, hosted_url=hosted.url, state="Hosted and ready", error="")
            try:
                host.verify(hosted.url, hosted.size)
            except HostingError as exc:
                self.write(post_id, error=str(exc) + " Retrying before Buffer is contacted.")
                raise TikTokError(str(exc)) from exc
        self.submit(post_id)

    def submit(self, post_id: str) -> None:
        post = self.get(post_id)
        if post["state"] not in (
            "Hosted and ready",
            "Waiting for Buffer limit",
            "Waiting for TikTok reconnection",
        ):
            return
        with closing(self.library.connect()) as db:
            retry = db.execute("SELECT retry_after FROM buffer_control WHERE id=1").fetchone()[0]
        if retry and parse(retry) > self.clock():
            self.write(
                post_id, state="Waiting for Buffer limit", error=f"Buffer limit resets {retry}"
            )
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
        connection = self.connection()
        assert connection
        payload: dict[str, Any] = {
            "channelId": post["channel_id"],
            "text": post["caption"],
            "schedulingType": "automatic",
            "mode": post["mode"],
            "needsApproval": False,
            "assets": [{"video": {"url": post["hosted_url"]}}],
            "metadata": {"tiktok": {"isAiGenerated": bool(post["ai_generated"])}},
            "source": "ShortsPublisher",
        }
        if post["mode"] == "customScheduled":
            payload["dueAt"] = post["due_at"].replace("+00:00", "Z")
        # Durable intent before the network call: a crash here becomes reconciliation, not a resend.
        self.write(post_id, state="Sending to Buffer", submitted_at=stamp(self.clock()), error="")
        try:
            result = self.api().create_post(payload)
        except BufferError as exc:
            if exc.kind == "rate":
                until = self.clock() + timedelta(seconds=exc.retry_after or 900)
                with closing(self.library.connect()) as db, db:
                    db.execute(
                        "UPDATE buffer_control SET retry_after=? WHERE id=1", (stamp(until),)
                    )
                self.write(post_id, state="Waiting for Buffer limit", error=str(exc))
            elif exc.kind in ("auth", "reconnect"):
                if exc.kind == "reconnect":
                    self.set_connection_status("Reconnect required")
                self.write(post_id, state="Waiting for TikTok reconnection", error=str(exc))
            elif exc.kind == "uncertain":
                self.write(
                    post_id,
                    state="Needs reconciliation",
                    error=str(exc),
                    next_check_at=stamp(self.clock() + timedelta(minutes=2)),
                )
            else:
                self.write(post_id, state="Failed", error=str(exc))
            raise TikTokError(str(exc)) from exc
        self.apply(post_id, result)

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
            error = "Buffer will ask for this post to be finished in the TikTok app; it is not automatic."
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
            return stamp(due + timedelta(seconds=90))
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
            try:
                if post["state"] == "Needs reconciliation":
                    self.reconcile_post(post, connection)
                elif post["buffer_post_id"]:
                    remote = self.api().post(post["buffer_post_id"])
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
            except BufferError as exc:
                if exc.kind == "rate":
                    with closing(self.library.connect()) as db, db:
                        db.execute(
                            "UPDATE buffer_control SET retry_after=? WHERE id=1",
                            (stamp(now + timedelta(seconds=exc.retry_after or 900)),),
                        )
                    return
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
            for p in self.api().recent_posts(connection["organization_id"], post["channel_id"])
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
            "Waiting for TikTok reconnection",
            "Hosting failed",
        ):
            raise TikTokError("Only posts that have not started publishing can be cancelled.")
        if post["buffer_post_id"]:
            remote = self.api().post(post["buffer_post_id"])
            if remote and remote.get("status") not in ("scheduled", "draft"):
                self.apply(post_id, remote)
                raise TikTokError(
                    "Buffer has already started this post; it can no longer be cancelled."
                )
            self.api().delete_post(post["buffer_post_id"])
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
            raise TikTokError("This post does not need a new attempt.")
        self.write(post_id, released=1, next_check_at=None)

    def remove_hosted(self, post_id: str) -> None:
        post = self.get(post_id)
        if not post["hosted_id"] or post["hosted_deleted_at"]:
            return
        try:
            self.host().destroy(post["hosted_id"])
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
                "SELECT * FROM tiktok_profiles WHERE removed=0 ORDER BY created_at"
            ).fetchall()
        return [{**dict(r), "settings": json.loads(r["settings"])} for r in rows]

    def decisions(self, profile_id: str) -> list[dict[str, Any]]:
        with closing(self.library.connect()) as db:
            rows = db.execute(
                "SELECT * FROM tiktok_decisions WHERE profile_id=? ORDER BY updated_at DESC",
                (profile_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def preview(self, values: dict[str, Any]) -> dict[str, Any]:
        connection = self.connection()
        if not connection or connection.get("status") != "Connected":
            raise TikTokError("Connect TikTok via Buffer in Accounts first.")
        if not self.hosting():
            raise TikTokError("Set up TikTok media hosting in Settings first.")
        with closing(self.library.connect()) as db:
            folder = db.execute(
                "SELECT * FROM watch_folders WHERE id=? AND removed=0", (values.get("folder_id"),)
            ).fetchone()
        if not folder:
            raise TikTokError("Choose an authorized watched folder.")
        settings = values["settings"]
        if settings.get("ai_generated") is None:
            raise TikTokError("Choose whether these videos contain AI-generated content.")
        if not 1 <= int(settings.get("daily_cap", 0)) <= 10:
            raise TikTokError("Choose a daily limit from 1 to 10 posts.")
        template = settings.get("caption_template", "")
        if set(re.findall(r"\{[^}]*\}", template)) - {"{filename}"}:
            raise TikTokError("The caption template supports only {filename}.")
        errors = caption_errors(caption_for(template, "x" * 40 + ".mp4"))
        if errors:
            raise TikTokError(" ".join(errors))
        rule = Rule.decode(settings["rule"])
        opportunities = [stamp(t) for t in rule.next(self.clock(), 5)]
        name = values.get("name", "").strip() or "TikTok automation"
        summary = (
            f"{name}\nFolder: {folder['path']}\nTikTok account: "
            f"{connection.get('displayName') or connection.get('name')} (via Buffer)\nVisibility: PUBLIC "
            f"(Buffer posts publicly by default; privacy is not selectable through Buffer's API)\n"
            f"Caption: {template}\nAI-generated content disclosed: {'Yes' if settings['ai_generated'] else 'No'}\n"
            f"Up to {settings['daily_cap']} posts per local day · eligible filenames: {settings.get('pattern') or '*'}\n"
            f"Background service allowed: {'Yes' if settings.get('background') else 'No'}\n"
            "Next opportunities:\n" + "\n".join(opportunities)
        )
        return {
            "id": values.get("id") or "",
            "name": name,
            "folder_id": folder["id"],
            "folder_path": folder["path"],
            "channel_id": connection["id"],
            "channel_name": connection.get("displayName") or connection.get("name") or "TikTok",
            "settings": {**settings, "pattern": settings.get("pattern") or "*"},
            "summary": summary,
            "opportunities": opportunities,
        }

    def approve(self, proposal: dict[str, Any], scanner: WatchService, cancel: Event) -> str:
        folder = scanner.preview(proposal["folder_path"], cancel)
        when = stamp(self.clock())
        identity = proposal["id"] or uuid.uuid4().hex
        consent = json.dumps(
            {"text": AUTOMATION_CONSENT, "hosting": HOSTING_CONSENT, "summary": proposal["summary"]}
        )
        with closing(self.library.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT revision FROM tiktok_profiles WHERE id=?", (identity,)
            ).fetchone()
            revision = (old[0] if old else 0) + 1
            db.execute(
                """INSERT INTO tiktok_profiles(id,revision,name,folder_id,channel_id,channel_name,settings,consent,enabled,revoked,removed,status,created_at,updated_at)
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
                db.execute(
                    """INSERT OR IGNORE INTO tiktok_decisions(profile_id,profile_revision,name,state,reason,source_sha256,created_at,updated_at)
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
                "SELECT revoked FROM tiktok_profiles WHERE id=?", (identity,)
            ).fetchone()
            if not row:
                raise TikTokError("That TikTok automation no longer exists.")
            if action == "resume" and row[0]:
                raise TikTokError("Revoked automations need renewed approval.")
            db.execute(
                f"UPDATE tiktok_profiles SET {column},updated_at=? WHERE id=?",
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
        with closing(self.library.connect()) as db, db:
            db.execute(
                """INSERT INTO tiktok_decisions(profile_id,profile_revision,name,source_sha256,video_id,state,reason,post_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(profile_id,profile_revision,name) DO UPDATE SET
                source_sha256=excluded.source_sha256,video_id=excluded.video_id,state=excluded.state,reason=excluded.reason,
                post_id=COALESCE(excluded.post_id,tiktok_decisions.post_id),updated_at=excluded.updated_at
                WHERE tiktok_decisions.state!='Excluded existing'""",
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
                            TikTokError,
                            BufferError,
                            HostingError,
                            ScheduleError,
                            LibraryError,
                            OSError,
                        ) as exc:
                            self.status(p["id"], str(exc))
                if self.connection():
                    self.refresh(cancel)
                    self.cleanup()
        except Timeout:
            return
        except Exception as exc:  # The local scanner must keep working whatever Buffer does.
            with closing(self.library.connect()) as db, db:
                db.execute(
                    "UPDATE tiktok_profiles SET status=? WHERE enabled=1",
                    (f"Needs attention: {exc}"[:300],),
                )

    def status(self, identity: str, text: str) -> None:
        with closing(self.library.connect()) as db, db:
            db.execute("UPDATE tiktok_profiles SET status=? WHERE id=?", (text[:300], identity))

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
            raise TikTokError(
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
            errors = caption_errors(caption) + media_errors(
                video, hosting["max_bytes"] if hosting else MAX_BYTES
            )
            with closing(self.library.connect()) as db:
                dup = db.execute(
                    "SELECT state FROM tiktok_posts WHERE source_sha256=? AND channel_id=? AND released=0",
                    (file["sha256"], p["channel_id"]),
                ).fetchone()
            if dup:
                self.decide(
                    p,
                    file["name"],
                    "Duplicate — skipped",
                    f"Already posted or pending for this TikTok account ({dup[0]}).",
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

    def advance(self, p: dict[str, Any], cancel: Event) -> None:
        """Submit at most one post per automation: the oldest waiting video, at the next free slot."""
        with closing(self.library.connect()) as db:
            paused = db.execute("SELECT paused FROM publishing_control WHERE id=1").fetchone()
            pending = db.execute(
                f"SELECT * FROM tiktok_posts WHERE profile_id=? AND released=0 AND state IN ({','.join('?' * len(UNSENT))})",
                (p["id"], *UNSENT),
            ).fetchone()
            used = [
                r[0]
                for r in db.execute(
                    "SELECT due_at FROM tiktok_posts WHERE profile_id=? AND due_at IS NOT NULL AND state NOT IN ('Cancelled','Missed — not sent')",
                    (p["id"],),
                )
            ]
        if pending:
            if pending["state"] in (
                "Uploading to media host",
                "Hosted and ready",
                "Waiting for Buffer limit",
                "Waiting for TikTok reconnection",
            ):
                self.host_and_submit(pending["id"], cancel)
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
        rule = Rule.decode(p["settings"]["rule"])
        zone = ZoneInfo(rule.zone)
        counts: dict[Any, int] = {}
        for due in used:
            day = parse(due).astimezone(zone).date()
            counts[day] = counts.get(day, 0) + 1
        latest = max((parse(d) for d in used), default=self.clock())
        after = max(self.clock() + timedelta(minutes=5), latest)
        slot = next(
            (
                t
                for t in rule.next(after, 32)
                if counts.get(t.astimezone(zone).date(), 0) < int(p["settings"]["daily_cap"])
            ),
            None,
        )
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
        except TikTokError as exc:
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
        except TikTokError:
            pass  # recorded on the post; retried on the next reconciliation
