# Stage 15 — Recurring Public posting and queue verification — 2026-10-17

**Stage 15 implementation and verification are complete: 641 tests passed, 0 failures, 0 skips.**

Stage 15 implements finite, explicitly approved recurring Public publishing through the existing
uploader. **Production recurring publishing remains
live-unverified.** No live YouTube upload, real OAuth, production service installation, Stage 16,
packaging or distribution was performed by this verification.

## Workflow and authorization

Videos → Recurring Posting and Publishing Queue → New Recurring Posting Queue open a batch editor.
Only saved, reviewed Public videos are selectable. The connected channel is named and its ID is
disclosed. Users choose a finite batch (1–100 entries), name, rule, days, IANA zone, same-day window,
approved maximum automatic claims and separate optional background permission. New plans start paused.

The confirmation lists exact channel/ID, Public visibility, each video's title, description, tags,
Made for Kids and synthetic-content declarations, review revision, filename, initial order,
cadence, cap, DST behavior, skipped-slot policy and next opportunities. Exact paths, fingerprints
and records remain expandable. Two unchecked inputs require explicit batch approval and confirmation
that no unresolved manual restriction/additional YouTube setting remains. Age restrictions still
require YouTube Studio; “not made for kids” is not an 18+ setting. Cancel creates no plan.

Approval binds immutable source/metadata snapshots to channel, rule, background permission and cap.
It re-fingerprints through existing ScheduleService, then rechecks source identity/review/account,
conflicts and optimistic revision in a transaction. Cancellation before commit rolls back the batch.
Approval never automatically enables global publishing. Activating a plan and enabling global
scheduled publishing remain separate explicit actions. No second dialog appears at each approved
opportunity. Additional videos require another exact batch approval. Rule/channel/background changes
require a new plan; removing unstarted entries permits deliberate reapproval. Merely watching,
importing, selecting, saving or reviewing a new file does not enroll it.

## Cadence, limits and outcomes

- Daily mode uses the selected local start once per selected day. Hourly modes support 1–24 whole
  hours, including 2/3/4-hour presets. Weekdays, weekends and custom selected days are supported.
- Intervals restart at the approved local start each day; the window end is included. Overnight
  windows are rejected. Completion time never shifts subsequent opportunities.
- IANA conversion reuses the existing DST resolver. Nonexistent times are skipped; repeated times
  use only the first occurrence. Persisted opportunities use UTC; presentation names the local
  date/time and zone. Rule values never come from parsing formatted labels.
- Default global budget remains three automatic claims per UTC day, configurable 1–10. Allocation
  uses the smaller current global limit and immutable approved cap, counting all automatic claims.
  Raising global limits does not expand a batch's approved cap. The confirmation explains that
  opportunities can exceed the allowed claims.
- Empty, paused, blocked and missed slots are skipped without consuming a waiting item. Startup
  and resume skip already-due opportunities; a continuously running dispatcher retains the existing
  60-second start window. One bounded skipped-range record covers downtime through the check.
  A persisted watermark and unique slot prevent replay after backward clock movement.
- Invalid items become Needs Attention; the next valid approved item may use that opportunity.
  Individual due jobs take priority. Active transfers, uncertain outcomes and known quota/channel
  restrictions can skip a recurring opportunity while retaining its queue.
- Once reserved/claimed, an item never automatically returns to the waiting queue, even after a
  failed attempt or restart. Existing upload history, explicit resume and reconciliation remain
  authoritative. Reordering/removal cannot reset an execution.

Queue displays ordered saved items, expected next video, pause state, saved review, actual acknowledged
bytes, upload/result states and diagnostics. “Published and verified” requires the existing verified
Public result plus processing success; transferred bytes alone do not establish publication.
Calendar shows eight projected opportunities per plan separately from concrete individual jobs,
labelled as opportunities rather than completed publications. Dashboard summarizes active plans,
waiting videos, attention and next opportunity. Selecting an opportunity with Enter opens its queue.
An unapproved editor preserves changes when Discard/Cancel is cancelled. Long pages scroll vertically.

## Persistence, duplicate identity and execution

Schema 10 additively creates recurring_plans, recurring_items, recurring_slots and recurring_events.
Migration creates no plan/consent, rewrites no older table and rolls back on failure. Genuine
databases were not migrated; isolated migration tests retain old values and prior execution history.

