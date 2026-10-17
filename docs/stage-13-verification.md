# Stage 13 — Workflow and information design verification — 2026-10-17

**Stage 13 implementation and verification are complete: 507 tests passed, 0 failures, 0 skips.**

Stage 13 implements intentional library/editor navigation and human-friendly presentation.
Packaging, distribution, live uploads, real OAuth, production service installation and Stage 14–16
implementation are outside this stage. Existing source media and remote uploads were not modified.

## Root causes and workflow corrections

Previously `VideosPage.activate()` selected the previous video again on navigation. Editor visibility
was also controlled by `MainWindow.update_upload_context()`: existing history could reopen the card
and switch to Publish even without a video. Selection, editing and history were consequently coupled.
Imports reset only on an ID change and started at Source, while same-selection callbacks retained
an unrelated workflow tab. A single-file picker offered no batch behavior. The old Review link
opened review without saving, leaving the next action blocked by unsaved details.

Videos now has explicit in-memory library, editor and history modes. Main navigation opens a clear
library with Import Videos, search/sort/filter and optional Continue Editing naming the retained
video. No disabled editor tabs appear on landing. Selecting/importing intentionally opens Details;
the File tab retains inspection and removal without being a mandatory intermediate step. Back to
Library is explicit. A new ID always starts at Details. Continue Editing restores the same session's
step, rechecking a saved source. Save and review saves successfully before navigating; failed saves
retain the buffer and current view. Review offers Publish now and Schedule without authorizing either.

Multi-file import runs serially through the unchanged local import service on the library worker.
The first successful file is selected; all successes remain in the library. Per-file failures appear
under keyboard-accessible Import errors. Importing an already known path opens that entry's Details.
A cancelled unsaved-edit guard prevents the import. No media is rewritten or transcoded.

Back, changing videos, reload, import, removal and leaving for another destination retain the existing
Save/Discard/Cancel guard. Clicking Videos while already editing may park the buffer without changing
it; Continue Editing restores it intact. Closing still asks about unsaved work. Navigation state is
not stored in the database or restored on restart; saved metadata and review remain persistent.

A UI context revision prevents a late selection/import result from reopening editing after a later
navigation. One worker owns library operations; conflicting refresh/import actions are disabled.
Browsing refreshes cannot switch modes. Removed selections return to the library. Missing source files
retain actionable feedback and invalidate saved review under existing rules. History is explicitly
accessible without a selected video and cannot implicitly become an upload target. Queue history
links use this destination while retaining the intended attempt selection.

## Page audit and information hierarchy

| Page | Immediate purpose and action | Additional/technical information |
| --- | --- | --- |
| Dashboard | Recognizable channel, publishing status, Prepare a video, recent results | Channel ID in tooltip; existing publishing limits disclosure; no PID/raw timestamps in summary |
| Library | Import or intentionally select/continue a video | Saved-state explanation in tooltip, per-file import errors expandable |
| Details | Preview and edit, Save and review | Optional tags/disclosure guidance remain expandable; file inspection in File → File information |
| Review | Check content/declarations, complete review, choose Publish or Schedule | Existing source checks and warnings retained; readable review date |
| Publish/History | Explicit visibility actions, actual transfer progress and result labels | Raw attempt/session status, returned IDs, exact timestamps and recovery remain under Upload details and recovery |
| Schedule | Named date, locale clock, selected IANA zone, resolved start time | Exact UTC/local ISO, fingerprint and path in schedule/activation diagnostics |
| Queue/Calendar | Title, readable approved start time, channel and activation/outcome | Approved IANA zone in row tooltip; identifiers, revisions, UTC and approval history remain expandable |
| Accounts | Name and useful handle, connection and permission actions | ID, description, last verification and exact UTC moved to Channel details |
| Settings/background | Service status and explicit enable/start/stop controls | Existing Advanced controls and diagnostics keep PID, heartbeat, paths and internal ownership; consent unchanged |
| Confirmations/errors | Exact channel ID, visibility, metadata/declarations and explicit final consent | Source hash/path/byte count moved behind diagnostics; actionable existing error messages retained |

