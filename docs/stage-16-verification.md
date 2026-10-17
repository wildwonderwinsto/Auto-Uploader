# Stage 16 — Approved folder-to-YouTube automation — 2026-10-17

**Stage 16 implementation and controlled verification are complete: 699 tests passed, 0 failures,
0 skips.** Native Cocoa, separate-process persistence, real isolated LaunchAgent and prior-stage
regressions passed. **Production folder-to-YouTube publishing remains live-unverified.** No real
upload, real OAuth, production service installation or live migration was performed. Actual OS
folder-picker selection remains unverified, with the attempted interaction and procedure below.

## User workflow

Open **Automatic Posting → New Automation**. Choose an already authorized watched folder or use
**Choose & Authorize a Watched Folder** to open the native directory chooser and existing folder
consent controls. Folder consent remains local-import permission only. Select the connected YouTube
channel, metadata rules, declarations, eligibility limits and recurring rule, then review the exact
scope before approving it. The profile is enabled only after three unchecked confirmations are
explicitly selected. Global publishing and the background service remain separate controls.

The default mode is **Review Before Publishing**. New files receive proposed metadata but stay out
of the queue until their saved metadata/source receives existing individual human review. In
**Approved Automatic Publishing**, the user's standing authorization covers matching future files;
no individual human Review Complete state is fabricated. The confirmation states the user's
responsibility for appropriate content/declarations and unresolved restrictions.

Existing files present at each authorization are excluded by name and recorded file identity.
Renaming an existing source does not confer future-file permission. Place a new completed video
into the authorized directory after approval. Use a temporary extension until writing is complete,
then rename to a supported video extension. The existing quiet period and inspection remain required.
Matching eligible sources enter the associated recurring queue, not an immediate upload. One
posting opportunity starts at most one eligible item through the existing uploader.

Manual Import Videos, Details, human Review, Publish Now, individual scheduling and finite Stage 15
batch queues remain available. Automatic Posting provides View Queue, View Publishing History and
Review Selected Waiting Video. Users can preview/edit proposed metadata in the normal editor.
Changing already approved metadata blocks its old automation snapshot; it is never silently rewritten
or approved again. Remove an unstarted item from the queue before deliberately using the manual
review/publishing workflow for that content.

## Profiles, metadata and authorization

Schema 11 adds automation_profiles, automation_decisions and automation_events inside the existing
migration transaction. No profile, consent or enabled automation is created by migration. Existing
library, upload-attempt and recurring tables are not duplicated. A profile stores its ID/name,
YouTube channel, watched-folder binding, settings, revision, enabled/revoked/removed states, consent,
associated recurring plan, timestamps and activity. Decisions retain exact per-source metadata,
source identity, preparation revision, approval snapshot and recurring item link. Events retain
approval/revocation history. Secrets stay in existing secure providers; no new credential field exists.

Title templates support only the literal **{filename}** substitution, optionally surrounded by a
user-supplied prefix/suffix. Filename stems are NFC-normalized; underscores, whitespace and repeated
separator hyphens are cleaned. There is no eval, attribute access, conversion, arbitrary formatter,
AI generation or truncation. Empty, over-100-character or invalid titles require review. A selected
actual file previews the resulting title. Distinct enrolled content generating the same normalized
title within a profile is conservatively held for manual resolution.

Descriptions are literal, with existing 5,000-byte validation. Tags use existing limits and duplicate
checks. Made for Kids and synthetic-content declarations require explicit Yes/No; neither is guessed.
The confirmation covers PUBLIC visibility, exact folder/channel, declarations, title rule,
description/tags, filename pattern, square/portrait eligibility, maximum duration (1–180 seconds),
maximum source size (up to 20 GiB), cadence, days/window/zone, daily cap and optional background scope.
The existing uploader category remains People & Blogs (22); no new API field or endpoint was added.
Content needing age restriction or another unresolved manual YouTube setting must not be authorized.
“Not made for kids” is not an 18+ setting. No copyright/content-safety classification is claimed.

