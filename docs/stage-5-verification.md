# Stage 5 verification — 2026-10-16

Stage 5 implements **Review Complete → explicit confirmation → Private resumable upload →
verified result/history → safe recovery**. **No real Google authorization or YouTube upload was
performed. Actual privacy was verified only against controlled responses, not a live YouTube video.**
Live authorization, channel identity and upload acceptance remain pending an explicitly approved
interactive test. Stage 6, scheduling, automatic posting and public/unlisted uploads are not implemented.

## Implementation and dependencies

New production files:

- `src/shortspublisher/uploads.py`: upload preflight, immutable source/metadata binding, transfer,
  cancellation, response verification and recovery.
- `src/shortspublisher/upload_http.py`: authenticated resumable wire protocol and error classification.
- `src/shortspublisher/upload_store.py`: nonsecret durable attempts, duplicate checks and recovery states.
- `src/shortspublisher/ui/uploads.py`: final confirmation, worker, acknowledged progress and history.

Modified `auth.py`, `credentials.py`, `accounts.py`, `ui/accounts.py`, `library.py`, `app.py` and
`ui/window.py` to integrate upload permission, secure sessions, migration and worker shutdown.
Updated README and architecture documentation. No new Python dependencies or lockfile changes
were required; the maintained Stage 4 OAuth, Keychain, HTTP and locking packages are reused.

New tests: `tests/test_uploads.py`, `tests/test_uploads_ui.py`, `tests/upload_support.py`,
`tests/native_upload_smoke.py`, and `tests/fixtures/library-v3.sqlite3`. Existing OAuth tests now
exercise the real official SDK exchange with both scopes and PKCE; schema assertions and shell
worker cleanup were updated. The v3 fixture is a backup of the genuine Stage 4 native database,
augmented with genuine Stage 3 video/review records and a clearly synthetic nonsecret connection
reference to test preservation together. It contains no tokens or usable account authorization.

## Authorization and privacy boundaries

Initial Connect still requests only `youtube.readonly`. **Authorize Private Uploads…** explains and
requests `youtube.readonly` plus `youtube.upload` through the existing official system-browser Flow.
It retains PKCE S256, cryptographic state, strict loopback validation, cancellation and timeouts.
No broader Google/YouTube scope is requested. The grant must contain both scopes and return exactly
the already confirmed channel. Denial, incomplete grants and unexpected channels preserve existing
stored authorization. Google login is never automated or embedded.

Installed applications do not support incremental authorization; this is a new explicit consent
request for both scopes. A missing new refresh token does not silently replace a renewable grant.
The existing refresh token is reusable only after refresh confirms both capabilities; a read-only
refresh grant is never presumed to have gained upload permission. Legacy Keychain tokens restore
as read-only. Rotated refresh tokens are persisted before subsequent API requests. OAuth responses
may omit unchanged scope under the protocol; parsing uses the requested/previous expected scope,
while explicit returned scope reductions are rejected.

Before creating a session or resuming, the service checks stored credentials, required scopes and
an actual channel API response against the intended ID. Production uses real Google services;
controlled providers are injected only by test code. There is no production simulation switch.
Account locking prevents disconnect or replacement during transfer.

