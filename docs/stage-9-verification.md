# Stage 9 verification — 2026-10-17

**Stage 9 is fully live-verified. One user-authorized scheduled Public upload started automatically,
YouTube independently confirmed the intended channel, Public privacy and successful processing, and
restart preserved the same execution without another attempt.** The live continuation is recorded at
the end of this report. Earlier pending statements describe the controlled implementation baseline
and are superseded by those observations. Both previous videos/records were preserved. No remote
visibility update or deletion was performed. Stage 10 was not implemented.

## Implemented workflow

Reviewed Public video → approved future plan → separate automatic execution authorization → persistent
global Enable → desktop timer → durable claim and linked upload attempt → existing resumable uploader
→ independent Public/channel verification → processing/result history. No second confirmation appears
at the scheduled instant. Drafts, ordinary plan approval, navigation and account connection do not
supply automatic publication consent.

The activation dialog identifies exact source/path/size/hash, title/description/tags, category 22,
channel ID, audience/synthetic declarations, PUBLIC visibility, local date/time, IANA zone, UTC instant
and approved revision. It explicitly authorizes automatic upload without a second confirmation while
the application runs. New plans can open activation immediately after saving; existing future Stage 8
plans use the same separate activation action. Past/changed plans require rescheduling/reapproval.

Global publishing starts Paused after migration. The rail always provides Enable or Pause All Scheduled
Publishing. Enable requires explicit confirmation. Pause commits through a separate worker even while
a transfer is active; it prevents new starts and does not stop or undo a started upload. A separate
Stop action is available in Dashboard, Queue and Calendar. It waits for an active request to return.
Closing/Quit coordinates cancellation and worker cleanup; there is no background process or service.

Dashboard displays persisted global status, activated upcoming count, next job, attention count and
active upload. Queue/Calendar share job and execution records, show acknowledged progress and linked
result, and offer Upload History for Resume/Verify/Resolve. Completed is displayed only for a Public
Verified result whose processing is processed/succeeded. A returned ID, 100% transfer or Public status
alone is not described as completed processing. Historical plan timing remains visible separately.

## Timing, ownership and recovery

The desktop dispatcher uses an injectable aware clock, authoritative UTC and a single-shot Qt timer
for the next upcoming instant, capped by a five-second safety check. It operates independently of
navigation and the separate 30-second planning refresh. The schedule editor does not pause dispatch.
Hashing, SQLite, credential operations and network requests run outside the GUI thread.

The execution window is `[scheduled instant, scheduled instant + 60 seconds)`. Preflight must finish
before durable start intent can commit within that window. Missed windows do not publish late. Startup
marks already-past unstarted activated jobs Missed even inside the window; enabling after a past
instant likewise never catches it up. Sleep/long delays are tested by advancing the injected clock.
Clock rollback never clears durable ownership or resets Missed work. No hard real-time guarantee is
made: OS scheduling, network delays, sleep and Keychain prompts can prevent a timely start.

Dispatch takes the same upload lock then account lock as manual work. A short BEGIN IMMEDIATE
transaction validates activation, exact consent binding, global switch, due window, local review,
source/channel snapshot, duplicates and local limits. It inserts one existing-format upload attempt
and its unique execution link together. Separate processes cannot claim the same job twice. No SQLite
write transaction remains open over a network operation. The shared upload lock serializes this
library's transfers, which is stricter than one automatic upload per channel.

The trusted uploader entry point takes an execution ID, not an arbitrary bypass flag. It independently
checks the persisted consent, approved revision binding, original Public payload, channel/source and
unused attempt. Existing live channel/scopes/refresh checks, source SHA-256/review checks, secure
session storage, acknowledged offsets, bounded retries and result verification are reused. No second
HTTP uploader, GUI-click simulation, new Google scope or dependency was introduced. Public is explicit
in the payload; `publishAt` is absent.

Immediately before initial POST, the uploader rechecks approval, local content, global pause and time,
and durably records start intent. A pause ordered after that commit does not undo the already-started
operation. An explicit initialization HTTP 401 rejection retains the existing one refresh/retry rule;
uncertain initialization responses never cause another independent session. A claimed or started job
cannot be reset to Scheduled. Restart never initiates or resumes its upload automatically.

A default budget of three automatic claims per UTC day is user-configurable from one to ten. Blocked
claims count conservatively. Known quota/channel-limit errors observed that UTC day block a new
claim; this is not a background quota monitor or a guarantee that Google permits another upload.
Account locks and the uploader's error handling remain authoritative. A conflicting active operation
can delay an unclaimed due job only within its start window; it cannot become a late catch-up upload.

