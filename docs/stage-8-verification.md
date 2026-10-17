# Stage 8 verification — 2026-10-17

Stage 8 implements local scheduling plans, explicit approval, a persistent Publishing Queue,
Calendar, rescheduling/cancellation and deterministic due-time evaluation. **No scheduled upload
execution exists. No real YouTube upload or remote modification was performed during Stage 8.**
Stage 9 remains unimplemented and requires a separate request.

## Implementation and boundaries

Videos has a Schedule tab and **Schedule Video…** action for saved, reviewed Public metadata and
an identified channel. The editor shows the selected video, intended stable channel, date picker,
24-hour time, editable IANA zone and UTC conversion. Repeated local times require an explicit first/
second occurrence. **Review Schedule…** hashes and checks the source in a worker, then displays the
exact source path/name/size/duration/dimensions, metadata, declarations, channel, Public visibility,
local time/zone and UTC instant. **Approve Schedule** is separate consent. Cancel creates no approved
job; Save Draft creates an explicitly unapproved record. Unsaved edits require a discard confirmation.

Scheduling is local planning: stored channel identity is used with an explicit UI explanation that
live authorization is required before future execution. It does not read Keychain, refresh grants,
contact Google or call the upload engine. An absent/replaced connection blocks approval and marks
existing plans Needs Attention. Offline cached identity may be used to plan; it does not authorize
future execution. No new scopes or dependencies were added.

Queue and Calendar share the same authoritative jobs. Queue sorts by scheduled UTC, with state
filters and retained cancelled history. Calendar uses Qt's month widget, previous/next navigation,
marked dates and a selected-day job list with state/channel/time-zone labels. Dates are grouped by
each job's approved local date, explicitly disclosed in the UI; the system time zone does not shift
jobs to another day. Both views expose details, rescheduling/reapproval and confirmed cancellation.
There are no dummy entries, fabricated progress or publication-success labels.

Rescheduling keeps the stable ID, increments its revision and requires renewed approval. The old
snapshot remains in schedule_events. Changed metadata/source/review never silently replaces approved
content. Needs Attention retains the original snapshot until deliberate reapproval; deleted videos
and disconnected accounts do not erase plans. Cancelled plans remain inspectable and cannot be edited
back into an approved state. A new plan requires separate validation/approval.

The shared cancellable source fingerprint/signature helpers moved from uploads.py to source.py;
manual uploads retain their behavior. Scheduling imports these read-only helpers, not an uploader.
Its model has platform/channel IDs for future adapters without introducing a generic platform framework.

## SQLite and concurrency

Schema **5→6** runs in the existing initialization transaction. It adds scheduled_jobs,
schedule_events, and partial unique indexes for active (platform,channel,video) and
(platform,channel,source SHA-256). All earlier tables, IDs, metadata/review revisions and upload
results are retained. The genuine v5 fixture is a backup of the controlled Stage 7 native database,
including its completed Public history, not a copy of the live user database. Earlier Private-history
fixtures and migration tests remain in the full suite.

Jobs retain source identity/hash, original video ID, platform, channel ID/title, immutable metadata/
review snapshot, Public preference, UTC instant, original wall time/IANA zone/fold, state/reason,
creation/modification/approval/cancellation times and optimistic revision. No credential/session secret
is stored. `execution_enabled=0` is enforced by a database CHECK: Stage 9 must deliberately migrate
that gate and obtain activation consent for existing Stage 8 plans. Approval now is a saved plan,
not automatic-upload authorization.

Events reference retained jobs through a foreign key and record every saved revision. There is no
cascading source-video foreign key: removing the video preserves schedule history and produces an
attention state. Approval hashes off-thread, compares the complete proposal, then takes BEGIN IMMEDIATE
and rereads metadata, review, source stat, channel, duplicates, expected revision and future time.
Both job/event writes roll back together. Competing approvals/edits/cancellations reject stale revisions.

Active plans reserve both video ID and identical content for the same channel. Completed same-source
uploads and unresolved upload attempts block conflicting schedules until the existing explicit
reconciliation process is used. Manual upload creation checks active plans in its own transaction;
users must cancel a conflicting plan before manually uploading it. This protects both directions
without creating an execution worker. Copied independent databases are not a coordinated job store.

## Time rules and state transitions

Python zoneinfo uses installed IANA data. Both fold values are round-tripped through UTC: zero valid
instants means a spring-transition gap, two distinct instants mean ambiguity requiring explicit choice.
Tests cover Chicago DST and half/quarter-hour offsets. Invalid dates/zones and instants at or before
now are rejected; dates through year 9998 are supported by the editor. Stored UTC is authoritative;
OS-zone changes never rewrite approved instants. Future IANA-rule changes affect new conversions,
not saved UTC. Missing IANA data is reported rather than silently using the wrong zone.

