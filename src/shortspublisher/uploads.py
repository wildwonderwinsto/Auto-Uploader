"""One explicitly confirmed Private or Public upload, durable recovery, and immutable source binding."""

import json
import os
import re
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from threading import Event
from typing import Any

from filelock import FileLock, Timeout

from .accounts import AccountService, Connection
from .auth import UPLOAD_SCOPES, AccountError, Tokens, check_cancel
from .credentials import SessionStore
from .library import now, source_status
from .preparation import PreparationStore, review_checks
from .scheduling import ScheduleService, utc_now
from .source import fingerprint, signature
from .upload_http import (
    CHUNK_SIZE,
    UploadHTTP,
    UploadReply,
    acknowledged,
    classify,
    retry_delay,
    session_url,
)
from .upload_store import Attempt, UploadStore


class UploadService:
    def __init__(
        self,
        store: UploadStore,
        accounts: AccountService,
        sessions: SessionStore,
        http: UploadHTTP | None = None,
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        self.store, self.accounts, self.sessions = store, accounts, sessions
        self.http = http or UploadHTTP()
        if chunk_size <= 0 or chunk_size % (256 * 1024):
            raise ValueError("Upload chunk size must be a multiple of 256 KiB")
        self.chunk_size = chunk_size
        self.owner_token = ""
        self.schedule_clock = utc_now
        self.lock = FileLock(str(store.library.path.with_suffix(".uploads.lock")))

    def credentials(
        self, intended: str, cancel: Event, force_refresh: bool = False
    ) -> tuple[Connection, Tokens]:
        connection, config = (
            self.accounts.repository.connection(),
            self.accounts.repository.config(),
        )
        if not connection or not config or connection.channel.id != intended:
            raise AccountError(
                "The connected channel no longer matches the confirmed destination.", "identity"
            )
        tokens = self.accounts.credentials.get(connection.key)
        if not tokens or not UPLOAD_SCOPES.issubset(tokens.scopes):
            raise AccountError(
                "Authorize both read-only and upload permission in Accounts before uploading.",
                "permission",
            )
        if force_refresh or tokens.expires_at <= time.time() + 60:
            tokens = self.accounts.oauth.refresh(config, tokens, cancel)
            if not UPLOAD_SCOPES.issubset(tokens.scopes):
                raise AccountError(
                    "Refreshed authorization lacks upload permission. Reauthorize in Accounts.",
                    "permission",
                )
            self.accounts.credentials.put(connection.key, tokens)
        channels = self.accounts.youtube.channels(tokens, cancel)
        if len(channels) != 1 or channels[0].id != intended:
            raise AccountError(
                "YouTube did not return exactly the intended destination channel. No upload can proceed.",
                "identity",
            )
        return connection, tokens

    def reviewed(self, video_id: int, revision: int | None = None) -> tuple[Any, Any]:
        video = self.store.library.get(video_id)
        store = PreparationStore(self.store.library)
        prep = store.snapshot(video, source_status(video))
        if review_checks(video, prep, source_status(video)).state != "Review Complete" or (
            revision is not None and prep.revision != revision
        ):
            raise AccountError(
                "The saved metadata/source review is incomplete or changed. Complete review again before creating an upload.",
                "source",
            )
        return video, prep

    def approved_for_transfer(self, attempt: Attempt) -> tuple[Any, Any]:
        identity = attempt.snapshot.get("automation")
        if not identity:
            return self.reviewed(attempt.video_id, attempt.snapshot["revision"])
        from .automation import approved_preparation
        from .scheduling import ScheduleError

        try:
            with closing(self.store.library.connect()) as db:
                # A marker alone is never authority: require the exact persisted scheduled linkage.
                row = db.execute(
                    "SELECT j.snapshot FROM scheduled_executions e JOIN scheduled_jobs j ON j.id=e.job_id WHERE e.attempt_id=?",
                    (attempt.id,),
                ).fetchone()
                if not row or json.loads(row[0]).get("automation") != identity:
                    raise ScheduleError(
                        "No scheduled automation execution authorizes this transfer."
                    )
                video, prep, _ = approved_preparation(
                    db, self.store.library, identity, attempt.video_id, attempt.channel_id
                )
                if prep.revision != attempt.snapshot["revision"]:
                    raise ScheduleError("Approved automation metadata changed.")
                return video, prep
        except (ScheduleError, OSError) as exc:
            raise AccountError(str(exc), "source") from exc

    def execute(
        self,
        action: str,
        argument: Any,
        cancel: Event,
        progress: Callable[[Attempt], None] = lambda _: None,
    ) -> dict[str, Any]:
        identity: str | None = (
            argument if action in ("confirm", "confirm_public", "resume", "verify") else None
        )
        try:
            self.store.library.path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock.acquire(timeout=0):
                self.store.library.initialize()
                if action == "history":
                    self.store.recover()
                    return {"attempts": self.store.all()}
                if action == "cancel_confirmation":
                    old = self.store.get(argument)
                    if old.state != "Awaiting Confirmation":
                        raise AccountError(
                            "Only an unstarted confirmation can be cancelled without reconciliation."
                        )
                    self.store.update(
                        old.id, "Cancelled", error="Confirmation cancelled. No session was created."
                    )
                elif action == "release":
                    old = self.store.get(argument)
                    if old.state in ("Preparing", "Uploading"):
                        raise AccountError(
                            "Stop the active operation before acknowledging duplicate risk."
                        )
                    self.store.update(
                        old.id,
                        old.state,
                        released=True,
                        error="User acknowledged possible remote/duplicate outcome and allowed another attempt. No remote video was deleted.",
                    )
                else:
                    # Hold account lock through remote work so disconnect/consent cannot replace credentials mid-transfer.
                    with self.accounts.lock.acquire(timeout=0):
                        check_cancel(cancel)
                        if action in ("prepare", "prepare_public"):
                            video_id, revision, intended = argument
                            connection, _ = self.credentials(intended, cancel)
                            video, prep = self.reviewed(video_id, revision)
                            privacy = "public" if action == "prepare_public" else "private"
                            if privacy == "public" and prep.metadata.privacy != "public":
                                raise AccountError(
                                    "Save planned privacy as Public and complete review before public publishing.",
                                    "source",
                                )
                            with Path(video.path).open("rb") as file:
                                sha = fingerprint(file, cancel)
                                stat = signature(os.fstat(file.fileno()))
                            self.reviewed(video_id, revision)
                            payload = {
                                "snippet": {
                                    "title": prep.metadata.title,
                                    "description": prep.metadata.description,
                                    "categoryId": "22",
                                },
                                "status": {
                                    "privacyStatus": privacy,
                                    "selfDeclaredMadeForKids": prep.metadata.made_for_kids,
                                    "containsSyntheticMedia": prep.metadata.synthetic,
                                },
                            }
                            if prep.metadata.tags:
                                payload["snippet"]["tags"] = list(prep.metadata.tags)
                            snapshot = {
                                "path": video.path,
                                "filename": Path(video.path).name,
                                "duration": video.media.duration,
                                "width": video.media.width,
                                "height": video.media.height,
                                "revision": prep.revision,
                                "sha256": sha,
                                "stat": stat,
                                "size": video.media.size,
                                "chunk_size": self.chunk_size,
                                "requested_privacy": privacy,
                                "payload": payload,
                                "channel_title": connection.channel.title,
                            }
                            attempt = self.store.create(video_id, intended, snapshot)
                            return {"attempts": self.store.all(), "confirmation": attempt}
                        elif action == "scheduled":
                            from .execution import validate_execution

                            with closing(self.store.library.connect()) as db:
                                row = db.execute(
                                    "SELECT * FROM scheduled_executions WHERE id=?", (argument,)
                                ).fetchone()
                                if row is None or row["phase"] != "Claimed" or row["started_at"]:
                                    raise AccountError(
                                        "No unused scheduled execution authorizes initiation."
                                    )
                                identity = row["attempt_id"]
                                attempt = self.store.get(identity)
                                if (
                                    attempt.state != "Awaiting Confirmation"
                                    or attempt.remote_id
                                    or attempt.released
                                ):
                                    raise AccountError(
                                        "Scheduled attempt already consumed. Inspect its existing outcome."
                                    )
                                validate_execution(
                                    db,
                                    ScheduleService(self.store.library, self.schedule_clock),
                                    attempt,
                                    owner_token=self.owner_token,
                                )
                            self.transfer(attempt, True, cancel, progress)
                        elif action in ("confirm", "confirm_public", "resume"):
                            attempt = self.store.get(argument)
                            if (
                                attempt.released
                                or attempt.remote_id
                                or attempt.state
                                in (
                                    "Uploaded Private",
                                    "Public Verified",
                                    "Cancelled",
                                    "Failed",
                                    "Processing",
                                    "Transfer Complete",
                                )
                            ):
                                raise AccountError(
                                    "This attempt cannot send more media. Inspect or verify its recorded outcome."
                                )
                            if action.startswith("confirm") and attempt.requested_privacy != (
                                "public" if action == "confirm_public" else "private"
                            ):
                                raise AccountError(
                                    "Confirmation does not match the approved privacy. Public uploads require explicit Public confirmation."
                                )
                            if (
                                action.startswith("confirm")
                                and attempt.state != "Awaiting Confirmation"
                            ):
                                raise AccountError(
                                    "Confirmation is stale. Resume the existing attempt instead."
                                )
                            if action == "resume" and attempt.state == "Awaiting Confirmation":
                                raise AccountError(
                                    "Explicit upload confirmation is required first."
                                )
                            self.transfer(attempt, action.startswith("confirm"), cancel, progress)
                        elif action == "verify":
                            attempt = self.store.get(argument)
                            _, tokens = self.credentials(attempt.channel_id, cancel)
                            if not attempt.remote_id:
                                raise AccountError(
                                    "No returned video ID is available. Check the existing session instead; do not guess an ID."
                                )
                            reply = self.http.verify(tokens, attempt.remote_id)
                            if reply.status == 401:
                                _, tokens = self.credentials(attempt.channel_id, cancel, True)
                                reply = self.http.verify(tokens, attempt.remote_id)
                            if reply.status != 200:
                                raise classify(reply)
                            items = reply.body.get("items", [])
                            if len(items) != 1 or items[0].get("id") != attempt.remote_id:
                                raise AccountError(
                                    "The returned video could not be verified. Cached result remains; check YouTube Studio.",
                                    "reconciliation",
                                )
                            self.result(
                                attempt,
                                UploadReply(200, items[0]),
                                tokens,
                                cancel,
                                verify_missing=False,
                            )
                        else:
                            raise AccountError("Unknown upload operation.")
                return {"attempts": self.store.all()}
        except Timeout:
            return {
                "error": "Another upload/account operation is active. Wait for it to finish; no new operation started."
            }
        except Exception as exc:
            # NEVER propagate raw transport/Keychain errors: they can contain session capabilities.
            error = (
                str(exc)
                if isinstance(exc, AccountError)
                else "Upload operation could not be completed safely. Existing state was preserved; inspect history before retrying."
            )
            if isinstance(exc, AccountError) and exc.kind == "cancelled":
                error = "Upload operation stopped locally. Remote content was not deleted; inspect the saved attempt before resuming."
            if identity:
                try:
                    old = self.store.get(identity)
                    if action == "verify" and old.remote_id:
                        self.store.update(
                            identity,
                            "Needs Reconciliation",
                            error="Remote verification unavailable; saved response is cached. "
                            + error,
                            verification_status="unverified",
                        )
                    elif old.state in (
                        "Preparing",
                        "Uploading",
                        "Interrupted",
                        "Paused",
                        "Needs Reconciliation",
                    ):
                        kind = exc.kind if isinstance(exc, AccountError) else "reconciliation"
                        state = (
                            "Paused"
                            if kind == "cancelled"
                            else "Needs Reconciliation"
                            if kind in ("reconciliation", "storage", "source", "identity")
                            else "Interrupted"
                        )
                        self.store.update(identity, state, error=error)
                except Exception:
                    pass
            try:
                return {"error": error, "attempts": self.store.all()}
            except Exception:
                return {"error": error}

    def transfer(
        self, attempt: Attempt, initial: bool, cancel: Event, progress: Callable[[Attempt], None]
    ) -> None:
        _, tokens = self.credentials(attempt.channel_id, cancel)
        snapshot = {**attempt.snapshot, "requested_privacy": attempt.requested_privacy}
        with Path(snapshot["path"]).open("rb") as file:
            if fingerprint(file, cancel) != snapshot["sha256"]:
                raise AccountError(
                    "Source bytes differ from the approved SHA-256 fingerprint. The existing session cannot receive changed content.",
                    "source",
                )
            _, prep = self.approved_for_transfer(attempt)
            if attempt.requested_privacy == "public" and prep.metadata.privacy != "public":
                raise AccountError(
                    "The reviewed Public preference no longer matches this attempt.", "source"
                )
            expected_stat = signature(os.fstat(file.fileno()))
            if list(expected_stat) != list(snapshot["stat"]):
                raise AccountError(
                    "Source identity changed since confirmation. Reconcile this attempt and review the source again.",
                    "source",
                )
            if initial:
                from .execution import validate_execution

                with closing(self.store.library.connect()) as db, db:
                    db.execute("BEGIN IMMEDIATE")
                    if db.execute(
                        "SELECT 1 FROM scheduled_executions WHERE attempt_id=?", (attempt.id,)
                    ).fetchone():
                        validate_execution(
                            db,
                            ScheduleService(self.store.library, self.schedule_clock),
                            attempt,
                            start=True,
                            owner_token=self.owner_token,
                        )
                self.store.update(attempt.id, "Preparing", error="")  # Durable intent BEFORE POST.
                check_cancel(cancel)
                reply = self.http.initiate(tokens, snapshot)
                if reply.status == 401:
                    _, tokens = self.credentials(attempt.channel_id, cancel, True)
                    check_cancel(cancel)
                    reply = self.http.initiate(tokens, snapshot)
                if reply.status not in (200, 201):
                    problem = classify(reply)
                    if reply.status in (0, 429, 500, 502, 503, 504):
                        raise AccountError(
                            "Session creation outcome is uncertain. No session was retried; check YouTube Studio before allowing another attempt.",
                            "reconciliation",
                        )
                    self.store.update(attempt.id, "Failed", error=str(problem))
                    raise problem
                url = session_url(reply.headers.get("location", ""))
                self.sessions.put(attempt.id, url)  # Must succeed BEFORE sending any bytes.
            else:
                url = self.sessions.get(attempt.id) or ""
                if not url:
                    raise AccountError(
                        "No secure resumable session is available. Remote outcome cannot be determined; check YouTube Studio before allowing another attempt.",
                        "reconciliation",
                    )
                session_url(url)
            self.store.update(attempt.id, "Uploading", error="")
            offset, failures, query_first = attempt.acknowledged, 0, True
            refreshed = False
            while True:
                check_cancel(cancel)
                if (
                    signature(os.fstat(file.fileno())) != expected_stat
                    or signature(Path(snapshot["path"]).stat()) != expected_stat
                ):
                    raise AccountError(
                        "Source changed during upload. No additional chunks will be sent; the remote outcome needs reconciliation.",
                        "source",
                    )
                self.approved_for_transfer(attempt)
                if tokens.expires_at <= time.time() + 60:
                    _, tokens = self.credentials(attempt.channel_id, cancel, True)
                if query_first:
                    reply = self.http.query(tokens, url, snapshot["size"])
                    sent_end = snapshot["size"]
                else:
                    file.seek(offset)
                    block = file.read(min(snapshot["chunk_size"], snapshot["size"] - offset))
                    if not block:
                        raise AccountError(
                            "No media remained but YouTube has not confirmed completion. Query this session again.",
                            "reconciliation",
                        )
                    if signature(os.fstat(file.fileno())) != expected_stat:
                        raise AccountError(
                            "Source changed while reading a chunk. No additional bytes were sent.",
                            "source",
                        )
                    sent_end = offset + len(block)
                    reply = self.http.chunk(tokens, url, block, offset, snapshot["size"])
                if reply.status in (200, 201):
                    self.result(attempt, reply, tokens, cancel)
                    progress(self.store.get(attempt.id))
                    return
                if reply.status == 308:
                    confirmed = acknowledged(reply, snapshot["size"])
                    if confirmed > sent_end or (confirmed < offset and not query_first):
                        raise AccountError(
                            "Acknowledged bytes conflict with the sent range. Reconcile this session.",
                            "reconciliation",
                        )
                    if not query_first and confirmed == offset:
                        failures += 1
                        if failures >= 3:
                            raise AccountError(
                                "YouTube repeatedly acknowledged no new bytes. Pause and resume later.",
                                "retry",
                            )
                        if cancel.wait(retry_delay(reply, failures - 1)):
                            check_cancel(cancel)
                    elif confirmed > offset:
                        failures = 0
                    offset = confirmed
                    progress(self.store.update(attempt.id, "Uploading", acknowledged=offset))
                    query_first = False
                    if offset == snapshot["size"]:
                        raise AccountError(
                            "All bytes are acknowledged, but completion and video ID are unknown. Reconcile the existing session.",
                            "reconciliation",
                        )
                    continue
                problem = classify(reply)
                if problem.kind == "unauthorized" and not refreshed:
                    _, tokens = self.credentials(attempt.channel_id, cancel, True)
                    refreshed, query_first = True, True
                    continue
                if problem.kind == "retry" and failures < 3:
                    delay = retry_delay(reply, failures)
                    failures += 1
                    if cancel.wait(delay):
                        check_cancel(cancel)
                    query_first = True  # Lost final responses are reconciled, never blindly resent.
                    continue
                if problem.kind == "permanent":
                    self.store.update(attempt.id, "Failed", error=str(problem))
                raise problem

    def result(
        self,
        attempt: Attempt,
        reply: UploadReply,
        tokens: Tokens,
        cancel: Event,
        verify_missing: bool = True,
    ) -> None:
        requested = attempt.requested_privacy
        body = reply.body
        identity = body.get("id", "")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", identity):
            raise AccountError(
                "The transfer response did not contain a usable video ID. Reconcile the existing session.",
                "reconciliation",
            )
        if attempt.remote_id and identity != attempt.remote_id:
            raise AccountError("Returned video ID differs from the saved result.", "reconciliation")
        status, snippet = body.get("status", {}), body.get("snippet", {})
        privacy, channel = status.get("privacyStatus", ""), snippet.get("channelId", "")
        processing = str(
            body.get("processingDetails", {}).get(
                "processingStatus", status.get("uploadStatus", "unknown")
            )
        )
        current = self.store.get(attempt.id)
        if not current.remote_id:
            current = self.store.update(
                attempt.id,
                "Transfer Complete",
                acknowledged=attempt.snapshot["size"],
                remote_id=identity,
                privacy=str(privacy),
                processing=processing,
                completed_at=current.completed_at or now(),
                verification_status="unverified",
                error="Transfer response received; requested visibility/channel verification pending.",
            )
        if (privacy and privacy != requested) or (channel and channel != attempt.channel_id):
            self.store.update(
                attempt.id,
                "Needs Reconciliation",
                privacy=str(privacy),
                processing=processing,
                verification_status="discrepancy",
                verified_at=now() if not verify_missing else current.verified_at,
                error=(
                    f"CRITICAL: requested {requested.title()} but YouTube returned {str(privacy) or 'unknown'} privacy. "
                    if privacy and privacy != requested
                    else "CRITICAL: returned destination channel differs from confirmation. "
                )
                + "No remote change or deletion was attempted. Inspect YouTube Studio; do not repeat this upload.",
            )
            return
        # Every Public completion requires an independent videos.list result. Insert alone
        # cannot establish Public Verified, even when it includes all requested fields.
        if verify_missing and (requested == "public" or not privacy or not channel):
            if not cancel.is_set():
                verified = self.http.verify(tokens, identity)
                if verified.status == 401:
                    _, tokens = self.credentials(attempt.channel_id, cancel, True)
                    verified = self.http.verify(tokens, identity)
                items = verified.body.get("items", [])
                if (
                    verified.status == 200
                    and isinstance(items, list)
                    and len(items) == 1
                    and items[0].get("id") == identity
                ):
                    self.result(current, UploadReply(200, items[0]), tokens, cancel, False)
                    return
            self.store.update(
                attempt.id,
                "Transfer Complete",
                verification_status="unverified",
                error="Video ID saved; privacy and destination are not yet verified independently. Use Verify Remote Result, not another upload.",
            )
            return
        if not privacy or not channel:
            self.store.update(
                attempt.id,
                "Needs Reconciliation",
                verification_status="unverified",
                error="Video ID saved, but requested privacy and destination remain unverified. Use Verify Remote Result; do not start another upload.",
            )
            return
        state = (
            "Failed"
            if processing in ("failed", "rejected", "terminated")
            else "Processing"
            if processing in ("processing", "uploaded")
            else "Public Verified"
            if requested == "public"
            else "Uploaded Private"
        )
        # Restriction reasons are allowlisted, never raw API messages or headers.
        reason = status.get("rejectionReason", status.get("failureReason", ""))
        safe_reasons = {
            "copyright",
            "duplicate",
            "inappropriate",
            "legal",
            "length",
            "termsOfUse",
            "trademark",
            "uploaderAccountClosed",
            "uploaderAccountSuspended",
            "codec",
            "conversion",
            "emptyFile",
            "invalidFile",
            "tooSmall",
            "uploadAborted",
        }
        detail = f" Reason: {reason}." if isinstance(reason, str) and reason in safe_reasons else ""
        self.store.update(
            attempt.id,
            state,
            privacy=str(privacy),
            processing=processing,
            verification_status="verified",
            verified_at=now(),
            error=f"{requested.title()} privacy and destination verified. Processing status: {processing}."
            + detail
            + (
                " Processing has not completed; visibility alone does not prove playback availability."
                if processing not in ("succeeded", "processed")
                else ""
            ),
        )