Preflight failures preserve Needs Attention and a linked Interrupted attempt when a claim already
exists. Uncertain remote outcomes retain Needs Reconciliation/session history. Existing-session
Resume, saved-ID Verify and deliberate duplicate-risk acknowledgement remain explicit actions in
Upload History. After release, Close Reconciled Schedule closes only the local plan and preserves its
execution forever. A new plan requires fresh approval. There is no remote delete/update operation.

SQLite, Keychain and YouTube still cannot commit atomically. A crash after durable start intent but
before POST can conservatively leave an unstarted attempt requiring reconciliation; a lost session
creation response can leave an unknown remote outcome. No exactly-once remote guarantee is claimed.

## Files and migration

New production files:

- `src/shortspublisher/execution_schema.py`: transactional schema 6→7 migration.
- `src/shortspublisher/execution.py`: per-job consent, global controls, atomic claims and coordination.
- `src/shortspublisher/ui/execution.py`: activation dialog, control/dispatch workers and status.

Modified `library.py`, `scheduling.py`, `uploads.py`, `app.py`, `ui/scheduling.py` and `ui/window.py`.
Added `tests/test_execution.py`, `tests/test_execution_ui.py`, `tests/native_execution_smoke.py` and
`tests/fixtures/library-v6.sqlite3`. Existing migration assertions now expect schema 7; the shell test
recognizes the implemented Dashboard. README, architecture and roadmap describe the final workflow.
No dependency or lockfile change was required.

Schema 7 rebuilds the execution-constrained schedule table and event foreign key transactionally,
preserving every original value/event and recreating active-plan indexes. Legacy jobs retain
execution_enabled=0. New tables store publishing_control, execution_consent and scheduled_executions.
Job and attempt links are database-unique; a trigger rejects enabling a plan without consent. OAuth
tokens and resumable session capabilities remain exclusively in Keychain, never these tables.

The v6 fixture is a SQLite backup of the genuine completed Stage 8 controlled native workflow at
`artifacts/stage8-schedules-final/data/library.sqlite3`. It contains generated-media plans/events and
a synthetic unused account reference, not real the owner's channel authorization. Migration tests compare all
original table values, verify foreign keys, disabled old jobs and paused defaults, and force a
mid-migration failure to verify complete rollback. Existing older/future-schema tests remain active.

## Automated verification

Environment: macOS Apple Silicon, Python 3.12.14, PySide6 6.12.0, FFmpeg/FFprobe 9.0.2.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

**390 passed, 0 failed, 0 skipped in 84.508 seconds.** Ruff passed; formatting reported 66 files
already formatted; mypy found no issues in 28 source files. The final complete run includes the
pre-POST failure-state regression. Output: `artifacts/stage9-final/automated-tests.log`.

The 319 previous tests are retained with schema/Dashboard expectations updated. There are 71 new
Stage 9 tests: 56 service/storage/execution tests and 15 Qt UI tests. They cover actual SQLite and
worker boundaries, including two independent OS processes competing for one claim. Google responses
and credentials in automated tests are controlled; no live service is used. OAuth loopback tests need
local socket permission, so the final suite ran outside the restricted sandbox.

Coverage includes activation cancellation/staleness and exact payload binding; draft/legacy disable;
global pause/enable persistence; no startup/resume catch-up; on-time boundaries, forward/backward
clock changes and simulated sleep; atomic rollback and competing claims; manual lock conflicts;
configurable daily budgets and observed quota errors; source/review edits and changed bytes during
transfer; missing scope/credentials, expired/revoked grants and unexpected live channel; failed secure
session storage; lost initialization/final responses; session expiry, long Retry-After and explicit
same-session resume; unknown/failed verification, private discrepancy and processing failure; no
repeat initiation on dispatch/restart; deliberate local reconciliation; migration preservation; native
Qt activation, navigation-independent dispatch, result filters, Stop and safe shutdown. Prior tests
continue covering lower-level HTTP, OAuth, privacy, refresh, source, write-failure and secret boundaries.

Initial checks found a stale Dashboard placeholder assertion and sandbox-blocked callback tests.
Those were corrected/rerun with listener permission; no test was removed or silently skipped. A quota
fixture needed its observation timestamp aligned with the injected future clock (the existing upload
store uses the actual wall clock). Visual review replaced a confusing Due/completed row label with
its linked upload outcome. Recovery review found that a write failure before Preparing could leave
Awaiting Confirmation on a consumed claim; it now persists Interrupted and has regression coverage
proving no later initialization. Final formatting/type checks also pass.

## Native macOS verification

Final automatic workflow:

```sh
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_execution_smoke.py prepare --root artifacts/stage9-native-final
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_execution_smoke.py reopen --root artifacts/stage9-native-final
```

Both separate processes passed. The harness uses real Cocoa widgets, real FFprobe inspection of
new generated 720×1280 H.264/AAC portrait sources, actual SQLite and isolated macOS Keychain services
`ShortsPublisher.Stage9.NativeVerification` and `ShortsPublisher.Stage9.NativeSessions`. All Google
OAuth/channel/transfer/verification responses are controlled. Any accidental ordinary HTTP request
raises an assertion. The controlled transport writes received bytes to a local file across processes.

Prepare imported three videos through the real library worker, saved Public metadata/declarations,
completed review, displayed actual schedule and activation modals, and activated each exact future
job. It verified the initially paused global control and then explicitly enabled it. Advancing the
injected clock and firing the real Qt timer caused one job to upload without a second modal or manual
upload action. Navigation remained available. Intermediate acknowledged progress was captured at
262,144 of 1,762,359 bytes, and the exact received bytes matched the approved original source.

The controlled result was synthetic ID `TestVid123_`, Public privacy and processed status. The Public
engine independently invoked its verification adapter; this is controlled evidence, not an actual
YouTube video or proof of live scheduling. Prepare then verified a paused/missed second job and a
wrong-channel third preflight with zero additional POSTs. Two attempts existed: one successful
transfer and one blocked preflight; only one session initiation occurred.

Reopen preserved those exact job/attempt links and completed history, with zero new session
initiations or media sends. Source hashes for all three originals matched. The final phase removed
and checked absence of all its synthetic credential/session Keychain entries. Evidence and screenshots
are under `artifacts/stage9-native-final/`; logs are `artifacts/stage9-final/native-final-prepare.log`
and `native-final-reopen.log`.

Nine additional native regression/entry processes passed:

```sh
.venv/bin/python tests/native_public_smoke.py prepare --root artifacts/stage9-public-regression
.venv/bin/python tests/native_public_smoke.py reopen --root artifacts/stage9-public-regression
.venv/bin/python tests/native_public_smoke.py history --root artifacts/stage9-public-regression
.venv/bin/python tests/native_upload_smoke.py prepare --root artifacts/stage9-private-regression
.venv/bin/python tests/native_upload_smoke.py reopen --root artifacts/stage9-private-regression
.venv/bin/python tests/native_upload_smoke.py history --root artifacts/stage9-private-regression
.venv/bin/python tests/native_schedule_smoke.py prepare --root artifacts/stage9-planning-regression
.venv/bin/python tests/native_schedule_smoke.py reopen --root artifacts/stage9-planning-regression
.venv/bin/python -m shortspublisher --smoke-test --data-dir artifacts/stage9-entry
```

Log: `artifacts/stage9-final/native-regressions.log`. The original manual Private/Public engines and
planning workflows still pass; synthetic Keychain entries are removed by their final history phases.
All 11 final native/entry processes passed. Earlier exploratory Stage 9 captures were superseded by
the final pair. The harness supplies picker selections and confirmation actions; manual OS interaction
and Google browser consent are not asserted. Screenshots at 760×560 and 1180×780 cover activation,
paused/enabled status, acknowledged progress, completion, calendar, missed/blocked jobs and reopened
history. Representative captures were visually inspected: readable identifiers/content, reachable
scrollable controls and no horizontal page overflow.

## Preservation and remaining limits

Read-only before/after hashes of every table in the live the owner's channel library match exactly. It remains
schema 5 and still contains only the genuine Stage 6 Private `<youtube-video-1>` and Stage 7 Public
`<youtube-video-2>` attempts. It was not opened by upgraded production code or migrated during development.
Both original media hashes remain unchanged. No real credential/session was read or modified. All
250 retained ShortsStudio reference hashes match and its git status remains clean. Evidence:
`artifacts/stage9-final/preservation.json`. The default user library was not used for testing.

Remaining limits:

- No real automatically scheduled Google/YouTube upload was authorized or attempted. Prior manual
  live success does not establish the new timed workflow's live acceptance.
- Foreground desktop operation only; no daemon, wake-from-sleep or powered-off execution. Sleep and
  clock cases were deterministic simulations, not exhaustive OS clock/power tests.
- Native checks are macOS only. Windows/Linux secure-storage backends remain unimplemented.
- Cancellation waits for finite network requests/Keychain operations; prompts and OS stalls can
  exceed intended timing. Read/connect timeouts are not universal wall-clock deadlines.
- Native restart was orderly. Crash windows/write failures were injected in tests; no power-loss
  test or cross-service atomicity guarantee exists.
