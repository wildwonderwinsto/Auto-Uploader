# ShortsPublisher architecture

Authoritative design decisions, recorded by stage. For a quick map see `CLAUDE.md`; for
current capabilities see `current-status.md`. Sections below describe the stage that introduced
them; later sections refine earlier ones.

## Stage 10 boundaries

The completed local workflow is import → inspection → library → publishing draft → explicit review.
Stage 4 adds Google desktop OAuth and read-only channel verification. Stage 5 adds explicit
Private manual resumable uploads. Stage 7 extends the same engine with explicitly approved Public uploads. Stage 8 adds local plans, Queue and Calendar. Stage 9 connects explicitly activated plans to the same Public uploader. Stage 10 adds an explicitly authorized per-user headless service. Analytics and transcoding remain excluded.

## Data flow

`app.build` composes `LibraryService(Library(path), Inspector(executable))` and passes it to
`MainWindow`. The Videos page starts exactly one `LibraryWorker` (`QThread`) at a time.
The worker calls the service; queued signals deliver results to decorated UI slots. Background
code never mutates widgets. Library loading, source checks, removal, and inspection all run off
the UI thread. Navigation remains usable while library controls are temporarily disabled.

The service resolves a canonical source path and checks for a duplicate before inspection.
The inspector reads the original in place. FFprobe receives an argument list (no shell), local
protocol allowlist, two decoder threads, and an explicit metadata field list. It counts decoded
frames through the input; required metadata, decoder errors, subprocess exit, and before/after
file identity/size/mtime checks are validated. Optional metadata is explicitly unknown or warned.
Rotation and pixel aspect ratio affect display-shape readiness; encoded dimensions are preserved.

The inspector polls every 100 ms for cooperative cancellation and enforces a 120-second timeout.
Its `finally` block kills and reaps a pending process. Closing a window during work defers closure
until cancellation finishes, retaining widgets and the worker until then. Application-level Quit
also cancels and joins the worker. A DB transaction already committed before cancellation may
remain, but it is always complete. No partially inspected record is inserted.

## SQLite

The production database is in Qt's OS application-data directory, outside installed source.
`--data-dir` allows isolated tests. No filesystem operations occur on Python module import. Existing databases trigger a background
account restoration at startup; new databases initialize on opening Videos or Accounts. No whole-library file scan runs.

Each service operation opens/closes its own connections on the worker thread. SQLite connections
never cross threads. The schema (version 11 since Stage 16) uses `PRAGMA user_version`; initialization runs inside
`BEGIN IMMEDIATE`. Future migrations must be explicit, transactional upgrades; unknown future
versions are refused. Corrupt/unwritable/locked databases produce errors, never silent resets.

The `videos` table contains a non-reused integer ID, UNIQUE canonical path, UTC import and last-successful
inspection timestamps, a JSON-encoded `Media` value, the latest refresh error, and an inspection revision. JSON is a typed
record field inside SQLite, not a separate persistence system. Insert uses ON CONFLICT to protect
against duplicate concurrent imports. Removal deletes only a row. Failed refreshes retain the
last metadata and attach an error; successful refreshes clear it while preserving ID/import date.

Selecting a row checks the single source's stat data in the worker. Missing/inaccessible/changed
sources are shown as unverified; nothing automatically deletes their records. Row labels identify
saved inspection results, not a claim that every unselected source was recently rechecked.

## Readiness and dependencies

`readiness.py` owns YouTube-only pure rules with a dated version and source URL. Dimensions and
maximum duration are eligibility checks; silent audio and unknown optional metadata are warnings.
No recommended export preset is elevated to a mandatory rule. Invalid media cannot be newly
saved. A valid video with warnings can be saved because the local library also holds videos
that are unsuitable for Shorts.

Stages 2 and 3 add only Python standard-library modules (SQLite, subprocess, dataclasses, etc.) alongside
existing PySide6. FFprobe is an external runtime prerequisite, discovered on PATH or explicitly
configured with `--ffprobe`. It is never downloaded automatically. FFmpeg generates deterministic
playable fixtures in tests only. No change to Python dependencies or the lockfile was necessary.

## Future work

Application services own workflows, platform adapters own API capabilities, and UI code remains
independent of provider SDKs. Introduce a shared adapter contract only when a future stage needs
one. SQLite migrations will own new durable fields. OS Keychain storage owns authorization tokens;
preferences must not become secret storage. Stage 8 adds a local evaluator; Stage 9 adds a focused execution coordinator. There is no generic platform framework. Keep logging configuration at the entry point and never log future authorization
headers or credentials.


## Metadata and local review (Stage 3)

`preparation.py` owns immutable `YouTubeMetadata`, `Preparation` and `Review` values, pure field
validation, tag counting, and `PreparationStore`. The application service exposes save/check/review
operations through the existing one-worker boundary. No extra scheduler or dependency was added.

The version 1→2 migration rebuilds `videos` inside the existing explicit transaction, preserving
all IDs and prior columns while adding `inspection_revision`. `INTEGER PRIMARY KEY AUTOINCREMENT`
prevents deleted IDs being reused: a delayed save from another process must not land on a new video.
It adds `preparations`, keyed by `(video_id, platform)`, with a cascading foreign key. YouTube alone
is implemented; its own fields live in a JSON payload, so future platforms need not inherit those
fields. Shared columns contain revision, modification time, reviewed revision, reviewed media binding
and review time. `PRAGMA foreign_keys=ON` is enabled on every connection. A failure rolls back the
entire table rebuild and metadata-table creation. Future schema versions remain refused.

Metadata records are created on the first save; an unsaved entry presents blank fields, explicit
Not specified declarations and Private privacy. Draft saves permit missing/invalid platform fields.
They use BEGIN IMMEDIATE and an expected revision to refuse stale writes, then increment revision
and clear approval atomically. A no-op save preserves its revision and approval. Successful/failed
inspection refresh increments inspection revision and clears approval in the same transaction.
Removal cascades metadata deletion but never changes the source.

