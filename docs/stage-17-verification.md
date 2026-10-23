# Stage 17 — Real-world acceptance and targeted fixes — 2026-10-17

**Controlled Stage 17 acceptance is complete: 708 tests passed, zero failures or skips.** The user
operated the real macOS folder picker, including cancellation; the selected folder and profile
persisted across a separate-process reopen. Native foreground and real isolated LaunchAgent checks
passed. **Live folder-to-YouTube verification remains pending: no real publishing approval was
provided, no real upload occurred, and no new genuine YouTube video ID exists for this stage.**

## Initial application state and preservation

Fresh process inspection found **no ShortsPublisher GUI or worker running and no loaded LaunchAgent**.
The Stage 16 PID 88473 was historical, not assumed alive. The established launcher still points to
`artifacts/stage6-live/library.sqlite3`. Read-only inspection found schema **9**, global publishing
paused, daily cap **3**, and the old foreground ownership row marked Stopped. Its historical PID and
heartbeat were retained. No process was stopped and no ownership or claimed-execution row was reset.
The default application database remains a separate schema-4 database.

The live library has three completed upload records, no in-progress transfer, and no future approved
job. Its old Due job is historical and linked to the Stage 9 result, not a fresh posting opportunity.
The earlier IDs `<youtube-video-1>`, `<youtube-video-2>` and `<youtube-video-3>` remain untouched. This is stored evidence;
Stage 17 did not contact YouTube to recheck those remote videos or refresh the real account.
Evidence: `artifacts/stage17-evidence/initial-state.json`.

A consistent SQLite backup was created using the backup API at
`artifacts/stage17-evidence/live-before-optional-migration.sqlite3`; integrity_check returned **ok**.
No live migration was performed. Both original database versions remain unchanged. Any later live
continuation must repeat current process/transfer inspection and obtain authorization before opening
or migrating the real workspace; the old inspection is not permission to stop another application.
Backup evidence: `backup.json`.

Final read-only snapshots match **all 19 live tables and all six default-database tables**, including
account, library, saved preparation/review, schedules, executions, attempts and ownership. All three
genuine source hashes and all **250 ShortsStudio reference files** match. ShortsStudio git status is
clean. No real credential or resumable-session secret was read. Secret preservation rests on isolated
test identifiers and unchanged secure-storage code, not inspection of real secrets.
Evidence: `preservation-before.json`, `preservation-after.json`, `preservation-result.json`.

## Actual macOS picker acceptance

The user agreed to operate an isolated Cocoa app with controlled providers. The first real chooser
was cancelled. The harness then asserted **zero watched folders and zero profiles**. In the second
chooser the user used Command–Shift–G, selected the instructed folder and confirmed completion in
conversation. No picker return was injected for this run.

Selected canonical path:
`<project folder>/artifacts/stage17-human-picker/incoming`.
The returned path included a trailing slash; normal canonicalization stored the exact directory
without it. The real folder-consent dialog showed that canonical path, checked by the harness before
it accepted controlled local-import consent. The controlled profile binds the same folder ID,
device/inode and consent instant. The harness supplied subsequent metadata and controlled approval
clicks; the user's manual actions in this check were the chooser cancellation and selection, not
real YouTube publishing consent.

The old harness's 40-second worker wait included time spent in the modal human interaction. It
reported a timeout after the folder and profile had been successfully committed. The harness limit
now allows ten minutes for manual picker runs. A separate Cocoa process reopened that same database,
verified the exact canonical path and consent/profile binding, displayed the restored profile and
asserted zero upload attempts. The timeout was a test-harness timing issue, not a failed picker or
lost application configuration. Full controlled publishing was separately verified in the final
native fixture below; the interrupted picker run is not represented as a completed publishing run.

Evidence: `artifacts/stage17-human-picker/picker-cancel.json`, `folder-consent.png`,
`picker-result.json`, `picker-reopened.png`, plus `human-picker-prepare.log` and
`human-picker-reopen.log` under `artifacts/stage17-evidence/`.

- [Exact selected folder consent](../artifacts/stage17-human-picker/folder-consent.png)
- [Folder and profile restored in a new process](../artifacts/stage17-human-picker/picker-reopened.png)

## Confirmed UI problems and focused changes