- UTC daily limits count claims, including blocked preflights. Quota observations may become stale;
  actual Google quotas/channel restrictions are not polled or bypassed.
- Public Verified reflects the observed response, not perpetual visibility, copyright clearance,
  playback/distribution or a guarantee that processing stays successful.
- Secure sessions retain the prior uploader's lifecycle limitations; database copies do not copy
  capabilities and cannot be treated as independently coordinated account stores.

## Official references and optional live continuation

Checked October 17, 2026:

- [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert): official
  upload endpoint, upload scope, supported metadata/declarations and explicit Public privacy.
- [Resumable protocol](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol):
  initiation, acknowledged ranges, status queries and existing-session recovery.
- [YouTube developer policies](https://developers.google.com/youtube/terms/developer-policies): user
  control, clear consent and accurate representation of operations. This is not a compliance audit.

No new scope or remote visibility-update endpoint was added. The existing uploader explicitly rejects
`publishAt`; application-owned timing initiates `videos.insert` with Public privacy.

A live continuation needs separate explicit user authorization for one new disposable original video.
Close the existing app and safely back up its database before opening upgraded code. Preserve the
the owner's channel connection and both previous videos. Verify actual channel/scopes, import/review the new
source, choose a future time with adequate preflight margin, inspect the automatic Public consent and
personally activate that exact job. Explicitly enable the global switch and leave the app open/awake.
After one initiation, independently verify the returned ID/channel/Public privacy and processing;
restart and confirm unchanged attempts/source. Unexpected restrictions require reconciliation, not
another upload or automatic visibility changes. Remote deletion/modification needs separate permission.

Stage 9's controlled implementation is complete; live timed verification remains pending. Stop here.
Stage 10 and background service development require a separate request.

## Live continuation — preparation on 2026-10-17

The user authorized preparing one real scheduled test, reserving automatic Public activation and
global enabling for their direct actions. Process inspection found no running ShortsPublisher before
backup. `artifacts/stage9-live/library-before-live-20261017T160048Z.sqlite3` was created using SQLite's
backup API and passed integrity_check. Supported migration from schema 5 to 7 preserved every original
column value in every existing table. Publishing remained Paused, with zero scheduled jobs.
Evidence: `artifacts/stage9-live/backup.json` and `migration.json`.

New original source: `artifacts/stage9-live/stage9-automatic-public-test.mp4`. A locally rendered
Stage 9 title slate and quiet generated sine tone, eight seconds, 540×960 portrait, 24 fps,
192 decoded frames, H.264/AAC, 76,838 bytes. SHA-256:
`53942c3aa5ec36b8ac671f41e2ab3bccd63927166505e888c75bcf6c03bb7e3c`.
Real Inspector/FFprobe inspected it; free disk space at preparation was 734,460,706,816 bytes.
Evidence: `artifacts/stage9-live/source-evidence.json`. It is distinct from both previous test videos.

The actual app was opened with `--data-dir artifacts/stage6-live` at 11:01:34 local time. A supported
production credential/channel check at 16:02:14 UTC confirmed the owner's channel (`UC_owner_channel`),
both required scopes and working Keychain access. Only the two previous completed attempts exist;
there is no unresolved upload conflict. No account was disconnected or replaced. No upload endpoint
was invoked. Nonsecret evidence: `artifacts/stage9-live/account-preflight.json`.

Native UI control was unavailable (native pipe startup failed). The user's own clicks will supply
metadata/declarations, plan approval, automatic Public activation and global Enable. These actions
remain pending, along with the real timed upload, independent result and restart verification.
The prior preservation statements above describe the controlled implementation phase; this authorized
continuation has now migrated the live database and checked its real account.

At 16:11:05 UTC, read-only inspection confirmed the user's automatic activation for job
`e869595aaa9e4010997fd082649df1fa`, approved at 16:10:49 UTC for 11:11:00 America/Chicago
(16:11:00 UTC). However, the global control was still Paused with no enable timestamp. No execution
or new upload attempt existed. A separate disabled draft for the same source was also present and
was left unchanged. The scheduled instant passed while paused; this is not an upload failure.
The user must deliberately reschedule/reapprove the activated plan and enable publishing before its
new instant. No late execution, automatic rescheduling or second upload was initiated by the assistant.

## Real automatic upload observed — 2026-10-17

The user deliberately rescheduled the same job after earlier paused times elapsed. Final approval
revision 11 was activated at 16:15:40.353020 UTC for **11:18:00 America/Chicago (16:18:00 UTC)**.
The user enabled global publishing at 16:16:44.168241 UTC. The other saved item remained a disabled
draft. The assistant did not accept either authorization, enable publishing, invoke dispatch, alter
the real clock or invoke an upload action.

The existing running desktop scheduler naturally claimed execution at **16:18:00.011141 UTC** and
committed start intent at **16:18:00.254912 UTC**, approximately 11 ms and 255 ms after the approved
instant respectively. The exact outgoing POST timestamp was not independently instrumented; start
intent is the durable pre-POST boundary. Transfer completion was saved at **16:18:02 UTC**.

- Job: `e869595aaa9e4010997fd082649df1fa`.
- Execution: `9e12ae1ca71f475c8b9fa4214f3c6d91`.
- Sole new upload attempt: `063e12f27663418a90602b8326c8b433`.
- Actual returned video: [`<youtube-video-3>`](https://www.youtube.com/watch?v=<youtube-video-3>).
- All **76,838 bytes** acknowledged; source SHA-256 remains unchanged.
- Approved payload: explicit Public, category 22, saved title/description, `test tag`, Made for Kids
  false and synthetic disclosure false, as selected by the user. No publishAt is present.
- Initial independent result confirmed Public with processing pending.
- A further production Verify Remote Result/videos.list check at **16:18:52 UTC** confirmed
  **the owner's channel (`UC_owner_channel`), Public privacy, processing succeeded**.
- Three total upload attempts exist: the two original manual records plus this one scheduled attempt.
  All original upload-record column values match the pre-migration backup exactly.

Evidence: `artifacts/stage9-live/live-result.json`. The small transfer had finished before inspection;
intermediate live percentages were not sampled. Persisted acknowledged bytes and source binding were
verified; server-side byte-for-byte media retrieval was not performed. Public availability time is not
inferred from upload-start or completion timestamps. No live API restriction or code defect was found.
No remote update or deletion occurred. Final close/reopen verification remains pending, so Stage 9
is not yet declared fully live-verified.


## Final restart verification — Stage 9 complete

The user closed the application normally. Process inspection confirmed no ShortsPublisher process
remained before reopening the same `artifacts/stage6-live` workspace at **16:20:34 UTC** in a new
process. Startup restored and live-verified the owner's channel at 16:20:34 UTC. Before any further result check,
every scheduled job, execution, consent and upload-attempt row exactly matched the pre-restart
snapshot. The disabled draft remained disabled; only the already-consumed test job was activated.

A fresh production Verify Remote Result/videos.list check at **16:20:59 UTC** confirmed the same
actual video [`<youtube-video-3>`](https://www.youtube.com/watch?v=<youtube-video-3>), channel
`UC_owner_channel`, **Public** privacy and **processing succeeded**. The single scheduled
execution remained linked to attempt `063e12f27663418a90602b8326c8b433`. Its claim/start timestamps,
completion timestamp and 76,838 acknowledged bytes were unchanged. Three total upload attempts
remain: two prior manual uploads and exactly one automatic upload. No new initiation or transfer
was requested by the verification work, and restart did not create another execution or attempt.
No packet-level session-creation capture was added; this conclusion uses durable records and the
existing completed-attempt initiation guard, not a claim of new network tracing.

Original video/preparation/upload records and public client configuration match the closed-app
pre-migration backup. All three original media hashes match their recorded values. Existing account
restoration can update verification time/refresh authorization normally; credentials were never
replaced or disconnected. Evidence: `artifacts/stage9-live/before-restart.json` and `restart-result.json`.

The user supplied actual metadata/declarations, per-job Public activation and global Enable through
the application. The assistant did not press manual Publish, invoke dispatch or transfer, alter the
clock, or substitute its own consent. Earlier 11:11 and 11:14 plans passed while global publishing
was paused; the user deliberately reapproved the final 11:18 schedule. No failed remote upload,
restriction or code defect was observed. No production changes or new regression tests were required;
the last full controlled baseline remains 390 passing tests and 11 native/entry-point checks.

Native UI automation remained unavailable. Restart persistence was checked through the real durable
records and production services supporting Queue/Calendar, rather than a new live screenshot of each
page. Intermediate transfer percentages were not sampled because the small upload finished before
inspection. The durable start timestamp is pre-POST intent, not an instrumented network-send instant.
These observation limits do not change the independently verified Public/processing result.

**Stage 9 live acceptance is complete.** The application is open on the same workspace. Global
publishing remains enabled as the user selected; there are no unstarted activated jobs, only the
completed job and a disabled draft. No remote content was updated or deleted. Stage 10 remains
unstarted. A later separately authorized stage may consider the proposed optional macOS background
service, with explicit lifecycle controls and its own verification; nothing from that stage was added.