Review completion is a separate explicit transaction. It rereads the selected video and saved
metadata, compares expected metadata and inspection revisions, evaluates blocking requirements,
and rechecks a source signature before storing approval. The media binding covers the inspection
revision, stored inspection value, error state and observed filesystem identity/size/mtime/ctime.
This is not a whole-file content hash. Source checks still require size/mtime to match inspection.
Detected missing/changed sources clear approval durably, so restoring a file does not revive it.
There is no continuous filesystem watcher or claim that an external file can never change after
checking; selection, entering Review, Recheck and review completion perform explicit checks.

The Videos page owns one editor bound to the selected stable video ID. During operations controls
are disabled; worker results include the operation identity. A recheck preserves unsaved fields
and their original baseline revision, rather than adopting another writer's newer revision.
This prevents stale values from silently overwriting concurrent edits on the next save.

The editor is separate from persistence. Its three tabs retain video details, present fields and
counters, and show an explicit review summary. The editor's forward button opens Review without
losing edits; unsaved metadata blocks approval. Tab sizing follows the active page to avoid forcing
short information/review panels to occupy the tall editor's empty space. Plain-text widgets render
filenames, metadata and error messages without treating user content as HTML.

Navigation, selection, refresh, reload, import, removal and close use one Save/Discard/Cancel guard.
Save captures video ID, values and baseline revision, then defers the requested continuation until
write success. On failure, no continuation runs and edits remain. Pending navigation/close during
a save waits for it; unrelated active checks must finish before resolving unsaved changes. The main
window filters application Quit events through the same guard, in addition to the worker shutdown
fallback. Draft state is only cleared by confirmed save or explicit discard.

Blocking review checks include missing/changed source, failed/invalid inspection, Shorts dimensions
or duration, invalid metadata limits and unspecified declarations. Silent audio and optional unknown
metadata remain warnings. Local Review Complete is never labeled as uploaded, approved by YouTube,
or copyright-cleared. Rules are based on official documentation dated 2026-10-15 and can be changed
without editing UI widgets.


## Secure account connection (Stage 4)

`app.build` composes `AccountService(AccountRepository(library), KeychainStore())` alongside the
video service. Accounts uses its own `AccountWorker` QThread; network, JSON configuration loading,
SQLite and Keychain operations never run in GUI slots. The window closes only after library, account and upload workers
finish safe cancellation. Accounts actions are disabled during an operation; navigation stays usable.
An existing library triggers one background restore, even when Dashboard is initially visible.

`auth.py` validates a Desktop client's installed configuration, fixes Google endpoints, and uses
`google-auth-oauthlib.Flow` to generate PKCE S256 and exchange the code. The listener binds only
127.0.0.1:0, validates path/Host/exact single state with constant-time comparison, rejects duplicate
or ambiguous response fields, and suppresses HTTP access logs. State comes from `secrets`; PKCE
cryptography is supplied by the maintained Google library. Callback sockets have read timeouts;
listener lifetime is bounded and cleanup uses a context manager. Browser launch is explicit Connect.
Initial Connect requests only youtube.readonly; explicit Stage 5 consent adds youtube.upload. Tokens travel in POST bodies or Authorization headers, never
URL query parameters. Redirects are disabled on token, API, revocation and thumbnail HTTP requests.

`credentials.py` defines the small credential-store protocol and explicitly instantiates keyring's
macOS backend. Automatic backend discovery and plaintext fallback are prohibited. Other operating
systems can still use the local library, but account storage is not implemented there. Tokens have
redacted object representations. Account boundaries return fixed, sanitized errors without SDK
exception text or tracebacks; OAuth wire logging is suppressed. Keychain errors preserve existing
data and report storage failure. System Keychain prompts remain controlled by macOS.

Schema 2→3 adds `account_config` (platform and minimal public client JSON) and `account_connection`
(platform, random credential key, channel JSON and last verification timestamp), inside the same
transaction as prior migrations. Existing videos, preparations and reviewed revisions are untouched.
Channel identity and credential identity are separate. The account row contains no token. The
original downloaded client JSON path and unused fields are not stored. No database reset is allowed.

A per-library OS file lock serializes account operations across processes. Authorization remains
in memory until the user confirms a returned channel ID; it expires after five minutes. Confirmation
checks that the client configuration has not changed, stores a new random Keychain entry, then
commits its account row. A failed DB write attempts to delete that new entry. SQLite and Keychain
cannot share a transaction: a crash between these steps can leave an unreferenced Keychain item;
no success is claimed before both writes. A Keychain cleanup failure is reported rather than hidden.
Replacement requires explicit local disconnect first. No multi-account framework is introduced.

Access expiry triggers refresh; a rotated refresh token is securely saved before further requests,
even if cancellation arrived during the successful refresh. A channel HTTP 401 permits at most one
refresh-and-retry. Failed refresh storage can require reconnection if Google rotated the credential.
Transient network failures retain authorization. Disconnect deletes the referenced Keychain entry
before clearing its nonsensitive row. If the SQLite clear fails, the remaining row requires reconnect
on the next check. Optional Google revocation is separate and only a confirmed response is success.

`youtube.py` requests only channels.list with mine=true and part=snippet. Pagination is bounded.
No channel is an actionable error; multiple channels require explicit selection, and even a single
new channel needs confirmation. Refresh must return the same stable channel ID; unexpected results
never silently replace it. Brand Account choice happens in Google's browser UI. Quota exhaustion,
permission failures and transient failures have distinct messages. Only transient channel failures
get up to three attempts with cancellable bounded backoff; there is no continuous API polling.

Optional thumbnails use only two allowed Google image hosts over HTTPS with no authorization
header or redirects. Downloads are capped at 256 KB; Qt validates dimensions before scaled decode.
Images are not persisted. Failure to obtain a thumbnail does not establish or invalidate authorization.

Accounts distinguishes disconnected, waiting/verification pending, recently verified, temporarily
offline and reconnect-required states. Cached identity and last verification time are labeled.
A five-minute UI freshness timer clears the recently-verified presentation without making another
request. Local video preparation and its approval remain independent of account connection.


## Manual Private and Public uploads (Stages 5–7)