The allocator runs inside the existing AutomaticPublisher claim transaction and ownership/upload/
account locks. It atomically links one item and unique (plan, UTC instant) to an ordinary scheduled
Job, execution/background consent, and the existing execution claim/upload attempt. Item/job links
and content/channel reservations have uniqueness constraints. No second uploader, dispatcher,
background daemon or remote scheduling field was introduced.

The existing uploader still validates source bytes, current saved review/metadata, credentials,
channel, scopes, consent and ownership before initiating Public transfer, and independently verifies
the result. Recurring binding and enabled-plan checks apply again at initiation. Pausing after
claim blocks a new session; an already initiated session retains existing recovery semantics.
Global Stop retains its existing cancellation behavior.

SHA-256 is authoritative. Shared conflicts cover manual publishing, individual schedules and
recurring channel/content reservations; old upload/schedule snapshots cover completed and uncertain
results. Recurring approval conservatively blocks previous noncancelled attempts, even released
failures, and has no automated repost override. Approved content links enter the Stage 14 registry.
Copies/renames do not create new authorization. Source files are never rewritten or moved.

The same allocator runs in the existing authorized headless worker, with separate batch background
consent. It installs nothing. The existing service must run, the Mac must be awake and source files
accessible. A foreground-only plan cannot execute in the background. Watch importing remains local
and independent of Public queue membership.