- **Draft:** saved, not approved; fully revalidated on approval.
- **Scheduled:** explicitly approved local plan, before its UTC instant.
- **Due:** at the exact instant through strictly less than 60 seconds later; awaiting execution,
  which is unavailable in Stage 8.
- **Missed:** at least 60 seconds overdue and unexecuted. No automatic rescheduling or uploading.
- **Needs Attention:** invalid source/review/channel or conflict; sticky until deliberate reapproval.
- **Cancelled:** deliberate terminal cancellation with retained snapshot/history.

The 60-second boundary is a documented display/evaluation window, not an execution grace period.
Dependency invalidation can take precedence over temporal state, while the original time remains
visible. Clock rollback never returns an observed Due/Missed plan to Scheduled. Repeated evaluation
with unchanged state/reason creates no event. Evaluation runs at real application startup, Queue/
Calendar navigation or reload and every 30 seconds while running; modal scheduling edits pause the
periodic refresh. It uses an injectable aware clock and never invokes publishing endpoints.

## Files

Created:

- `src/shortspublisher/scheduling.py`: time conversion, model, transactional approval/revisions,
  conflict checks, cancellation and local evaluation.
- `src/shortspublisher/ui/scheduling.py`: editor/confirmation, shared worker/controller, queue/calendar.
- `src/shortspublisher/source.py`: shared read-only source integrity helpers.
- `tests/test_scheduling.py`, `tests/test_scheduling_ui.py`, `tests/native_schedule_smoke.py`.
- `tests/fixtures/library-v5.sqlite3`, this report and nonsecret verification evidence.

Modified library.py (migration), uploads.py (shared helper import), upload_store.py (active-plan guard),
app.py and ui/window.py (composition/navigation/context/shutdown/startup evaluation), existing schema
expectations and shell-route tests, README, architecture and roadmap. No lockfile/dependency changes.
Dashboard, analytics and unrelated account features were not redesigned.

## Automated verification

Environment: macOS Apple Silicon, Python 3.12.14, PySide6 6.12.0, FFmpeg/FFprobe 9.0.2.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

**319 tests passed, 0 failures, 0 skips in 61.731 seconds**: all 249 prior tests plus 70 scheduling tests
(56 service/time/storage and 14 Qt UI). Ruff passed; the final formatting check reported 59 files already formatted;
mypy found no issues in 25 source files. Local OAuth listener permission was required for the
existing callback regressions; Google responses remained controlled. No real credentials were used.
Final output: `artifacts/stage8-final/automated-tests.log`.

Coverage includes drafts/approval, no job on preview/cancel, immutable snapshots, changed reviews,
missing/changed/deleted sources, disconnected/wrong channels, duplicate video/hash/upload conflicts,
manual-upload blocking, genuine previous-schema preservation, migration and write rollback, independent
service-instance stale-write rejection, retained approval events, exact Due/Missed boundaries, clock
rollback, restart, sorted distinct jobs, cancellation isolation, IANA/DST/fractional offsets, future/
past limits, forbidden execution activation, no HTTP/credential reads, editor/confirmation, filters,
month navigation/day selection, cross-view synchronization, small layouts, unsaved changes and errors.
All previous OAuth, secure-storage failure, media, metadata, review, upload recovery/secrecy and
shutdown tests remain passing.

Development checks caught syntax/type issues, an attempted assignment to a read-only test UI property,
a missing test import, and two native harness assumptions: waiting for review validation and using
the selected calendar job's stable ID rather than assuming both lists share a current selection.
These were corrected without removing/skipping tests. Visual review prompted higher-contrast calendar
navigation/header styling and a specific missing-source attention message.

## Native macOS verification

```sh
.venv/bin/python tests/native_schedule_smoke.py prepare --root artifacts/stage8-schedules-final
.venv/bin/python tests/native_schedule_smoke.py reopen --root artifacts/stage8-schedules-final
.venv/bin/python tests/native_public_smoke.py prepare --root artifacts/stage8-public-regression
.venv/bin/python tests/native_public_smoke.py reopen --root artifacts/stage8-public-regression
.venv/bin/python tests/native_public_smoke.py history --root artifacts/stage8-public-regression
.venv/bin/python tests/native_upload_smoke.py prepare --root artifacts/stage8-private-regression
.venv/bin/python tests/native_upload_smoke.py reopen --root artifacts/stage8-private-regression
.venv/bin/python tests/native_upload_smoke.py history --root artifacts/stage8-private-regression
.venv/bin/python -m shortspublisher --smoke-test --data-dir /tmp/publisher-stage8-entry
```