`app.build` composes `UploadService(UploadStore(library), accounts, KeychainSessions(), UploadHTTP())`.
Production uses real HTTPS requests. Tests inject controlled transports; there is no simulation toggle
in the application. `UploadWorker` owns one operation off the GUI thread. Queued progress/results
update the Videos Upload tab, which also remains accessible for history when no library row exists.
The worker is retained until cancellation finishes, including application Quit. Navigation remains usable.

`auth.py` uses the existing official Google Flow with both read-only and upload scopes for explicit
additional consent, retaining PKCE/state/loopback protections. Installed apps do not use incremental
authorization. Grants must carry both scopes and resolve to exactly the already confirmed channel.
A missing refresh token never replaces an existing token blindly: the old renewable grant must itself
prove both scopes. Scope information is stored with tokens in Keychain; legacy entries remain read-only.
A failed upgrade preserves previous credentials. No broad YouTube management scope is requested.

Schema 3→4 adds `upload_attempts`, without rebuilding existing tables. Its JSON snapshot records
approved metadata, review revision, original path/stat, size, SHA-256, chunk size and destination title.
Columns retain intended channel/library IDs, state, acknowledged bytes, returned ID/privacy/processing,
UTC timestamps, sanitized recovery information and an explicit duplicate-risk release marker. History
has no cascading video foreign key: removing a library entry cannot erase a remote attempt. A partial
unique index prohibits concurrent unresolved attempts per channel; service preflight also blocks
conflicting unfinished work and repeated video/content hashes. All migrations are transactional.

A separate per-library upload FileLock serializes transfer and history recovery across processes.
Account operations use their existing lock, held through transfer, so disconnect or reauthorization
cannot replace credentials mid-request. No global scheduler or multi-platform framework was added.
No new dependencies or lockfile changes were needed beyond Stage 4.

Preparation performs live channel/scope checks and review validation, hashes the source, and saves
Awaiting Confirmation before opening a final modal dialog. Navigation never constitutes consent.
Confirmation revalidates the source hash, stat identity and revision. Preparing is committed before
session creation; the returned session URL must be saved in Keychain before any media chunk is sent.
Hashing uses a retained file descriptor; before each chunk, descriptor/path stat and saved review are
checked again. Changed content stops transfer and retains the immutable original snapshot.

`upload_http.py` implements HTTPS videos.insert with part=snippet,status, notifySubscribers=false,
application/octet-stream, explicit approved Private or Public privacy, category 22, declarations and saved metadata.
The adapter rejects missing, unsupported or inconsistent intent/payload privacy and publishAt. Session URLs are accepted only at the
fixed Google HTTPS upload endpoint, with redirects disabled. Authorization stays in headers.
Session capabilities never enter SQLite, exception messages or logs. They use a separate macOS
Keychain namespace keyed by attempt ID, and remain stored for explicit recovery (no expiry sweeper).

Chunks default to 2 MiB, a multiple of 256 KiB, except the final remainder. The snapshot fixes chunk
size across restarts. Every initial/resumed transfer first queries session status with an empty PUT.
308 Range determines the next offset; percentages reflect only acknowledged bytes. A failed chunk
is followed by a status query before resending. Retries are bounded (three retries without progress),
exponential and cancellable; Retry-After is honored. Cooldowns over 30 seconds require later explicit
resume rather than sleeping indefinitely or shortening the server delay. One refresh/retry handles
401; quota and channel-limit failures stop. HTTP connect/read timeouts are 5/30 seconds.

An uncertain initialization response is never retried automatically. An explicit 401 permits one
reauthorized initialization retry because it rejected authentication. A lost final response retains
its session for querying. A 404/410 or unavailable capability needs reconciliation, never a fresh
session. Stop may race with a successful final response: confirmed completion is retained even if
cancellation arrived. Restart converts active work to Interrupted without any upload request.

A successful transfer must return a syntactically valid actual video ID. Transfer Complete is saved
before privacy/channel verification. Every Public completion requires an independent authorized videos.list check before Public Verified.
Private missing fields also trigger that check; explicit
Verify Remote Result can check it again. Processing and Uploaded Private are distinct; failed processing
is recorded. Unknown processing is stated. Visibility mismatches or wrong-channel results become critical
Needs Reconciliation without remote mutation. Failed re-verification marks cached information as
unverified. Any attempt with a returned ID cannot send more media. Duplicate-risk acknowledgement
preserves history and requires a separate new review/confirmation before any new upload.

SQLite intent, Keychain capabilities and remote HTTP cannot form one atomic transaction. A crash after
session creation but before secure storage can leave an unrecoverable remote session; a crash after
remote completion but before SQLite persistence needs the saved session to recover the returned ID.
If that session has expired, manual Studio inspection and deliberate duplicate-risk acknowledgement
are required. Neither locking nor transactions provide exactly-once remote creation. Source restoration,
re-review after changes, renewed grants and actual Google session lifetime remain external constraints.


## Stage 6 integration verification completed

The existing architecture and schema remain unchanged. Final confirmation now also shows the exact
source path and byte size. Controlled regression/native checks are separate from live verification.
Stage 6 requires user-driven OAuth and final upload consent, an independent videos.list result check,
and a subsequent restart without another upload. No automatic live-test entry point was added.
One real user-confirmed Private upload passed independent channel/privacy/processing verification
and subsequent application restart checks. Google Desktop downloads using the exact official legacy
authorization URI are accepted and normalized to the fixed modern endpoint; other URLs remain rejected.
See [Stage 6 verification](stage-6-verification.md) for evidence and limitations.

## Application-owned scheduling decision

The [roadmap](roadmap.md) specifies direct Public upload initiation at the approved time, without
YouTube `publishAt`. Stage 9 implements this while the desktop application runs. Stage 8 plan approval
alone is insufficient: a separate activation binds automatic execution to exact content and timing.
Background execution remains deferred until a separate explicit request.

## Stage 7 explicit Public intent