The Stage 16 editor placed 18 fields, day controls and preview in one long scroll. It now has four
small setup pages: **Folder** (including channel, mode and background scope), **Details**, **Schedule**
and **Limits**. Back/Next and direct page tabs preserve the draft. Cancel and Review Exact Automation
Scope remain visible outside the scrolling page. The final consent dialog, unchecked confirmations,
explicit declarations, future-file scope and separate global/service controls are unchanged. The
review action still validates the whole draft, including fields on other pages. No authority is
created by navigating between pages. Some schedule/detail content still scrolls at small sizes.

The selected-profile overview previously showed Enabled without the global pause state beside it.
It now also shows folder discovery state, global automatic publishing paused/enabled, and the last
background-service status (including Not checked). Enabled profile authorization is not presented as
proof that a background worker is running. Existing per-file and upload outcome wording is retained.
These are periodic local snapshots, not continuous remote verification.

Native screenshots were inspected for each setup page, consent and activity at the supported main
window sizes 760×560, 1180×780 and requested 1600×1000 (actual 1600×921 on this display). No horizontal
overflow was observed. Navigation/actions remain keyboard-accessible; no broad accessibility audit
or user study is claimed.

- [Folder and channel](../artifacts/stage17-native-final/profile-editor-0-top.png)
- [Video details](../artifacts/stage17-native-final/profile-editor-1-top.png)
- [Schedule](../artifacts/stage17-native-final/profile-editor-2-top.png)
- [Eligibility limits](../artifacts/stage17-native-final/profile-editor-3-top.png)
- [Distinct operational states](../artifacts/stage17-native-final/prepare-enrolled-760-top.png)

## Review-waiting performance

A local fixture of **30 distinct valid MP4s totaling 255,835,010 bytes** confirmed that every
reconciliation rehashed every video awaiting human review. The generated MP4s include padding to
represent larger byte reads; these measurements do not model complex codec decoding or network disks.
Three before runs took 116.8, 115.4 and 115.1 ms, with **30 hashes each**.

The focused fix defers expensive hashing when an existing decision is specifically waiting for
individual review, the saved metadata/revision and media token still match, the watch fingerprint
matches the decision, the current size/mtime/ctime/device/inode signature matches the observation,
source checks pass, and review is still incomplete. This branch only retains a hold: it cannot
create a queue item or authorize an upload. No new fingerprint cache or schema is introduced.

Review completion, metadata edits or identity changes leave that branch. **Fresh SHA-256 is still
required before enrollment**, and the uploader retains its independent rehash immediately before
transfer. Restart and concurrent reconciliation cannot turn a waiting hold into approval. The
existing local-filesystem stability limitations remain; metadata is not proof of unchanged bytes
against a hostile writer, which is why the shortcut never authorizes enrollment.

The three after runs took 17.7, 17.9 and 18.2 ms with **zero hashes** while waiting. All source hashes
matched afterward, with zero queue entries and zero upload attempts. These are component timings on
this Mac, not an end-to-end latency or very-large-library claim. Reproduce with
`PYTHONPATH=src:tests .venv/bin/python tests/benchmark_review_waiting.py <output.json>`.
Evidence: `review-performance-before.json` and `review-performance-after.json`.

## Controlled publishing and regression evidence

The complete suite passed **708 tests in 189.842 seconds, zero failures and zero skips**: all 699
baseline tests plus nine regressions. The focused profile/UI suite passed **67 tests in 15.738
seconds**. The new tests cover unchanged waiting holds across service restart, mandatory rehash after
review, source/metadata invalidation, concurrent reconciliation, exact-filename single-publication
scope, draft preservation across setup steps, visible small-window actions and explicit global pause.

Ruff passes; formatting reports **128 files already formatted**; mypy passes **47 source files**.
Logs are under `artifacts/stage17-evidence/`: `verified-suite-final.log`, `focused-final.log`,
`ruff-final.log`, `format-final.log`, `mypy-final.log`. No baseline test was deleted or skipped.
The only changes after the full suite began were harness/manual-wait/evidence additions; production
code was unchanged throughout that run.

The final Cocoa prepare/reopen workflow passed with generated originals, real FFprobe, SQLite,
actual profile/consent widgets and controlled Google responses. It enrolled two different videos,
skipped a renamed duplicate, used two recurring opportunities through the existing dispatcher,
independently verified controlled Public results and exact bytes, then reopened history and tested
pause/resume without catch-up. Source hashes remained unchanged. It uses an injected clock and
explicit dispatcher calls, not real elapsed wall-clock posting or live Google services.
Evidence: `artifacts/stage17-native-final/`, `native-prepare-final.log`, `native-reopen-final.log`.

