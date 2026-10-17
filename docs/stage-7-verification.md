# Stage 7 verification — 2026-10-17

**Stage 7 is fully live-verified. One user-confirmed Public upload succeeded, independent YouTube
checks confirmed Public privacy and successful processing, and restart preserved the same attempt.**
The implementation phase used controlled responses only. During the user-led live continuation below,
one real Public upload succeeded. No remote visibility update or deletion was performed. Stage 8 was
not implemented. Earlier pending statements below describe the pre-live verification baseline.

## Implemented workflow

Reviewed Public preference → separate Public confirmation → existing resumable upload engine →
independent videos.list verification → persistent history. Private uploading remains available.
No Unlisted upload, publishAt, scheduler, automatic queue, background service or remote update exists.

Videos now offers **Upload Privately…** and **Publish Publicly Now…**. Public is unavailable unless
saved metadata explicitly selects Public and the current revision has completed review. Permission,
source, readiness, destination identity and conflicting attempts are checked by the existing worker.
Live channel verification and hashing occur before final confirmation and are checked again before
transfer. Missing/stale review, altered source, wrong channel and insufficient scopes stop the action.

The Public dialog shows filename/path, byte size, duration, dimensions, exact title/description/tags,
channel title/ID, audience, synthetic disclosure and PUBLIC visibility. Its separate action is
**Publish Publicly Now**; cancellation creates no session and sends no bytes. Navigation and OAuth
consent do not authorize publication. Public confirmation cannot be substituted with Private consent.

Each immutable snapshot stores requested_privacy alongside the matching payload privacy. The HTTP
boundary rejects missing, mismatched and unsupported privacy and publishAt. Private remains an
explicit choice even when saved planned privacy is Public. An existing Private attempt cannot be
converted to Public. Both modes use the same service, HTTP adapter, source/review binding, locks,
secure session storage, acknowledged offsets, bounded retries and reconciliation logic.

Public completion cannot become **Public Verified** from videos.insert alone. The ID is persisted
first; videos.list must independently return that same ID, expected channel and Public privacy.
A visibility/channel discrepancy becomes Needs Reconciliation and preserves the observed result.
Unavailable verification keeps the ID and labels the result unverified; returned IDs prohibit more
media transfer. Verify Remote Result checks the existing result without creating another attempt.

Processing remains separate: uploaded/processing is pending, failed/rejected/terminated is failure,
and processed/succeeded is shown explicitly. Unknown processing is reported even when visibility
is verified. Public Verified establishes an API observation, not guaranteed playback or distribution.
Known permission, privacy-setting, channel, quota and metadata errors use sanitized explanations;
recognized processing rejection reasons include copyright. Permanent failures are not blindly retried.
No attempt is made to bypass restrictions or automatically change remote visibility.

## Files and persistence

Modified production files:

- `src/shortspublisher/uploads.py`, `upload_http.py`, `upload_store.py`: explicit intent, independent
  Public verification, result state/timestamps and restriction messages.
- `src/shortspublisher/library.py`: transactional schema 4→5 migration.
- `src/shortspublisher/ui/uploads.py`, `ui/window.py`: distinct actions, confirmation, progress,
  history visibility and reviewed Public context.
- `src/shortspublisher/accounts.py`, `ui/accounts.py`: authorization wording covers both upload modes.

Added `tests/test_public_uploads.py`, `tests/test_public_uploads_ui.py`,
`tests/native_public_smoke.py` and `tests/fixtures/library-v4.sqlite3`. Updated schema expectations
in existing library/preparation/account/upload tests and added verification-call tracking to
`tests/upload_support.py`. Updated README, architecture and roadmap. No new dependency or scope;
`pyproject.toml` and `uv.lock` did not need changes.

Schema 5 adds verification_status and verified_at, and updates the partial unique index to recognize
Public Verified as a completed transfer. New attempts start unverified. Existing snapshots and all
previous column values remain unchanged; their new verification status is historical and their new
timestamp is empty. Old explicit Private payloads establish legacy intent without inventing missing
privacy. Requested visibility remains inside the immutable snapshot, not an editable history field.

The v4 fixture is a SQLite backup of the genuine controlled Stage 5 native workflow, not a copy of
live account credentials. Tests compare every original column value across migration and verify
transaction rollback when index migration fails. Prior migrations and future-version rejection
remain covered. SQLite and Keychain/YouTube still cannot commit atomically; prior documented crash
windows, unknown outcomes and duplicate-risk reconciliation remain applicable.