The existing prepare action explicitly chooses Private; prepare_public additionally requires the
saved reviewed preference Public. Each new immutable snapshot stores requested_privacy and the
matching payload status. Separate confirm/confirm_public operations prevent generic Private consent
from authorizing Public. Both reuse the same source hashing, locks, secure session store, chunk
transport, cancellation, bounded retry and recovery implementation. No new dependency or scope.

Schema 4→5 transactionally adds verification_status and verified_at and extends the existing partial
index for Public Verified. Existing column values and snapshots are preserved exactly. Historical
snapshots are recognized as Private only from their explicit private payload; missing visibility is
never inferred. New attempts begin unverified. Old timestamps are not invented.

Public insert responses cannot establish Public Verified. The service saves the returned ID before
checking videos.list for that same ID, channel and Public privacy. Visibility discrepancies remain
Needs Reconciliation; processing pending/failure remains distinct. Successful remote observations
record their timestamp; failed later checks mark cached values unverified and retain the last
observation time. No remote update/delete method exists. A saved ID prevents further byte transfer.

Stage 7 controlled tests and native workflows are documented in [verification](stage-7-verification.md).
A user-confirmed Public upload subsequently passed independent live verification and restart checks.
See the Stage 7 report for exact observations.


## Local planning foundation (Stage 8, retained in Stage 9)

`ScheduleService` depends on Library, preparation validation and read-only source helpers; it has no
HTTP client, credential store or uploader reference. `source.py` holds the existing fingerprint/signature
functions shared with the manual uploader. Moving these helpers did not introduce another uploader.
The UI controller owns one ScheduleWorker and shares its authoritative job results across Queue and
Calendar. Real application startup activates local evaluation; a 30-second timer and explicit reload
refresh it. Workers are cancellable and retained until shutdown finishes. No background service exists.

Schema 5→6 transactionally adds scheduled_jobs, schedule_events and partial unique indexes for active
(platform,channel,video) and (platform,channel,SHA-256). A plan stores nonsecret source/review/metadata,
Public intent, UTC instant, original local time/zone/fold, stable IDs, timestamps, revision and state.
There is deliberately no cascading video foreign key: deletion preserves the plan and produces Needs
Attention. schedule_events has a foreign key to the retained job and records each revision, preserving
previous approval snapshots. Schema 6 originally enforced `execution_enabled=0`; schema 7 rebuilds
that table transactionally and retains all old values/events. Separate consent is now required.

Preparation hashes off-thread. Approval rehashes, compares the entire proposal, then obtains BEGIN
IMMEDIATE and rereads source stat, review, metadata, channel, duplicates and expected job revision.
The final future-time check happens inside that transaction. A competing cancellation, edit, upload
attempt or schedule cannot silently win a stale approval. Manual upload creation also checks active
plans in its own write transaction. Exceptions roll back the record and revision event together.
Source bytes can still change after approval: periodic stat/review checks detect ordinary changes;
the Stage 9 upload boundary rehashes immediately before transfer. Planning itself does not execute.

`zoneinfo` resolves IANA local times by checking both fold values and round-tripping through UTC.
No valid round-trip means a nonexistent time; two different instants require explicit occurrence
selection. UTC is authoritative after approval, and the original local time, zone and fold remain
stored. OS time-zone changes do not rewrite plans. Existing approvals retain their stored UTC even
if a future IANA rules update changes conversion; rescheduling uses the currently installed rules.

States: Draft has no approval; Scheduled has explicit local-plan approval; Due begins at the exact
UTC instant and lasts less than 60 seconds; Missed begins at 60 seconds. None denotes publication.
Needs Attention is sticky until explicit reapproval, and Cancelled is terminal. Due/Missed never
revert to Scheduled after clock rollback. Rescheduling an unclaimed, noncancelled plan requires renewed
approval and a future time; modifying an approved plan as a draft is rejected. Repeated evaluation
writes no event when state/reason are unchanged. An interrupted transaction leaves the prior revision.

Evaluation checks local source/review/account records and upload conflicts; it neither polls Google
nor checks Keychain. An offline stored channel identity may be used to plan, with UI disclosure that
live authorization is a separate future execution gate. Account removal or an unexpected ID causes
Needs Attention without redirecting or deleting the plan. Drafts are revalidated on approval.

Calendar uses QCalendarWidget with marked dates and state-bearing day rows. Dates are grouped by each
job's approved local wall date, explicitly labeled, so different-zone jobs are not silently converted
to the system zone. Queue sorting uses UTC. Both views share one controller and refresh from SQLite;
other app instances become visible on navigation/reload/timer. Modal editing pauses periodic evaluation,
retains unsaved changes on errors and prevents a stale worker result from becoming approval.

No dependencies were added. See [Stage 8 verification](stage-8-verification.md) for the planning baseline.


## Automatic execution (Stage 9)

`AutomaticPublisher` coordinates `ScheduleService` and the existing `UploadService`. It implements no
HTTP transport. `ExecutionController` owns an ExecutionWorker for startup/dispatch and a separate
control worker so Pause can commit while a network request is active. Signals carry real persisted
states and acknowledged bytes to Dashboard, Queue, Calendar and upload history. All disk, hashing,
Keychain and network work stays off the GUI thread. The real entry point starts dispatch independently
of navigation; close/Quit waits for these workers as well as the existing workers.

Schema 6→7, in `execution_schema.py`, runs inside Library's BEGIN IMMEDIATE transaction. It rebuilds
scheduled_jobs and its foreign-keyed events without changing original values, removes the always-zero
execution CHECK and recreates unique active-plan indexes. `publishing_control` defaults paused with
three claims per UTC day (user range 1–10). `execution_consent` stores job ID, approved plan revision,
canonical snapshot/time binding, timestamp and exact authorization wording. A trigger rejects enabling
without consent. `scheduled_executions` has unique job and upload-attempt foreign keys, consent binding,
claim/start-intent timestamps, phase and sanitized error. No token or session capability is added.

Activation rehashes off-thread, compares the complete proposal, then rechecks revision, source stat,
review, channel and conflicts under a short write transaction. Rescheduling clears activation/consent;
prior approval remains in schedule_events. Content/time bindings exclude mutable evaluator state and
its revision increments, but include the immutable approved plan revision in the consent/execution link.
An updated stored channel title can conservatively require reapproval even if its stable ID matches.