Profiles do not inherit Stage 15 batch authority. Editing first disables the old approval. Saving
requires a fresh exact confirmation and a new profile revision/recurring-plan binding. Cancelling
the editor leaves the old authorization disabled. Existing queue/history records remain; they are
not silently rebound. Each profile revision creates one plan, never one plan per discovered file.

Pause blocks new enrollment and initiation; resume skips past opportunities. Disable revokes
standing authority and cannot be reversed by Resume alone. Remove retains a configuration tombstone
and history. Folder Pause affects discovery; global publishing Pause affects all automatic posting;
Settings stops the background service; Stop Active Scheduled Upload uses existing transfer controls.
Profile checks also occur during transfer, so revocation may interrupt remaining chunks. Any started
session retains its actual Interrupted/Needs Reconciliation/result history; remote effects are not
reported as undone.

## Enrollment and uploader boundaries

The existing WatchService scanner performs its ordinary stable-source import and then reconciles
profile eligibility using persisted watch_files records. There is no second scanner or uploader.
Reconciliation performs no YouTube operation and creates no upload attempt. Only successfully new
imports qualify; duplicate/manual-library/history discoveries remain conservatively skipped.
This can hold content even if a different-channel repost might be permissible; no automated repost
override or silent path relinking is provided.

Before enrollment, the service checks current folder identity/consent, exact membership, pattern,
media limits, SHA-256 and stable descriptor/path signature. A short BEGIN IMMEDIATE transaction
rechecks profile revision/permission, discovery identity, saved metadata and publishing conflicts,
then creates one immutable automation decision and one ordinary recurring queue item atomically.
Cancellation/disk failure rolls back partial changes. Existing content/channel uniqueness applies
across profiles, finite queues, individual schedules and upload history. SHA-256 covers identical
bytes, not every perceptually equivalent reencode.

Queues are bounded to 100 waiting entries per profile plan; overflow waits locally for reconciliation.
No years-long schedule backlog is generated. Existing daily budgets, DST rules, inclusive same-day
windows, one-item opportunities, ownership locks and missed-slot skipping remain authoritative.
Failed/uncertain attempts never return automatically to the waiting queue or create replacement
sessions after restart.

ScheduleService accepts automation provenance only when explicitly supplied by the linked recurring
snapshot. Manual Publish Now and normal scheduling still require human Review Complete. The upload
boundary requires an actual persisted scheduled execution matching that automation decision, then
revalidates profile activation/revision, folder scope, metadata/source token and eligibility. The
existing uploader independently rehashes bytes and checks credentials, channel, scopes, ownership,
limits and opportunity before transfer, then independently verifies the returned Public result.
No publishAt, remote privacy changes, remote deletion or bypass of reconciliation was introduced.

The same scanner hook and allocator are used by the existing headless worker. Its scanner clock
uses the publisher's injected clock, permitting deterministic tests while production uses real time.
Background enrollment needs both folder discovery permission and profile background permission;
execution also uses existing background consent/ownership. No QtWidgets import was introduced in
production headless modules. The Mac must be awake, service authorized/running and sources accessible.
Actual Stage 16 launchd behavior passed the isolated acceptance described below.

## UI and evidence

The new Automatic Posting destination combines setup, profiles, activity, controls and diagnostics.
It shows persisted names/folders/channels, next opportunity, queued/review counts and latest verified
publication. Existing scanner waiting/error states appear before a profile decision. Queue items
identify automation approval separately from saved human review. Calendar retains the distinction
between projected opportunities and concrete executions. Dashboard adds enabled-profile/global
publishing state. Exact records remain in disclosures. Navigation now scrolls vertically at minimum
size so adding the destination does not compress and clip its labels.

The final **Cocoa** prepare/reopen harness exercised actual dialogs/widgets, local filesystem,
FFprobe, SQLite, scanner, allocator and controlled uploader in two separate OS processes. It
configured a profile, added distinct files after authorization, rejected a renamed duplicate,
verified two distinct controlled Public results and exact received bytes, reopened persistent
records, and tested pause/resume without catch-up. Reopen checked original source hashes. Credential
and session providers were in-memory and ordinary HTTP was patched to fail. The foreground harness
advances the injected clock and calls the existing dispatcher; the independent background test uses
the real worker loop. These are controlled tests, not live YouTube uploads.