The payload always includes `privacyStatus=private`, explicit Made for Kids and synthetic disclosure,
saved title/description/tags, and category 22 (People & Blogs, also used by Google's upload example).
Planned Public/Unlisted privacy is ignored and explained in final confirmation. The HTTP adapter
independently rejects nonprivate/omitted privacy and any publishAt. Content type is the documented
`application/octet-stream`. No remote video update/delete endpoint exists.

## Upload protocol and durable recovery

A worker verifies current review and metadata revision, hashes the original with SHA-256 and saves
an immutable snapshot before final confirmation. Hashing and filesystem operations run outside the
GUI thread. Confirmation includes the exact file, media dimensions/duration, metadata, stable channel
ID, declarations, category and Private privacy. Cancel creates no session and sends no video.

After explicit acceptance, the service rechecks source/review and commits Preparing before POST
session creation. It saves the returned URL to macOS Keychain before sending any media. HTTPS session
locations are restricted to Google's expected upload endpoint; redirects are disabled. Tokens use
Authorization headers. Raw transport exceptions, tokens and session URLs are excluded from UI/logs.
Session URLs use `ShortsPublisher.UploadSessions`, keyed by random attempt IDs; SQLite stores the ID,
not the capability. SDK/wire logging remains suppressed.

Production chunks are 2 MiB (256 KiB multiples except the last remainder). Snapshot chunk size is
preserved across restarts. Empty PUT status checks and 308 acknowledged ranges determine offsets.
Progress percentages use only acknowledged bytes. Every resumed transfer queries the same session
before sending. Transient failures permit at most three retries without progress, with exponential
backoff and Retry-After; cooldowns over 30 seconds stop for later explicit resume. Authentication
rejection permits one refresh/retry. Quota exhaustion and channel limits stop retry loops. Network
requests have 5-second connect and 30-second read timeouts.

Before each chunk, file descriptor/path identity and saved review revision are checked. Changed
source bytes or metadata cannot silently replace the approved content. Recovery rehashes the source.
The source is never moved, modified, deleted or transcoded. Stop/close prevents later chunks once
the active request returns; a confirmed final response is retained even if Stop arrived meanwhile.

Schema 3→4 adds `upload_attempts` transactionally and preserves prior tables. Attempts retain source
SHA-256/stat, immutable metadata, review revision, video/channel IDs, progress, state, returned ID,
privacy/processing, timestamps and sanitized recovery errors. History survives library removal.
An upload FileLock, account FileLock and partial unique database index prevent conflicting work.
Identical source hashes/previous video attempts block another same-channel upload until deliberate
resolution. No automatic multi-video loop or publishing queue exists.

Startup/history never resumes a transfer: active records become Interrupted. Missing/expired
sessions and uncertain responses become Needs Reconciliation. A lost final response is reconciled
with the existing session; a lost initialization response never causes a blind new POST. Explicit
HTTP 401 initialization rejection permits one refreshed retry. No returned ID means no invented link.

Transfer Complete is persisted before result verification. Uploaded Private requires a real response
ID plus Private status and the expected channel; missing fields trigger videos.list verification.
Processing and processing failure remain distinct; unknown processing is stated. Nonprivate or wrong
channel results raise a critical discrepancy without changing/deleting remote content. Failed later
verification labels saved information as cached/unverified. Attempts with a returned ID cannot send
more media and instead offer verification.

SQLite, Keychain and YouTube cannot commit atomically. A crash after POST but before secure session
storage can leave an unknown remote outcome. A crash after remote completion but before the local
write can be recovered only while the existing session still supplies its result. Otherwise Studio
inspection and explicit **Acknowledge Outcome / Allow Another Attempt…** are required. This retains
history and does not itself upload/delete anything. Exactly-once remote creation and universal
remote duplicate detection are not claimed.

## Automated verification

Environment: macOS Apple Silicon, Python 3.12.14, PySide6 6.12.0, FFmpeg/FFprobe 9.0.2.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Final result: **211 passed, 0 failed, 0 skipped**. This comprises all 151 prior-stage tests and
60 new Stage 5 tests (49 service/protocol/storage, 10 UI, one additional official OAuth exchange).
The final full run took **39.605 seconds**; its output is retained at
`artifacts/stage5-final/automated-tests.log`. Ruff passed; formatting
reported 46 files already formatted; mypy reported no issues in 22 source files. The suite ran with
permission for local OAuth loopback listeners. No Google service was contacted by upload tests.
After the final cancellation/placeholder wording corrections, the 59 upload tests and eight shell
tests were rerun successfully; their separate logs are retained alongside the full-suite log.

Coverage includes both-scope acquisition, denied/incomplete consent and preservation, renewable
scope handling, channel mismatch, missing authorization, refresh/401, review/source invalidation,
exact private serialization, secure session write failure, exact offsets/308 ranges, bounded
429/5xx/network recovery, Retry-After, quota/limits, permanent errors, cancellation/close/Quit,
restart without automatic requests, expired sessions, lost final/initial responses, duplicate
blocking/release, transactional migration rollback/future rejection, write failures before session
creation and after remote completion, secret-leak assertions, actual returned ID/privacy, processing,
failed verification, live history updates, navigation and history after library removal.

Early checks caught an invalid 12-character synthetic video ID in the fixture, a frozen-view mutation
in a UI test, retry-counter reset behavior, missing persisted chunk size and a stale active-history
label. These were corrected, with regression coverage. Tests were not skipped to obtain a pass.

## Native macOS verification

Final commands (three upload processes, two account processes, plus the real entry point):

```sh
.venv/bin/python tests/native_upload_smoke.py prepare --root artifacts/stage5-final
.venv/bin/python tests/native_upload_smoke.py reopen --root artifacts/stage5-final
.venv/bin/python tests/native_upload_smoke.py history --root artifacts/stage5-final
.venv/bin/python tests/native_accounts_smoke.py prepare --root artifacts/stage5-accounts-regression
.venv/bin/python tests/native_accounts_smoke.py reopen --root artifacts/stage5-accounts-regression
.venv/bin/python -m shortspublisher --smoke-test --data-dir /tmp/publisher-stage5-entry
```

All six processes passed. Entry point closed with code 0 at 18:12:50 local time. Upload processes
used real Cocoa widgets, FFprobe-inspected generated 720×1280 H.264/AAC media, SHA-256, SQLite and
real Keychain services `ShortsPublisher.Stage5.NativeVerification` / `ShortsPublisher.Stage5.NativeSessions`.
Google OAuth/channel/transfer responses were controlled. Received media persisted to a local test
file across processes so recovery checked the actual previously acknowledged offset and byte content.

Prepare verified preserved read-only connection after denied upload consent, successful explicit
scope upgrade, review gating, cancelled confirmation with zero sessions, a displayed final modal
confirmation, active 24% acknowledged progress, navigation, Stop at partial progress, resumed work,
and closing during an active request. Reopen restored partial progress without sending automatically,
injected unavailable-session reconciliation, blocked duplicates, resumed the same restored controlled
session, and persisted the returned synthetic ID with Private privacy. History reopened completed
results without sending bytes and removed all its synthetic token/session Keychain entries.

The account regression separately rechecked connection, channel confirmation, restoration, offline
presentation, refresh, confirmed local removal and authorization cancellation using real isolated
Keychain. No real user credential was read, changed or removed. Harness confirmations and picker
returns are supplied programmatically; manual OS picker or browser consent is not asserted.

Screenshots cover 760×560 and 1180×780, top/bottom, for permission denial/grant, uploading, paused,
reopened partial state, reconciliation, completion and restored history. The confirmation dialog
was also captured with both parent window sizes. Representative captures at both sizes were visually
inspected: readable IDs, accessible scrollable controls, no horizontal page overflow, correct
acknowledged percentages and synchronized history. The later cancellation text clarifies that local
Stop does not delete remote content. Useful nonsecret evidence remains in the two final artifact
folders. Generated source hashes and exact received bytes matched. All 250 retained ShortsStudio
reference hashes matched and its git status remained clean. The original Stage 3 fixture hash also
matched its pre-workflow value.

## Official research and limits

Checked October 15–16, 2026:

- [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert):
  supported upload type, explicit privacy/declarations and current upload quota.
- [Resumable protocol](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol):
  initialization, status queries, chunk alignment, 308 and expired sessions.
- [Upload guide](https://developers.google.com/youtube/v3/guides/uploading_a_video): category example
  and bounded retry guidance.
- [Native OAuth](https://developers.google.com/identity/protocols/oauth2/native-app): installed-app
  consent, loopback and lack of incremental authorization support.
- [Quota calculator](https://developers.google.com/youtube/data/determine_quota_cost): current default
  is 100 videos.insert calls/day, each costing one unit in the separate Video Uploads bucket;
  actual Cloud project limits must be checked. No promotion or restriction guarantees are made.
- [Revision history](https://developers.google.com/youtube/v3/revision_history): its older July 2020
  private-only audit restriction conflicts with the current insert reference saying unverified
  projects are not restricted to private. Applicability was not tested live; future public access
  is not assumed. Stage 5 forces Private independently of project status.

Remaining limitations:

- No real Google consent, Brand Account selection, production upload, live session expiry,
  real quota/processing behavior or live privacy verification was exercised.
- Windows/Linux secure account/upload storage is unimplemented; native checks were macOS only.
- Cancellation waits for an active request/Keychain operation. Connect/read timeouts do not prove
  a hard wall-clock bound against every OS/network stall; system Keychain prompts may need action.
- Native shutdown was orderly cancellation, not a power-loss test. Interrupted DB states and
  remote/local write gaps were injected deterministically in service tests.
- Recovery conservatively requires the original file/stat and current reviewed revision. If those
  are unavailable/changed, inspect Studio and reconcile rather than silently replacing content.
- Session entries are retained securely, including after local account disconnect; there is no
  session-expiry sweeper. A database copy alone cannot restore capabilities. Concurrent independent
  copies of the same account/session references are unsupported.
- Returned success establishes Private status at the observed response/check, not perpetual privacy
  or successful completed processing. User actions in Studio can change remote state later.

## Safe launch and pending live test

Launch `.venv/bin/python -m shortspublisher`; use `--data-dir PATH` for an isolated library.
Follow README's Desktop-client configuration, import/inspection and review instructions. To test
without live Google activity, use the three native harness commands above with a **new** disposable
root and finish all phases so synthetic Keychain entries are removed.

For a later live test, the user must supply/configure a legitimate Desktop client, authorize in the
system browser, verify the intended real channel and both scopes, approve a disposable reviewed
video, and directly accept its final Private confirmation. Verify the actual returned ID and Private
status, then restart and inspect history. Remote deletion requires separate explicit approval.
This live test remains pending; no production success is claimed here.

Recommended Stage 6 scope, only after explicit approval: first complete the live Private upload
verification, then consider a narrowly scoped manual publication/reconciliation workflow with
separate consent and safeguards. No scheduler, automatic posting or Stage 6 code was implemented.