Dispatch obtains the same upload lock then account lock used by manual operations. In one short
transaction it checks global permission, consent, due window, budget, local review/source/channel and
conflicts; inserts a normal Awaiting Confirmation upload snapshot and its unique execution; and records
the job revision. It then calls the narrowly scoped `UploadService.execute('scheduled', execution_id)`.
That boundary independently resolves the durable claim, checks exact Public payload/source/channel
and consent, and rejects consumed identities. It does not call a GUI/manual confirmation action.

The existing transfer method performs actual channel/scopes/refresh checks, source SHA-256 and current
review checks. Immediately before initial POST, the scheduled boundary rechecks consent, local content,
global pause and timing, and commits the start-intent phase. A pause ordered after this commit does not
undo the already-started operation. SQLite and YouTube cannot make this transition atomic: a crash in
this small gap remains conservative and never authorizes another initialization for that execution.
An explicit HTTP 401 rejection retains the existing engine's one refresh/retry allowance. Uncertain
initialization responses never create another session. All later chunks/retries/Keychain session storage,
independent videos.list verification and privacy/processing handling use the existing engine unchanged.

One upload process lock serializes transfers across this library, a stronger limit than one automatic
upload per channel. DB uniqueness and duplicate checks also protect competing claims, tested with
separate processes. An unavailable lock leaves unclaimed work eligible only within its 60-second
window. Other unresolved attempts or local validation failures become Needs Attention. Known quota/
channel-limit errors observed on the current UTC date block another automatic claim. This local
observation is not a quota monitor; actual API restrictions remain authoritative.

A single-shot timer targets the earliest upcoming instant, capped by a five-second safety interval.
It is independent of the 30-second planning/display refresh. Dispatch does not run in the editor's
worker. Preflight must finish within the 60-second window. Startup and global enabling mark past
unstarted activated jobs Missed immediately, including jobs less than 60 seconds overdue. Overdue and
clock rollback handling never rewrite timestamps. There is no catch-up loop, automatic session resume,
background daemon, wake-from-sleep capability or real-time scheduling guarantee.

Planning state remains separate from linked upload state. Claimed plans are excluded from local
re-evaluation; rows display their upload outcome, and details preserve plan timing/revision and execution
identity. Completed is a presentation derived only from Public Verified plus processed/succeeded.
Returned IDs prevent further media transfer even when verification fails. Restart retains every link
and interrupts orphaned active/claimed records without sending. Resume/Verify/Resolve use the original
Upload History actions. Only after explicit release can the local schedule be closed; its execution
and history remain immutable and that job ID can never initiate again. A new job needs fresh approval.

See [Stage 9 verification](stage-9-verification.md) for actual tests and live limitations.


## Background execution (Stage 10)

`background.py` manages a per-user LaunchAgent through bounded argument-array `launchctl` calls.
`worker_entry.py` is the absolute script entry; `worker.py` composes the existing account, Keychain,
schedule and upload services without importing Qt. No production simulation switch exists. The
native test worker injects controlled providers in test code only.

Schema 7→8 adds `background_config`, `background_owner`, `background_consent` and `execution_owner`.
Existing tables/columns remain unchanged. Defaults disable the service and login startup. Job scope
is separate from the existing execution consent, bound to the same snapshot/revision and explicitly
selected in the activation dialog. Reapproval without the checkbox removes background scope. Service
consent is separate from global publishing. None of these records contain credentials.

`Ownership` uses the library's `.dispatcher.lock` OS file lock. A foreground dispatcher holds it through
startup reconciliation or an entire scheduled transfer. The background worker holds it for its entire
lifetime and waits for an existing foreground owner to release it. SQLite records a random process
identity, owner kind, PID and heartbeat; each claimed execution records that identity. The uploader
checks it against current ownership and its in-process identity before transfer and initial POST.
Background claims additionally require matching background job consent and service permission.
The OS lock, not heartbeat age, authorizes acquisition. Upload/account locks and unique execution
claims remain authoritative. The GUI skips reconciliation and execution while background is requested.

A two-second monitor updates health and observes Stop during a blocking request; cancellation remains
cooperative. Dispatch waits until the next future instant, capped at five seconds. SIGTERM cancels work,
waits for request return, records normal shutdown and releases ownership. No request uses an open
SQLite write transaction across network I/O. Worker startup conservatively marks past unclaimed work
Missed and orphaned attempts Interrupted. Crash restart is explicit (`KeepAlive=false`), with no new
session for a previously claimed job. Unknown remote outcomes retain the existing reconciliation rules.

Configuration is atomically written with mode 0600 into an owned directory. Stable label ownership,
expected command/data path and existing file permissions are checked before lifecycle operations.
Lifecycle operations are serialized by a per-user label lock. Service control has no TCP listener and
uses no shell or arbitrary command payload. Session-only registration uses a workspace plist; explicit
login preference additionally creates the standard user LaunchAgents plist. Enable writes consent and
configuration; Start separately requests execution and bootstraps/kickstarts. Stop clears that request,
sends SIGTERM, polls for a bounded interval, then bootouts after the PID exits. Timeout is reported as
still stopping; there is no forced ownership theft. Disable removes login startup; Remove also deletes
the managed workspace plist. Data and Keychain entries are preserved.

Settings uses a QThread for controls and five-second status observations. OS PID, loaded path, owner PID,
initialization state and heartbeat freshness determine reported status. Unknown OS status is an error,
not proof of stopping. Dashboard shares these observations; Queue/Calendar read durable upload progress.
Closing the GUI stops its own workers, not an authorized LaunchAgent. Stop Background Publishing and
Quit suppresses new foreground ticks, waits for service stop and then closes. Pause remains independent.

The production worker uses the same explicit macOS Keychain backend as the desktop. Actual test
LaunchAgents accessed isolated synthetic credentials. This does not establish every locked-Keychain,
permission-dialog or signed-runtime case. Logs contain IDs, states and acknowledged byte counts, with
1 MB rotation and three backups. Exceptions are reported as sanitized errors rather than SDK bodies.
For a future app bundle, evaluate SMAppService, stable embedded helpers, signing and the user's
Background Items approval. The current checkout is neither packaged nor distribution-verified.