Final evidence: `artifacts/stage16-native-final/`, `native-prepare-final.log` and
`native-reopen-final.log`. Earlier successful offscreen and native fixtures remain separate. The
harness records qt_platform explicitly. The Dashboard capture explicitly refreshes saved history;
its stopped foreground timer reflects this deterministic fixture, not a claim that a dispatcher is
running. Real background execution has separate process evidence.

`tests/native_automation_background.py prepare/continue` used a real isolated LaunchAgent and
synthetic Keychain identifiers. After the prepare GUI exited, a new generated file was discovered
without a manual scan, enrolled, and published once at an injected opportunity by the headless
worker. Evidence confirms parent PID 1, no Qt import, synthetic Keychain access, exact received
bytes, verified Public result, worker restart without another POST, and GUI reopen showing the
result. Its final phase removed the test service and credentials/sessions. Evidence:
`artifacts/stage16-background-accepted/process-evidence.json`, `background-prepare-accepted.log`
and `background-continue-accepted.log`.

All **16 prior native regression commands passed**, covering Private/Public prepare/reopen/history,
account, individual planning, foreground/background execution, service controls and entry smoke.
All **eight additional commands passed**: library and watched-folder prepare/reopen, Stage 15 recurring
prepare/reopen and recurring background prepare/continuation. These use controlled providers and
isolated synthetic credential/service identifiers. Logs: `native-regressions-final.log` and
`additional-native-final.log` under `artifacts/stage16-evidence/`.

Native captures cover 760×560, 1180×780 and requested 1600×1000, constrained by macOS to 1600×921.
Actual dimensions are recorded in prepare/reopen JSON. Profile configuration top/middle/bottom,
standing consent, enrolled/published activity, Queue, Calendar and Dashboard were visually inspected.
No horizontal overflow was observed in those captures. Long forms and minimum-size navigation scroll
vertically. No general accessibility or usability study is claimed.

- [Profile configuration](../artifacts/stage16-native-final/profile-editor-0-top.png)
- [Cadence and limits](../artifacts/stage16-native-final/profile-editor-0-middle.png)
- [Exact standing consent](../artifacts/stage16-native-final/profile-consent.png)
- [Enrolled and duplicate activity](../artifacts/stage16-native-final/prepare-enrolled-760-top.png)
- [Two verified controlled results](../artifacts/stage16-native-final/prepare-published-1180-top.png)
- [Persisted background result](../artifacts/stage16-background-accepted/reopened-gui.png)

An early screenshot callback used an incorrect findChildren argument and stalled its modal dialog;
the corrected harness passed and now has a two-minute watchdog. After execution access was restored,
only that confirmed isolated PID was terminated. The separate live application was not stopped.

## Automated checks

Focused tests cover both operating modes, exact scope and unchecked consent, filename/Unicode rules,
invalid metadata, explicit declarations, source edits, existing-file exclusion, renamed existing
identity, duplicates/prior manual publication, competing profiles, concurrent enrollment, migration
preservation/rollback, disk errors, cancellation, background separation, revocation during hashing
and after claim, global/profile pause, missed opportunities, restart, uncertain outcomes, two
controlled verified uploads and UI/navigation/editor safeguards.

The final unrestricted complete suite passed **699 tests in 185.819 seconds**, with **0 failures and
0 skips**: all 641 baseline tests plus 58 new tests. Log:
`artifacts/stage16-evidence/verified-suite-final.log`. The focused suite passed **58 tests in 13.200
seconds** (47 service and 11 UI tests), recorded in `automation-focused-verified.log`.
Ruff passes; formatting reports **125 files already formatted**; mypy passes **47 source files**.
Logs: `ruff-final.log`, `format-final.log` and `mypy-final.log`.

The baseline assertions remain; current-schema expectations move to 11, and historical migration
fixtures remove the new additive tables before reconstructing old schemas. No tests were removed
or skipped. Final harness-only changes add middle-form screenshots and refreshed upload history;
no production code changed after the final complete suite started.