## Automated verification

Environment: macOS Apple Silicon, Python 3.12.14, PySide6 6.12.0, FFmpeg/FFprobe 9.0.2.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

**249 passed, 0 failed, 0 skipped in 45.790 seconds.** All 212 previous-stage tests plus 37 new tests
(29 service/protocol/storage and eight Qt UI tests). Ruff passed; the final formatting check reported 52 files already formatted;
mypy found no issues in 22 source files. Local OAuth callback tests ran with listener permission.
The final full log is `artifacts/stage7-final/automated-tests.log`. No real Google credentials or
upload endpoint were used by regression tests.

Coverage includes explicit privacy serialization/rejection, separate confirmations, cancellation,
Public preference and review binding, missing permissions, wrong channel, changed source, lost final
response, pause/resume with unchanged privacy, expired sessions, saved-ID verification recovery,
restart without transfer, duplicates, processing pending/rejected, returned-ID mismatch, Public
requested/Private returned, rate limits, token refresh, quota/permanent failures, migration rollback,
old Private history, safe close/Quit, secret-leak checks and exact source bytes. Existing long-filename,
media, metadata, OAuth, Keychain failure and Private-upload regressions remain passing.

Development checks caught a stale expected error phrase and mistakes in the new UI test's AccountView
and confirmation-result lookup. These were corrected; no tests were removed or skipped. New attempt
verification defaults were explicitly set to unverified rather than inheriting the historical migration
default. Final history labels include requested visibility and last verified observation.

## Native macOS verification

```sh
.venv/bin/python tests/native_public_smoke.py prepare --root artifacts/stage7-public-final
.venv/bin/python tests/native_public_smoke.py reopen --root artifacts/stage7-public-final
.venv/bin/python tests/native_public_smoke.py history --root artifacts/stage7-public-final
.venv/bin/python tests/native_upload_smoke.py prepare --root artifacts/stage7-private
.venv/bin/python tests/native_upload_smoke.py reopen --root artifacts/stage7-private
.venv/bin/python tests/native_upload_smoke.py history --root artifacts/stage7-private
.venv/bin/python tests/native_accounts_smoke.py prepare --root artifacts/stage7-accounts
.venv/bin/python tests/native_accounts_smoke.py reopen --root artifacts/stage7-accounts
.venv/bin/python -m shortspublisher --smoke-test --data-dir /tmp/publisher-stage7-entry
```

All nine processes passed. The final Public workflow was rerun after history-label changes. Logs:
`artifacts/stage7-final/public-native-final.log` and `native-tests.log`. Entry-point shutdown returned
code 0 at 09:46:50 local time. Reused Private harness output still identifies itself as Stage 5.

These workflows use actual Cocoa widgets, real FFprobe inspection, generated 720×1280 H.264/AAC
media, SQLite, source hashing and isolated real macOS Keychain. Google OAuth/channel/upload/verification
responses are controlled. Public uses `ShortsPublisher.Stage7.NativeVerification` and
`ShortsPublisher.Stage7.NativeSessions`. Final phases removed all synthetic credential/session entries.

Public prepare checked explicit permission, cancelled confirmation with zero session creation, real
modal Public confirmation, acknowledged partial progress, navigation, Stop, resume and close during
an active request. Reopen retained the same attempt without sending automatically, exercised missing
session reconciliation, resumed at the acknowledged offset, compared exact received bytes, and
required independent Public verification. A later controlled Private response produced a critical
visibility discrepancy; a fresh Public check restored verified status without sending more media.
The third process restored Public history without another initiation or transfer.

Private regression exercised the original separate confirmation, recovery and Private result. Account
regression covered denied consent, secure storage, restart, offline presentation, refresh, disconnect
and cancellation. Harnesses supply file-picker/confirmation responses; manual browser consent and
OS picker interaction are not asserted.

Screenshots at **760×560** and **1180×780**, top/bottom, cover upload, paused, restarted, reconciliation,
Public verification, discrepancy and history; both confirmation sizes were captured. Representative
captures were visually inspected: readable Public warning and channel/file details, scrollable content,
no horizontal page overflow, clear requested/returned visibility and accessible controls. Long filenames
are also covered by the existing UI regression. Evidence remains in the three Stage 7 native folders.

## Preservation