The dark theme remains. Disclosure sections no longer stretch vertically and create large empty gaps.
The Dashboard's preparation action is placed before the status card. At minimum size the library
landing fits its essential actions and browsing controls; longer editor/consent pages scroll vertically.
No data was removed from troubleshooting surfaces. No upload success is inferred from acknowledged
bytes. “Video processed” requires the stored verified outcome plus processing success; uncertain
results say “Check upload result.” Saved review still does not establish remote permission or success.

## Date/time, duration and progress rules

`ui/formatting.py` is shared by Videos, Accounts, scheduling, execution, history and Dashboard:

- Convert aware instants using `ZoneInfo`; plans use their approved IANA zone. Other activity uses
  the current system zone. No formatted string is parsed back into a plan.
- Named, locale-translated months/days and an explicit year avoid numeric month/day ambiguity.
  Ordinary timestamps use `Oct 17, 2026 · 3:30 PM CDT`; schedule rows also include weekday.
- QLocale's clock convention selects 12- or 24-hour display. Nonzero seconds are retained. The
  schedule input retains seconds, a calendar popup and a named-month date; serialized local ISO
  input and the existing DST resolver remain unchanged.
- Repeated fall-back times display different DST abbreviations. The existing occurrence choice
  remains explicit; nonexistent spring-forward local times remain errors. No approved instant moves.
- Invalid/naive timestamps are explicitly unavailable rather than assigned a guessed zone.
- Durations use seconds/minutes/hours; file sizes use binary units. Transfer progress reflects only
  actual acknowledged bytes. Preview's elapsed/total clock remains a conventional playback clock.
- Exact ISO/UTC, byte counts and revisions remain in intentional diagnostics (including tooltips).

There is no new application preference store: clock/locale follow system preferences. The schedule
zone is selected per plan; ordinary historical activity follows the system zone.

## Automated verification

The final complete run passed **507 tests in 132.340 seconds, with 0 failures and 0 skips**:
all 476 baseline tests plus 31 new tests. Log: `artifacts/stage13-evidence/verified-suite-final.log`.
Final results are recorded in `artifacts/stage13-evidence/`.
The final native visual audit and Stage 13 workflow run both passed. The scheduling UI focused
run passed **14 tests in 10.142 seconds**, with no failures or skips (`scheduling-final.log`).
Ruff passes, formatting checks **94 files**, and mypy passes **37 source files**.
The 476-test baseline is retained; existing assertions were updated for intentional UI behavior,
not removed or skipped. New tests cover mode transitions, batch imports/partial failures, new video
identity, same-video continuation, unsaved cancellation/save/parking, history refresh and initial-load
races, missing/removed sources, date conversion, both DST boundaries, years, half-hour zones,
12/24-hour locales, diagnostics, literal metadata preservation and conservative status wording.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Localhost-only OAuth tests require execution outside the filesystem/network sandbox; their first
sandboxed run failed because callback listeners were blocked. No real Google authorization was used.
During development, tests caught Qt moving away from disabled hidden tabs, row highlighting before
mode transition, and wording expectations tied to the old UI. These were corrected. Intermediate
logs are retained; only `verified-suite-final.log` is authoritative. An earlier 507-test run
loaded the old schedule-confirmation assertion before its update and reported one failure; the
updated focused scheduling suite and the fresh full run both passed.

## Native verification and visual comparison

Before changes, `tests/native_ui_audit.py` captured all existing routes and editor steps in an isolated
Cocoa app. Final audit captures include Dashboard, Videos steps, Queue, Calendar, Accounts, Settings,
Private confirmation and automatic activation. `tests/native_workflow.py` exercises batch import,
unsaved Cancel/park/Continue/save/review, explicit history and account diagnostics with zero sessions.
The native library prepare/reopen harness uses 24 generated portrait videos, actual FFprobe and Qt
decoding, muted playback, pause/keyboard seek, errors, search/filter/sort and separate-process restart.
Original fixture hashes remain unchanged and no upload is initiated by either local-only harness.