Earlier sandbox runs reported three failures and six errors, all in nine existing OAuth tests
whose loopback callback sockets were blocked. Automatic approval review initially rejected an
execution check because the account usage limit was reached. After the user requested continuation,
a direct retry succeeded; the unrestricted full suite and native checks then passed. No real
Google authorization was used. Intermediate logs remain, but only the final full log is authoritative.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_automation.py prepare --root artifacts/stage16-native-final
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_automation.py reopen --root artifacts/stage16-native-final
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_automation_background.py prepare --root artifacts/stage16-background-accepted
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_automation_background.py continue --root artifacts/stage16-background-accepted
```

Use fresh isolated roots when repeating prepare phases; do not overwrite the preserved evidence.

### Native macOS folder-picker acceptance procedure

On an authorized native session, use a fresh isolated root with:

```sh
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_automation.py prepare --native-picker --root artifacts/stage16-picker-accepted
```

The real picker was opened in `artifacts/stage16-picker-attempt`, but computer use could not attach
to the unbundled Python app (timeoutReached). Only that isolated process was terminated without
selection or profile approval. The successful automated workflow supplies a chooser return value.

For manual acceptance, operate the macOS chooser to select the printed incoming folder, verify its exact path in
folder consent and profile consent, then complete the controlled workflow. Separately cancel the
chooser and verify no folder/profile was created. Injected picker returns test the application
boundary only. **Actual OS picker acceptance remains unverified.**

## Preservation, policy research and limits

Read-only before/after snapshots show genuine schemas unchanged at **9** (stage6-live) and **4**
(default application library). **18 of 19 live tables and all six default tables match**; the sole
changed live table is background_owner. Process inventory confirms the separate live foreground
application PID 88473 is still running against that database, matching the prior-stage observation.
No ownership row was restored or overwritten. All three source hashes and 250 ShortsStudio reference
files match; ShortsStudio git status is clean. The existing account, metadata/reviews, schedules and
upload records remain intact, including remote IDs bfNDN1Gq540, OxgK8zkDmkI and vmbsEJ4E9Rc.
Production login plists are absent. All test LaunchAgents were removed; none remain loaded.
Test synthetic Keychain entries/sessions
were removed by final harness phases. Real secrets were not read. Credential preservation relies on
isolated identifiers, not a comparison of real secret values. `service-cleanup.json` records cleanup.
Evidence: preservation-before.json, preservation-after.json and preservation-result.json.

Research revisited [YouTube developer policies](https://developers.google.com/youtube/terms/developer-policies),
[required minimum functionality](https://developers.google.com/youtube/terms/required-minimum-functionality)
and [videos.insert](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert).
The design requires prior specific standing consent for exact scope and explicit declarations,
channel and visibility; it does not infer a blanket permission from folder monitoring. This is an
implementation interpretation, not a Google audit or guarantee of policy compliance. No real
unattended upload was authorized or performed. Production folder-to-YouTube posting remains live-unverified.

Known limits: future-only initial scope; one connected account at a time; fixed existing category;
no overnight window; no AI/content classification; no automatic age restriction; conservative duplicate
holds; no silent reapproval of edited enrolled metadata; 100 waiting items per plan; local-directory
stability remains a heuristic. Waiting review items may be rehashed on reconciliation; very large
libraries, slow/network filesystems and memory use need additional profiling. The next improvement
should be hands-on OS picker acceptance, reducing vertical scrolling on the long profile form, and
profiling waiting-review rehashing at larger scales. No packaging or later stage was implemented.

New production files: automation_schema.py, automation.py, ui/automation.py. Integration changes are
in library, watching, worker, scheduling, recurring, execution, uploads, app and UI window/recurring.
New tests/harnesses: test_automation.py, test_automation_ui.py, automation_fixture.py,
native_automation.py and native_automation_background.py. README, architecture and roadmap document
this implementation and its explicit remaining limitations. **Stop after Stage 16. No Stage 17,
packaging or distribution work is included.**