Research used current primary
[developer policies](https://developers.google.com/youtube/terms/developer-policies),
[required minimum functionality](https://developers.google.com/youtube/terms/required-minimum-functionality)
and [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert).
The implementation uses existing Public insertion, precise finite consent and user-controlled metadata;
it introduces no unsupported restriction field. This research is not a Google compliance certification.

## Automated verification

The final full suite passed **641 tests in 168.847 seconds, with 0 failures and 0 skips**.
Log: artifacts/stage15-evidence/verified-suite-final.log. The baseline's 561 tests are retained; migration expectations now
target schema 10. New coverage comprises 20 cadence tests, 50 queue/execution tests and 10 UI tests.

The final focused queue/UI run passed **60 tests in 10.863 seconds**, without failures/skips.
Ruff passes, formatting reports **114 files already formatted**, and mypy passes **44 source files**.
Evidence: recurring-focused-final.log, ruff-final.log, format-final.log and mypy-final.log.

Coverage includes daily/hourly/window/day rules, both DST transitions, half-hour zones, stale and
cancelled approval, transactional rollback/migration, immutable bindings, append/reorder/remove,
duplicate content across queue/manual/individual/history, no enrollment of new library arrivals,
source/review/channel changes, atomic concurrent claims, daily caps, missed/empty/paused slots,
clock rollback, failed-transfer retention, two distinct controlled Public uploads, pause after claim,
foreground/background consent, checkbox gating, keyboard-accessible controls, cancellation and
conservative status wording. Existing protocol tests retain 308 resume, lost responses, token
refresh, quota, source mutation, ownership, interrupted transfer and remote reconciliation coverage.

Commands:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Complete runs use permission for synthetic localhost OAuth listeners; no real Google authorization.
Intermediate logs remain. The first run used old schema assertions (7 failures, 2 migration-fixture
errors); these fixtures now remove the new additive tables when constructing an older database.
The next run had one new test expecting the media cancellation exception instead of the existing
fingerprint helper's AccountError; the assertion was corrected. No existing test was deleted/skipped.

## Native and separate-process verification

tests/native_recurring.py uses real Cocoa widgets, generated distinct portrait videos, FFprobe,
SQLite and the actual approval dialogs with controlled checkbox/button interaction. It selects a
two-video batch, an every-two-hour rule and exact destination, approves, activates and enables
global publishing, advances the injected clock and obtains a controlled verified Public result.
A second OS process reopens the queue, dispatches the next different video, checks retained
history and pause behavior, verifies original hashes and deletes synthetic credentials/sessions.
Final evidence: artifacts/stage15-native-final and native-prepare-final.log/native-reopen-final.log.

The harness supplies generated import/review fixtures through real local services. Existing native
library/watching harnesses additionally exercise the normal import/edit/review/preview widgets.
An initial reopen attempt competed with its initial account worker and correctly made no claim.
The final fixture explicitly waits for initial workers and passed in fresh separate processes.

tests/native_recurring_continue.py verifies a real isolated LaunchAgent after the prepare GUI exits:
worker parent PID 1, no Qt imported, exact source bytes received, one verified Public result,
second item still queued, restart without another session, and GUI reopen showing stored results.
Final evidence: artifacts/stage15-recurring-background-final/process-evidence.json and
additional-native-final.log. Its final phase removes the test service and synthetic Keychain entries.
No production service was installed and no real credential was inspected.

All **16 existing native regression commands passed**: Private/Public prepare/reopen/history,
account prepare/reopen, individual planning prepare/reopen, foreground automatic prepare/reopen,
background prepare/continuation, service controls and entry smoke. Log: native-regressions-final.log.
Additional library prepare/reopen and watched-folder prepare/reopen passed, alongside the final
recurring background phases (all six commands in additional-native-final.log). Controlled transports
exercise the real uploader; these are not live YouTube uploads.

Native captures use 760×560, 1180×780 and requested 1600×1000, constrained to actual 1600×921 on this
display. JSON records dimensions. Editor, batch consent, Queue, Calendar and Dashboard captures
were visually inspected. No horizontal overflow was observed in checked pages; longer pages and
dialogs use vertical scrolling. No exhaustive accessibility or user-study claim is made.

- [Recurring editor](../artifacts/stage15-native-final/recurring-editor.png)
- [Exact batch approval](../artifacts/stage15-native-final/batch-consent.png)
- [Paused queue at minimum size](../artifacts/stage15-native-final/approved-paused-760-top.png)
- [Future opportunities](../artifacts/stage15-native-final/opportunities-760-top.png)
- [Dashboard](../artifacts/stage15-native-final/dashboard-1180-top.png)
- [Persisted results after reopen](../artifacts/stage15-native-final/reopened-results-1180-top.png)

## Performance and preservation

On the two-item isolated fixture, 100 status reads averaged **0.246 ms**, maximum **5.629 ms**.
Computing eight opportunities averaged **0.053 ms**, maximum **0.169 ms**, over 100 runs.
These are local component timings, not end-to-end UI latency or large-library benchmarks.
Fingerprinting/database work runs on the UI worker. Future projections and skipped ranges are
bounded; no per-video media decoder or second background process was added. No exhaustive memory,
very large queue or network-mounted source performance claim is made. Evidence: performance.json.

Stage 15 snapshots show the genuine artifacts/stage6-live database already at schema **9**, and
the default application database already at schema **4**. Both versions remain unchanged. These
actual starting snapshots supersede older reports' historical schema descriptions.
**18 of 19 live tables and all six default-database tables match.** The sole changed live table is
background_owner: a separate foreground application (PID 88473, Start ShortsPublisher.command)
was running against that live database, and the ownership row identifies that PID with a current
heartbeat. This verification did not launch, stop or modify that separate application. An unchanged
ownership row is not claimed, and it was not restored over an active application's state.

All three genuine source hashes and all 250 ShortsStudio reference files match. The account,
preparations, schedules, attempts and remote IDs bfNDN1Gq540, OxgK8zkDmkI and vmbsEJ4E9Rc are retained.
ShortsStudio git status is clean. Real Keychain/session secrets were not read; preservation relies
on isolated test identifiers rather than comparing secrets. No ShortsPublisher LaunchAgent remains
loaded and production login plists are absent. Evidence: preservation-before.json,
preservation-after.json, preservation-result.json and service-cleanup.json.

## Files and remaining boundaries

New production files: cadence.py, recurring_schema.py, recurring.py and ui/recurring.py.
Modified: library.py, scheduling.py, execution.py, upload_store.py, app.py, ui/window.py,
ui/videos.py and ui/execution.py. New tests: test_cadence.py, test_recurring.py,
test_recurring_ui.py, native_recurring.py and native_recurring_continue.py.
Existing migration assertions, README, architecture and roadmap are updated.

Rules are immutable rather than edited in place. Background permission applies to the exact batch;
there is no future-file profile. Plans cannot cross midnight in one window; create separately
approved plans if needed. Recurring results use the same conservative uncertain-result handling as
individual jobs. The UI shows stored review and periodic snapshots, not a guarantee of current
remote availability. Very large libraries, every timezone/codec/filesystem and OS sleep behavior
were not exhaustively tested. Stage 14's native OS folder-picker acceptance remains unverified.
Production recurring Public posting is live-unverified because no live upload was authorized.
Stage 16 reusable profiles, packaging and distribution are not implemented. **Stop after Stage 15.**