All **16 existing native commands passed** (`native-regressions-final.log`). They cover Private/Public prepare/reopen/history, account prepare/reopen,
planning prepare/reopen, foreground automatic prepare/reopen, background prepare/continuation,
service controls and application entry smoke. Google responses are controlled; test-only Keychain
entries and LaunchAgents are isolated and removed by their final phases. No production service is
installed. The native regressions exercise synthetic upload transport; these are not live uploads.

Screenshots use 760×560, 1180×780 and requested 1600×1000. macOS limits the large window to 1600×921
on this display. Actual dimensions are recorded in the harness JSON files. Native screenshots were
visually inspected, including landing, editor, Accounts, schedule input, Dashboard, Queue and final
consent. No horizontal overflow was observed. Keyboard navigation and disclosure tests remain active.

| Before | After |
| --- | --- |
| [Videos at minimum size](../artifacts/stage13-before/videos-1-760-top.png) | [Intentional library landing](../artifacts/stage13-workflow-final/library-760.png) |
| [Accounts](../artifacts/stage13-before/accounts-None-760-top.png) | [Simplified identity](../artifacts/stage13-workflow-final/account-760.png) |
| [Queue](../artifacts/stage13-before/queue-None-760-top.png) | [Readable plan time](../artifacts/stage13-final-visual/queue-None-760-top.png) |
| [Private confirmation](../artifacts/stage13-before/private-confirmation.png) | [Essential consent with expandable source diagnostics](../artifacts/stage13-final-visual/private-confirmation.png) |

## Persistence, preservation and files

Read-only snapshots cover the live `artifacts/stage6-live` database and default application database,
all three genuine source videos, and 250 retained ShortsStudio reference files. Stage 13 does not
read real credential/session secrets. Native credential operations use isolated test services; no
real account is refreshed. No source file or YouTube record is changed. The live schema remains 8.
The Stage 13 before/after snapshots match all 15 tables in both databases, all three sources,
and all 250 reference files (`preservation-result.json`). The retained remote IDs are
`bfNDN1Gq540`, `OxgK8zkDmkI` and `vmbsEJ4E9Rc`. No ShortsPublisher LaunchAgent remains loaded;
production login plists are absent. Real Keychain contents were deliberately not read, so their
preservation claim rests on unchanged credential code and use of isolated test identifiers, not
on an inspection of secret values. The Stage 13 baseline was captured during implementation before
any production-data operation; no claim is made that the user's live database still matches the
older Stage 12 snapshot.

New files: `src/shortspublisher/ui/formatting.py`, `tests/test_workflow_ui.py`,
`tests/native_workflow.py`, and this report. UI changes are in videos, window, metadata, components,
accounts, scheduling, execution and uploads. Relevant existing automated/native tests adapt to
multi-file selection, explicit library/history navigation and relocated diagnostics. README,
architecture and roadmap are updated. Backend services, schemas, dependencies, OAuth, Keychain,
HTTP upload, scheduling rules and background execution implementations are unchanged.

## Remaining limitations and future stages

The five editor panels remain available; Details is now the entry point and File is optional.
Long metadata/consent forms still need vertical scrolling at minimum size. Search/filter/sort and
editing navigation are session-local. Library status remains a snapshot. Large libraries, every
locale, every codec and network-mounted source performance were not exhaustively tested. System
locale supplies translated date names/clock conventions; the application UI itself remains English.
No custom locale/time-format preference panel was added. No user study or accessibility audit beyond
native visual/keyboard checks is claimed. Age restrictions still require YouTube Studio; “not made
for kids” is not an 18+ setting.

The roadmap and architecture now describe Stage 14 local watched-folder discovery/stability/import
and persistent content deduplication; Stage 15 approved recurring cadence execution; and Stage 16
explicitly approved reusable profiles connecting intake to automatic posting. They include filesystem
notifications plus reconciliation, SHA-256/stable identity, cross-library/queue/history/uncertain-result
deduplication, no missed-slot bursts, daily/windows/day rules, precise declaration and channel consent,
and global/folder/video pause. None of these features is implemented. **Stop after Stage 13.**