## Stage 11 presentation refinement

The service composition, schema 8 and all approval/upload/dispatcher boundaries are unchanged.
`ui/components.py` supplies a keyboard-accessible Disclosure and primary-action styling.
`ui/preview.py` uses existing PySide6 QtMultimedia: an opt-in QMediaPlayer, muted QAudioOutput
and QVideoSink rendered into a Qt-owned label. This keeps real decoded frames visible in native
widget screenshots. It accepts only local library paths, performs no upload or source mutation,
and releases playback on navigation/hide/close. Preview errors do not alter inspection results.

Videos retains the five existing panels and their guards, with shorter labels and explicit forward
navigation. Expanded technical content remains accessible; hiding it does not discard state.
Dashboard and Queue presentation derive from existing service results. Background action feedback
is tied to worker results, not assumed success on click. Service enablement and per-job/global
consent remain separate. No dependencies, migrations, new API fields or headless imports were added.


## Library discovery and preview (Stage 12)

`library_view.py` is a read-only presentation projection over existing videos, preparations,
schedule jobs/execution links and upload attempts. The existing library worker reads it inside
one SQLite read transaction. It does not initialize a new schema, stat every source, read Keychain,
evaluate/modify schedules or contact YouTube. Search/sort/filter operate on this in-memory snapshot;
NFKC plus casefold handles Unicode. Stable video IDs break ordering ties; unknown duration sorts last.

Review labels describe saved review, not a new source or remote check. Future plans exclude claimed,
cancelled, draft and past jobs. Results count returned IDs across all attempts, including uncertain
verification or failed processing. They never mean successful publication. Selecting a video retains
the existing real source/review checks. Expanding Library, returning to Videos, and explicit reload
refresh the projection; saves/reviews refresh it in the same worker result.

Filtering never selects a replacement or discards an editor. The existing Save/Discard/Cancel guard
still handles actual selection/reload/navigation. Reload rereads the selected ID on its worker if
still present. List scroll position is retained where its new range permits. No preference schema
was added: search, sort and selection survive in-session navigation, not application restarts.

Preview remains opt-in Qt multimedia playback of a local URL, muted on source change/release.
Loading, end, missing-file and unsupported-format messages are separate from inspection results.
Seeking is enabled only when Qt reports it supported. No thumbnail cache, fullscreen player,
source conversion or second decoder was introduced.

## Intentional navigation and presentation (Stage 13)

Videos owns an in-memory `library` / `editor` / `history` presentation mode independent of the
retained selected Video and metadata buffer. Main navigation always enters library mode. Continue
Editing rechecks a saved source; an unsaved parked buffer is reopened intact. Back and destructive
context changes retain the existing Save/Discard/Cancel guard. A monotonically increasing UI context
revision prevents a completed import/selection from reopening the editor after a later navigation.
Only one library worker runs at a time. Refreshes do not choose a new mode. Upload context is disabled
outside editor mode; history is an explicit read/recovery destination. Different IDs and imports
start at Details; continuing the same session restores its editing step. A missing database entry
returns to a useful library state. Navigation state is not persisted across application restarts.

Multi-file import is an orchestration action in the existing library worker, calling the unchanged
local import service serially. Individual failures are disclosed, successful imports persist, and
the first success is selected. Save and review chains navigation only after a successful metadata
save. Final publishing and automatic activation confirmations retain exact destination identity and
existing consent. No production publishing, credential, schema or scheduling logic changes.

`ui/formatting.py` centralizes display-only timestamps, durations, sizes and upload status labels.
Aware UTC instants are converted using ZoneInfo and the approved plan zone (system zone for other
activity). QLocale supplies translated month/day names and the clock convention. Named months and
years avoid ambiguous numeric dates; nonzero seconds are preserved. Naive or invalid timestamps
are explicitly unavailable, never guessed. Schedule inputs retain seconds and serialize unchanged
ISO local values to the existing DST resolver. Diagnostic disclosures retain exact machine values.

## Watched folders and content identity (Stage 14)

`watching.py` owns local discovery independently of Qt. `WatchService` reuses `LibraryService`'s
Inspector and `Library.save_in_transaction`; it does not call uploader, account or scheduler
mutations. `watch_schema.py` adds four tables in the existing migration transaction (schema 9):
`watch_folders` (consent, root dev/inode, configuration revisions), `watch_files` (observations,
quiet/retry state, outcomes), `content_registry` (unique SHA-256), and `content_videos` (legacy IDs
and source signatures). Content links deliberately survive library removal and retain tombstones.
No old rows are merged. A failed migration rolls back to schema 8.

The service checks nonrecursive, explicitly approved roots, rejects symlink locations/files and
filesystem roots, and pins root identity. A replaced directory needs new consent. Candidate identity
is size, nanosecond mtime/ctime, device and inode. At least two matching observations and a default
15-second quiet interval precede fingerprinting and actual FFprobe inspection. Empty/changing files
wait; stable malformed media retries with exponential 30-second-to-one-hour backoff. Changed
signatures reset evaluation. O_NOFOLLOW protects the final hash-open component. Root and file
identity are rechecked after inspection and before commit. These checks are conservative heuristics,
not a filesystem snapshot or proof that a writer has closed; atomic temporary-file handoff is advised.

A per-database file lock coordinates GUI and headless scanners, including separate processes.
Hashing and inspection occur outside SQLite transactions. A final BEGIN IMMEDIATE rechecks folder
revision/permission, current library IDs, history and source identity before the shared library save
and registry update commit together. Manual library files are indexed lazily, with cached signatures;
unknown unavailable legacy sources block intake. Historical upload snapshots and scheduled source
hashes preserve channel-specific evidence. No history outcome grants new publication eligibility.
Final evidence checking occurs under the write lock. Existing uploader preflight remains unchanged.