The native scheduling workflow uses actual Cocoa widgets, generated playable H.264/AAC portraits,
FFprobe, SQLite and real metadata/review controls. It imports two distinct sources, saves/reviews
Public metadata, opens Schedule Video, approves via actual modal dialogs, verifies Queue and Calendar,
reschedules the selected stable job ID, confirms cancellation of the other job and closes. A second
OS process restores the same scheduled/cancelled IDs and snapshots. Injected clock advances produce
Due then Missed; temporarily unavailable generated media produces Needs Attention. Both source hashes
match afterward. The isolated upload_attempts table remains empty, and every HTTP request is patched
to fail. No scheduling test reads a real credential.

The Private and Public native regressions separately passed with controlled Google responses and
real isolated synthetic Keychain entries; final history phases removed those entries. Their old
Stage 5/7 labels remain in harness output. The application entry point exited code 0 at 10:18:40
local time with scheduling evaluation active. Nine native processes passed in the full workflow;
the scheduling pair was rerun after the last missing-source message correction.

Logs: `artifacts/stage8-final/native-tests.log` and `scheduling-native-final.log`. Captures at
**760×560** and **1180×780** include empty queue, editor, approval, queue/calendar day selection,
reschedule, cancelled history, reopened plans, Due, Missed and attention states. Representative
captures were visually inspected: readable long titles/paths and zone/UTC information, clear local-
plan wording, accessible scrollable controls and no horizontal page overflow. Calendar arrows/header
were improved after inspection. Harnesses supply picker returns and confirmation clicks; manual OS
picker interaction is not claimed. No real Google UI or live upload was involved.

## Preservation and limits

All live table hashes match the Stage 8 pre-work hashes. The actual StonkYa database remains schema 5;
it was not migrated or opened by the new application during tests. The account connection, drafts,
reviews, Private record `bfNDN1Gq540` and Public record `OxgK8zkDmkI` are unchanged. Both original
source hashes still match. All 250 retained ShortsStudio hashes match and its git status is clean.
Evidence: `artifacts/stage8-final/preservation.json`. No real token/session or remote video was changed. Redundant disposable scheduling test roots were
removed after final captures were retained; live/user media and prior-stage evidence were preserved.
Remote visibility was not rechecked; the last live observations remain the Stage 6/7 reports.

- Scheduling is a local planning foundation; it cannot execute, claim an upload, guarantee availability
  or recover an actual scheduled remote transfer. Stage 9 must add that separately.
- Planning intentionally uses stored identity, not live permission verification. Stage 9 must verify
  credentials/channel/scopes, rehash source, revalidate review and duplicates before execution.
- Evaluation uses stat/review bindings rather than rehashing every file every 30 seconds. Approval
  rehashes; future execution must rehash. File/network volumes can stall OS filesystem calls.
- Evaluation scans retained non-draft/noncancelled jobs under a SQLite write transaction; very large
  libraries or slow volumes can delay work or produce a busy error in another process. UI work is
  off-thread; there is no scalability benchmark or filesystem timeout guarantee.
- The app must be running to evaluate time. On reopen it detects overdue plans; it never catches up
  by uploading. Modal schedule editing pauses the timer and later evaluation may mark a plan Missed.
- Revision history is retained in SQLite; the UI presents the current revision and cancelled records,
  not a full historical diff browser. No retention sweeper or packaging work was added.
- Native verification was macOS only. IANA zone data must be installed; Windows/Linux scheduling
  packaging and native behavior were not verified. No power-loss stress test was performed.

Time-zone and widget references checked October 17, 2026:
[Python zoneinfo](https://docs.python.org/3/library/zoneinfo.html) and
[Qt QCalendarWidget](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QCalendarWidget.html).

## Manual workflow and next stage

Close an older app before running updated code. Back up your library with the app closed before
allowing schema 6 migration. Use `--data-dir` for a disposable library when trying scheduling first.
Import a new video, save Public metadata/declarations, complete Review and identify the intended
channel. Open Schedule, choose a future date/time/zone, inspect Review Schedule and explicitly approve.
Check Queue and Calendar, reschedule through renewed confirmation, then cancel a different plan.
Restart and check persistence. A near-future disposable plan will become Due and then Missed without
any upload. Inspect attention feedback after changing its reviewed metadata or moving its source;
restore/review and explicitly reapprove to recover it.

**Stop after Stage 8.** Recommended Stage 9 scope, only after a new explicit request: per-plan activation
consent for existing Stage 8 jobs, transactional execution ownership, fresh identity/review/source/
duplicate checks, and connection to the existing Public uploader with interruption/reconciliation
handling. Missed-job policy must remain explicit. No automatic execution or background service was
introduced in this stage.