The real isolated LaunchAgent workflow also passed. After GUI exit, a new file was discovered by
the normal scanner loop, enrolled once, and uploaded once through the existing background allocator
and uploader at an injected opportunity. Process evidence confirms parent PID 1, no Qt import,
synthetic Keychain use, exact received bytes and independent controlled Public verification.
Restart created no second POST; reopening showed the stored result. Test service and synthetic
credentials/sessions were removed. Evidence: `artifacts/stage17-background-final/process-evidence.json`,
`background-prepare-final.log`, `background-continue-final.log`.

All **16 previous native commands** passed (Private/Public history, accounts, individual planning,
foreground/background execution, service controls and entry smoke), as did **eight additional native
commands** (library, watched folders, Stage 15 recurring foreground/background). They retain isolated
SQLite roots, controlled Google providers and synthetic credential identifiers. Evidence:
`native-regressions-final.log`, `additional-native-final.log`. Final cleanup found no ShortsPublisher
GUI/worker, loaded LaunchAgent or production login plist: `service-cleanup.json`.

## Optional live test: prepared, not authorized or executed

A new original disposable source is prepared outside the empty future folder:
`artifacts/stage17-prepared/source/Stage17_acceptance_2e516f8375a3.mp4`.
It is a six-second 540×960 H.264/AAC MP4 containing a generated test pattern and synthesized tone,
638,448 bytes, SHA-256 `20387b515f084f23ddc6c20750e8bbe3b42c21ea950af295f13f1aa41342d69f`.
FFprobe confirms the streams. Its bytes differ from all three genuine existing source videos.
No existing production content was reused. This is not AI-generated publishing content.

The proposed isolated source folder is `artifacts/stage17-prepared/incoming`, still empty. Proposed
metadata and the exact filename are recorded in `artifacts/stage17-prepared/proposal.json`; source
inspection is in `prepared-source-inspection.json`. A future profile should use the **exact filename**
as its eligibility pattern, a one-claim approved cap, and one daily opportunity (same start/window-end)
chosen in the user's intended IANA zone. No global limit was changed. One physical source, exact
filename scope, terminal enrollment identity and content/channel deduplication constrain the intended
publication; unrelated filenames and identical renamed copies do not receive a second enrollment.
The added controlled regression verifies one attempt across dispatch/restart/duplicate arrival.
This does not give permission to add more content or renew/edit the test profile afterward.

Before a live continuation, the user must authorize preparation of the real workspace/migration,
personally review the source and exact the owner's channel destination, explicitly select audience and synthetic
content declarations, approve the exact standing Public scope in the app, and personally enable/start
the required real background/global controls. No such live approval or activation happened in Stage
17. Real account permissions and Google restrictions have not been freshly checked. Production
background installation remains absent. No power setting was changed.

Only after those actions may the source be handed off via a completed temporary file renamed into
the authorized folder. The normal background scanner and scheduler must do the work. Record the
real opportunity, durable claim and pre-POST intent separately from an observed network-send time;
none of those live timestamps exists yet. Use the existing independent verification/reconciliation
workflow, never a replacement upload for an uncertain outcome. After the user-approved test, pause
only its disposable profile; removal of a real service or remote video requires separate permission.

Live channel/Public/processing verification, genuine new video ID, live restart/no-duplicate evidence
and post-live profile pause are therefore **pending**, not claimed by controlled results.

## Files and remaining recommendations

Production changes are limited to `automation.py` (review-waiting hold) and `ui/automation.py`
(setup steps and operational status). Tests extend `test_automation.py` and `test_automation_ui.py`;
`native_automation.py` now captures all steps and supports manual cancellation with a longer timeout.
New `native_picker_reopen.py` and `benchmark_review_waiting.py` retain reproducible evidence.
No schema, credential storage, uploader protocol, cadence, ownership or publishing-limit change was
introduced. README, architecture and roadmap point to this report.

Next acceptance work is the optional personally authorized live test. Further polish can reduce the
Schedule page's day-control scrolling and test waiting-review performance on genuinely large or slow
local libraries. Current results do not certify network filesystems, hostile filesystem mutation,
all codecs, sleeping/logged-out Macs or Google account eligibility. **Stop after Stage 17. No
packaging, distribution, new platforms or Stage 18 work was performed.**