`ui/watching.py` hosts a cancellable QThread, native folder chooser and explicit consent dialog.
QFileSystemWatcher events debounce for one second. Ten-second scans reconcile misses and reattach
watches after disappearance. Monitoring starts at app open. A separate optional thread in the existing
authorized headless worker uses this exact scanner and only folders with explicit background permission.
No extra service or publishing consent is created. launchd's restricted PATH is handled with standard
Homebrew FFprobe lookup. Its discovery thread is cancelled/joined during shutdown; upload ownership
and dispatch rules stay intact. Pausing publishing does not pause local discovery; folder controls do.

Discovery refreshes the existing library projection without changing mode, selection, workflow step,
search controls or unsaved editor text. Ordinary results omit hashes and internal IDs; diagnostics
retain precise source and per-channel evidence. Counts describe current file states, not lifetime
import totals. Change Folder requires pausing the old location and approving the new one separately.

Filesystem decisions follow [Qt QFileSystemWatcher documentation](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QFileSystemWatcher.html)
and [Apple FSEvents reconciliation guidance](https://developer.apple.com/library/archive/documentation/Darwin/Conceptual/FSEvents_ProgGuide/UsingtheFSEventsFramework/UsingtheFSEventsFramework.html):
notifications may coalesce, watches can disappear and OS resource limits apply, so scanning is the
authoritative reconciliation. Headless discovery polls; it does not implement a second notification backend.

## Recurring Public queues (Stage 15)

Schema 10 additively creates recurring_plans, recurring_items, recurring_slots and recurring_events
in the existing initialization transaction. Migration creates no plan and grants no consent.
Existing schema-9 data remains intact; older headless workers must wait for GUI migration.
Verification used isolated databases; the genuine databases were not migrated.

cadence.py computes bounded future UTC instants from an IANA-zone wall-clock rule. Intervals of
1–24 whole hours anchor at the selected start on each selected weekday; daily mode contributes
one start. Same-day windows include the end. DST gaps are skipped, overlaps use the first
occurrence. Rule strings are never inferred from formatted UI dates. A monotonic persisted
watermark and unique (plan_id, instant) prevent clock rollback/restart from replaying opportunities.
Downtime is represented by one bounded skipped range through the current check, not millions of
individual historical slots. Future calendar entries are projections, not reservations.

recurring.py implements finite batch preview/approval, immutable rule/channel/background/cap
bindings, ordered queue mutations and allocation. Approval reuses ScheduleService preview and
source fingerprinting outside SQLite's write transaction, then rechecks saved review, source
identity, account, conflicts and revision inside BEGIN IMMEDIATE. Cancellation before commit
rolls back the whole batch. Explicit append approval is independent; no incoming file auto-enrolls.
Rule changes require a new plan. Queue order is explicit and retained; optimistic revisions prevent
lost edits. Removed items are tombstones and cannot remove source bytes or upload history.

The existing AutomaticPublisher calls the recurring allocator inside its claim transaction, under
its existing publisher/upload/account locks. One valid item becomes an ordinary immutable Job,
execution consent, optional background consent, unique slot and existing upload attempt/claim
atomically. An individually due job has priority. No duplicate dispatcher, transport or daemon is
introduced. Current global and approved caps, known quota/channel restrictions, active transfers
and uncertain history gate allocation. Invalid source/review/account snapshots need attention;
the next valid approved item may use the same slot. Failed or started items never auto-requeue.

Shared conflicts now include recurring channel/content reservations for manual and individual
schedule paths. SHA-256 identity is authoritative; the Stage 14 content registry retains approved
content links. Existing upload and schedule snapshots provide channel-specific history, including
uncertain outcomes. Recurring approval conservatively rejects previous noncancelled upload attempts,
even explicitly released failures; there is no automatic repost override. Intake alone never
authorizes publication. Source bytes and credentials are independently revalidated at the existing
upload boundary. Each linked recurring job also checks its persisted binding and enabled plan
before new session initiation. A pause does not erase an already initiated session.

ui/recurring.py performs database/fingerprint work in a cancellable worker. Queue, Calendar and
Dashboard use stored projections; the controller refreshes every ten seconds and receives existing
dispatcher updates during transfer. A read transaction keeps each recurring status snapshot
consistent. Main-thread calendar projection is bounded to eight future opportunities per plan.
Acknowledged bytes are distinct from completed transfer, verified Public visibility and processing
completion. Exact fingerprints, records and skipped opportunity reasons remain in disclosures.
UI shutdown joins/cancels the new worker through the existing application lifecycle.

Research: [YouTube developer policies](https://developers.google.com/youtube/terms/developer-policies),
[required minimum functionality](https://developers.google.com/youtube/terms/required-minimum-functionality)
and [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert).
The design retains explicit user control over channel, visibility and exact metadata and authorizes
only a finite reviewed batch. It uses Public insertion at the approved opportunity, no publishAt
field or new unsupported restriction API. Controlled verification is not a live YouTube policy audit.

## Authorized future-folder profiles (Stage 16)

Schema 11 additively stores automation_profiles, automation_decisions and automation_events. Migration
creates no authorization. Each approved revision binds a watched-folder identity and consent, exact
channel, Public metadata rules/declarations, eligibility, cadence, cap and background permission to
one recurring plan. Existing files are excluded at approval by name and source identity. Editing
revokes the previous approval and requires fresh consent; history and old snapshots remain intact.

The existing WatchService scanner invokes AutomationService after ordinary local discovery. It
reconciles durable watch records, verifies stable SHA-256/source/folder scope and current permission,
then atomically persists exact per-video provenance and a unique ordinary recurring item. It creates
no upload attempt. Waiting enrollment is bounded to 100 items per plan; the existing allocator owns
posting opportunities, budgets, missed-slot skipping, job creation and execution claims.

Review mode requires existing individual human review. Automatic mode uses separately authorized
profile provenance without writing Review Complete. ScheduleService accepts that provenance only
explicitly through the recurring snapshot. The uploader requires the matching persisted execution,
rechecks profile activation/revision/source/metadata and retains existing fingerprint, channel,
credential, ownership and remote-result checks. Revocation blocks new sessions and may interrupt
remaining chunks; existing uncertain/session history is retained rather than automatically retried.
Manual publishing and finite Stage 15 approvals retain their individual-review requirements.

Production headless integration uses the existing worker/scanner and imports no QtWidgets. Both
folder background permission and profile background permission are required. No second daemon or
production service installation is introduced. Automatic Posting UI work runs on cancellable workers;
Dashboard, Queue, Calendar and history reuse stored projections. Navigation scrolls at minimum size.

The final full suite passed 699 tests with no failures or skips. Native Cocoa prepare/reopen and
real isolated LaunchAgent acceptance passed with controlled Google services. All 16 prior native
commands and eight additional library/watching/recurring checks passed. Actual OS picker selection
remains unverified because computer use could not attach to the unbundled Python app. See
[Stage 16 verification](stage-16-verification.md) for evidence, policy sources, preservation and known
limitations. No live upload is claimed. Stop after Stage 16.

## Stage 17 acceptance and focused fixes

Review-waiting decisions may bypass hashing only while unchanged and still awaiting human review.
The branch cannot authorize or enroll anything: saved metadata/revision, source/media identity and
current stat signature must match. Human review completion leaves that branch and requires a fresh
SHA-256 before enrollment; upload-time rehashing is unchanged. There is no new schema or durable
fingerprint cache. Native restart and concurrent tests preserve the same boundary.

The profile editor groups its existing fields into Folder, Details, Schedule and Limits pages with
persistent Back/Next/Cancel/Review actions. Final explicit consent is unchanged. The overview now
shows folder discovery, global pause and last known background status alongside profile permission.
The user-operated OS picker gap is closed for cancel/select and separate-process persistence.
708 tests, controlled native foreground/background and prior native regressions pass. Live publishing
remains pending the user's own exact approval and control activation. See
[Stage 17 verification](stage-17-verification.md). Stop after Stage 17; no packaging or Stage 18.

## Interface language (Stage 18)

The application interface is English-only for now. Qt's default `QLocale` follows the macOS
**Region** (not the interface language), so a Chinese Region rendered Qt-generated month/day names
and AM/PM text — in `ui/formatting.timestamp`, `QDateEdit`/`QTimeEdit` and `QCalendarWidget` — in
Chinese. `app.build` now calls `ui.formatting.use_english_interface()`, which keeps an English
Region's locale (en_GB, en_IN…) and otherwise substitutes English for that territory (Qt falls
back to en_US). The original Region is retained only to choose the 12/24-hour clock in
`time_pattern()`. Time-zone conversion is unchanged. User data is never translated. Native macOS
dialogs (file chooser) still follow the macOS language. See [Stage 18 verification](stage-18-verification.md).

## TikTok through Buffer (Stage 19)

TikTok uses Buffer's GraphQL API (`buffer.py`) — never unofficial TikTok endpoints or browser
automation. Buffer accepts only a public video URL, so `hosting.py` streams an independent copy of
the original to the user's Cloudinary account, hashing exactly the bytes sent, and checks the URL
anonymously before Buffer is contacted. `tiktok.py` owns the state machine (`tiktok_posts`):
hosting → "Sending to Buffer" (durable intent) → Buffer status (`scheduled`/`sending`/`sent`/
`error`; `schedulingType` must stay `automatic`). Uncertain creates are reconciled by the unique
hosted URL, never resent. Duplicate key: source SHA-256 + Buffer channel (partial unique index).

Sign-in is a public OAuth client with PKCE. Buffer requires HTTPS redirect URIs even on localhost,
so a user-hosted static relay page forwards the code to a one-shot `127.0.0.1:47633` listener,
with a paste fallback. Rotating refresh tokens are used only under a cross-process file lock with a
Keychain re-read; an uncertain refresh marks the connection "Reconnect required" and the old token
is never reused (Buffer revokes the grant on reuse).

Scheduling: ShortsPublisher chooses the video and exact time (cadence `Rule`); Buffer executes via
`customScheduled` + `dueAt`. Buffer's queue is never used and each TikTok automation keeps at most
one unsent Buffer post, so there are no competing queues and pausing affects at most one post.
TikTok automations (`tiktok_profiles`) have their own consent and run after YouTube's in the
folder scanner, in the desktop and the background worker. See
[Stage 19 verification](stage-19-buffer-tiktok-verification.md).

## Instagram and Facebook through Buffer (Stage 20)

Instagram Reels and Facebook Page Reels reuse the Stage 19 path instead of Meta's Graph API (a
second OAuth stack with an app secret, 60-day tokens and app review). The posting logic moved from
`tiktok.py` into `social.py`: a `Platform` spec (limits, caption rules, payload `metadata`, AI-label
support, excluded Buffer channel types) parameterizes one `BufferPostService`; `tiktok.py` keeps
its Stage 19 names. Each platform has its own tables (`<key>_posts`, `_profiles`, `_decisions`,
`_drafts`; `social_schema.py`, v13), connection row, operation lock, duplicate index (SHA-256 +
Buffer channel) and consent. Instagram posts are `type: reel` with `shouldShareToFeed: true` and
an explicit AI label; Facebook posts are `type: reel` to a Page (Groups and profiles excluded) and
send no AI label because Facebook's API has none.

Shared, with one owner each: the Buffer app client (the `account_config` row Stage 19 created as
`tiktok`), the `ShortsPublisher.TikTok` Keychain namespace, Cloudinary hosting, the Buffer
rate-limit backoff and the `.buffer.lock` token lock. Platforms reuse one Buffer sign-in (one
Keychain key): a key is deleted only when no connection refers to it, "Reconnect required" is set
on every connection sharing the key (so no platform reuses a possibly spent refresh token), and a
fresh sign-in re-points other connections whose channels it can see. The folder scanner reconciles
TikTok, Instagram and Facebook after YouTube, each isolated from the others' failures. In the UI,
`ui/social.py` has one `SocialController` per platform (the TikTok one owns the Buffer-wide
Settings); the video editor's pages 6 and 7 are Instagram and Facebook. See
[Stage 20 verification](stage-20-verification.md).