Read-only SHA-256 comparisons of every row in the live Stage 6 videos, preparations, account_config,
account_connection and upload_attempts tables exactly match their pre-work values. That live database
remains schema 4: it was not opened by the new application or migrated during development. Its channel
reference and Private history for `bfNDN1Gq540` are unchanged. The original six-second source hash
still matches `08627815bda4005b3ec34030bb9f3015bd1a69a9aed2601ed3cecb8f8db0b5fe`.

No real token/session was read or modified for Stage 7 testing. No remote request was made to modify,
reupload, publish or delete the Stage 6 video. Its remote privacy was not rechecked in this stage;
its last live verified status remains the Stage 6 Private result. All 250 retained ShortsStudio hashes
match and its git status is clean. Evidence: `artifacts/stage7-final/preservation-check.json`.

## Official research and limitations

Checked October 17, 2026:

- [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert):
  explicit privacy/declarations, upload authorization and API errors. Missing privacy must not rely
  on the API's Public default; the local boundary rejects it.
- [Resumable protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol):
  session creation, status queries, acknowledged ranges and chunk constraints remain unchanged.
- [videos.list](https://developers.google.com/youtube/data/reference/rest/v3/videos/list): independent
  snippet/status/processingDetails verification for the returned ID.
- [Quota guidance](https://developers.google.com/youtube/data/determine_quota_cost): default 100 insert
  calls/day, one unit per call in the separate Video Uploads bucket; actual project limits apply.
- [Developer policies](https://developers.google.com/youtube/terms/developer-policies): clear user
  control and consent, accurate metadata/visibility presentation, privacy and no restriction bypass.
  This stage does not constitute a Google compliance audit or distribution approval.

Current insert documentation says unverified projects are not restricted to Private, while older
revision-history language describes a private-only audit restriction. Neither statement establishes
this project's actual Public capability. **No live Public restriction was encountered because no
live Public test was attempted.** Private success is not evidence of Public access. No claims about
promotion, distribution, copyright clearance or immunity from restrictions are made.

Remaining limits include pending live Public payload acceptance, actual privacy/processing and
project restrictions; macOS-only secure storage/native checks; finite request timeouts rather than a
universal wall-clock bound; cancellation after the active request/Keychain operation returns; and
existing SQLite/Keychain/remote crash windows. No hard power-loss test was performed. Stored API
observations can become stale; failed checks mark them unverified. Public API privacy alone does not
prove playback availability. No background verification polling or automatic retry queue exists.

## Original live-test instructions (completed below)

1. Close the old application before launching updated code. Preserve the Stage 6 library and
   connection; do not disconnect or reuse its uploaded video. Back up the database with the app closed.
2. Launch `.venv/bin/python -m shortspublisher --data-dir artifacts/stage6-live` to use the established
   connection; schema migration will occur on this explicit launch. Verify the actual channel is
   StonkYa (`UCoj4BJDrrcuE3gTvpi-7QFA`) and upload permission is present.
3. Import a **new disposable original video**, inspect it, enter the exact intended title/description,
   tags and declarations, select Public, save and complete review. Check source integrity and unresolved
   attempts; never release an uncertain attempt merely to bypass a duplicate block.
4. Open Publish Publicly Now and personally review the exact content, channel and PUBLIC visibility.
   Only the user's direct **Publish Publicly Now** acceptance authorizes that real upload. Cancel is safe.
5. Perform exactly that one approved upload. Record its actual ID and independent videos.list privacy,
   channel and processing result. If Public is rejected or Private is returned, retain the result and
   resolve the restriction without another upload or automatic visibility modification.
6. Restart and verify the same history and ID, then Verify Remote Result. Confirm no new attempt or
   transfer occurred. Any remote deletion/change requires separate permission.

This optional live test remains pending; no new video has been approved for Public publication.
Fully live-verified Stage 7 requires that real test and independently observed Public status.
Recommended next step is to complete it before separately authorizing Stage 8: persistent job approval,
time-zone/date handling and deterministic scheduler tests. Scheduling and automatic posting are deferred.

## Live continuation — preparation on 2026-10-17 (historical observations)

At the user's request, the running application was closed and its absence confirmed before backup.
SQLite backup `artifacts/stage7-live-preflight/library-before-live-20261017T145449Z.sqlite3`
passed integrity_check. The original database was schema 4 with one completed Private attempt,
`90743697aeec433f91580769a2b89a7d`, video `bfNDN1Gq540`.

The existing application was then launched with `--data-dir artifacts/stage6-live`. Migration to
schema 5 succeeded. Read-only comparisons confirmed every original column value in videos,
preparations, upload_attempts and account_config was preserved. Account restoration may refresh
its verification timestamp normally; credentials were not replaced or disconnected.

New original candidate: `artifacts/stage7-live-preflight/public-integration-test.mp4`.
Locally rendered Public integration title slate plus quiet generated sine tone; no third-party media.
Real Inspector/FFprobe reports six seconds, 540×960, 24 fps, 144 frames, H.264/AAC, 53,683 bytes,
and Passes implemented checks. SHA-256:
`286ee9eeb1a4f1dc12cfa16df2386040addb04fe968d1773804b00448ebedba4`.
Media evidence is saved beside the candidate. Free disk space at preparation was 734,602,940,416 bytes.

Native UI-control startup was unavailable, so import, metadata review and final confirmation are
being guided through the user's own clicks. No Public upload has yet been approved or initiated.
Live Public result, processing and restart verification remain pending.


## Live upload observed — 2026-10-17

On the user's “ready” reply, read-only inspection found that the application had already transferred
the new candidate through its confirmation workflow. The assistant did not accept the dialog or
initiate the upload. The exact saved metadata used the proposed title and description, with the
user's tag `test tag`, Made for Kids false and synthetic disclosure false. Visibility was Public.

- Attempt: `6f3e2a679e084f97b4434590151c672b`, created 14:57:33 UTC.
- Actual returned video: [`OxgK8zkDmkI`](https://www.youtube.com/watch?v=OxgK8zkDmkI).
- Transfer completion: 14:57:37 UTC; all 53,683 bytes acknowledged.
- Intended and verified channel: StonkYa, `UCoj4BJDrrcuE3gTvpi-7QFA`.
- Initial saved result: Public with processing pending.
- Fresh independent verification at 14:58:22 UTC: **Public**, **processing succeeded**,
  state **Public Verified**. The existing UploadService verify action used production channel
  authorization and videos.list, with no transfer/update/delete action.
- Source SHA-256 still matches the pre-upload evidence.
- Exactly two total attempts exist: the original Stage 6 Private attempt and this single Public attempt.

Nonsecret API-result evidence: `artifacts/stage7-live-preflight/live-result.json`.
No API restriction or visibility discrepancy was observed in this approved upload. This establishes
this project's Public capability for this operation, not a permanent guarantee. The short transfer
was already complete when inspected; intermediate UI progress percentages were not independently
observed live. Persisted acknowledged bytes confirm full transfer.

Final close/reopen history and duplicate-count verification is still pending. No production defect
was found and no code was changed; the 249-test controlled baseline remains applicable.


## Final restart verification — Stage 7 complete

The user closed ShortsPublisher normally; process inspection confirmed it had exited. The application
was reopened in a separate process at 14:59:04 UTC using the same `artifacts/stage6-live` library.
Its normal startup restored and live-verified StonkYa at 14:59:05 UTC. A further production
UploadService/videos.list check at **14:59:32 UTC** independently confirmed the same video
`OxgK8zkDmkI`, same channel, **Public** privacy and **processing succeeded**.

Exactly two attempts remain: the original Stage 6 Private attempt and one Stage 7 Public attempt.
IDs, acknowledged offsets and completion timestamps were unchanged across restart and re-verification.
No new transfer/session was initiated by the verification work. The Stage 6 attempt's original
column values match the pre-migration backup exactly; both original source hashes are unchanged.
Final evidence: `artifacts/stage7-live-preflight/restart-result.json`.

The live upload used the user's direct application confirmation; the assistant observed the completed
transfer afterward and performed only verification. Native UI automation was unavailable, so final
history persistence was checked through the application's durable store/services rather than a new
live screenshot. Intermediate progress was not sampled because the small transfer had already finished.
There was no observed Public restriction, wrong channel, privacy discrepancy or processing failure.
No remote content was modified or deleted. No relevant code defect was found, so no source changes,
new regression tests or repeat full suite were needed; the 249 passing tests remain the last full run.

**All Stage 7 live acceptance criteria are satisfied.** The old pending statements above are retained
as chronological pre-live observations, superseded by these actual results. Stage 8 remains unstarted
and requires a separate explicit request. The application is open on the existing library.
