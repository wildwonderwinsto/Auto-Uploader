# Stage 12 — Video library and preview verification — 2026-10-17

**Stage 12 implementation and verification are complete: 476 tests passed, with no failures or skips.**
No live YouTube upload, OAuth flow, remote modification or production service installation is part
of this stage. Stage 13 and packaging are not implemented.

## Problems and changes

The old library had a short list without search, sorting or workflow filters. Reload cleared the
selected editor. Finding a video required browsing filenames, and preview loading/errors offered
little guidance. The Stage 11 baseline was inspected in a real isolated Cocoa application before
changes (`artifacts/stage12-before/`). ShortsStudio's Library/search organization was read-only
reference material; no component or runtime dependency was copied.

The focused changes are:

- Search filename, saved title, path or local video ID. Whitespace-separated search terms must all
  match. Unicode NFKC normalization and casefold support composed/decomposed accents, full-width
  equivalents and case-insensitive text. There is no fuzzy/accent-removing search.
- Sort by newest/oldest import, filename A–Z/Z–A or shortest/longest duration. Persisted import
  timestamps and inspected duration are used. Stable video IDs break ties; unknown durations sort
  last in both directions. Names use normalized lexical ordering, not locale-specific collation.
- Filter All videos, Needs details or review, Review complete (saved), Has a future plan,
  Has a YouTube result, or Inspection failed. Labels deliberately describe stored evidence.
- Entries show filename, saved title when present, duration and saved workflow status. Full paths
  and titles remain in tooltips. The bounded list grows to show several entries without expanding
  to the entire library. Selecting a different video collapses browsing to leave editing space.
- Search/sort/filter never change the selected editor. A filtered-out selection stays open with
  an explicit message. No matches explains how to recover. Reload, save, review, returning from
  another destination and sorting retain the selected ID and step when it still exists.
- A compact selected-video summary names the current step, saved/unsaved details and review state.
  Removal clears the old summary. Existing disabled upload/schedule reasons and explicit final
  confirmations remain in their respective panels. Save/Discard/Cancel safeguards still apply.
- Preview keeps Play/Pause, mute, aspect-preserving portrait frames and the time display. Seeking
  is enabled only after Qt reports a seekable source. Loading, end-of-playback, missing-file and
  unsupported-format feedback explain the current state. Source changes and release clear the
  decoder, frame, time and range and restore muted audio. Playback is never automatic.

## Meaning of filters

These are projections of existing records, not a new workflow database or upload authorization:

| Filter | Evidence used | What it does not establish |
| --- | --- | --- |
| Review complete (saved) | Existing review rules plus matching saved review revision | Fresh source integrity, account permission or YouTube acceptance |
| Needs details or review | No current valid saved review under those rules | A claim that metadata alone is the only issue |
| Has a future plan | Unclaimed Scheduled job with a future UTC instant | Activation, global enablement or completed publication |
| Has a YouTube result | At least one upload attempt with a returned remote ID | Verified visibility, successful processing or continued remote existence |
| Inspection failed | Persisted inspection error | A fresh scan of all original files |

Multiple plans/results are counted separately. A cancelled/draft/past/claimed plan does not count
as a future unstarted plan. An uncertain result still counts as a returned ID and remains subject
to the original history/reconciliation behavior. Source checks occur on selection through the
existing service, which invalidates review if the file changed or disappeared. No whole-library
filesystem scan or Google request runs during search.

Library data refreshes after save/review/import/inspection, on explicit reload, on returning to
Videos and when expanding the library. It is a snapshot, not continuous monitoring of another
process. Search/filter/sort settings and selection persist within the open UI, not across app exits.
Video metadata and completed review do persist across restart.

## Files and boundaries

New: `src/shortspublisher/library_view.py`, `tests/test_library_browse.py`,
`tests/native_library_browse.py`, and this report.

Modified: `src/shortspublisher/ui/videos.py`, `src/shortspublisher/ui/preview.py`, README,
architecture and roadmap. The projection reads existing tables inside one SQLite read transaction
on the library worker. There is no schema, dependency, OAuth, Keychain, HTTP uploader, scheduler,
background worker or consent change. No source media is rewritten, transcoded or reencoded.

## Automated verification

The first complete run passed **473 tests, no failures or skips, in 108.109 seconds**.
The final focused run passed **29 new tests in 8.295 seconds** after adding three cleanup/selection
regressions. The final complete run passed **476 tests, 0 failures, 0 skips in 109.688 seconds**:
all 447 prior tests and those 29 new tests. Log: `artifacts/stage12-evidence/automated-tests-final.log`.
No existing test was removed or skipped.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Ruff passes; formatting reports **89 files already formatted**; mypy passes **36 source files**.
Logs are under `artifacts/stage12-evidence/`.

New tests cover filename/title/path/Unicode search, no matches, every sort direction and stable
ties, unknown duration, saved-review rules, future/draft/cancelled plan classification, multiple
returned upload IDs, selection and step retention, filtered editing/save/review, unsaved cancellation,
keyboard search/filter controls, changed-source review invalidation, missing/unsupported media,
loading/end feedback, muted source changes and decoder cleanup. Existing responsive layout and
upload/scheduling/ownership tests remain in the complete suite.

An early experiment refreshed browsing when upload-panel work completed; a fixture exposed a race
where that could make the list busy just as selection occurred. That competing refresh was removed.
Collapsing browsing now starts no worker. No-selection summaries clear stale review text. Regression
coverage exercises these behaviors. No production publishing defect was found or altered.

## Native verification and screenshots

```sh
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_library_browse.py prepare --root artifacts/stage12-native-final
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_library_browse.py reopen --root artifacts/stage12-native-final
```

The prepare process imports **24 local portrait MP4s** through the real library worker and FFprobe.
Three files were independently generated with different durations; the others are distinct local
paths copied from generated media to exercise a larger list. No third-party media was used.
It checks empty/one/many entries, a long Unicode filename, no results, saved-title search, sorting,
review filtering, selection retention, metadata save/review and Publish/Schedule navigation with
**zero session initiations**. It plays a real decoded portrait frame, pauses, seeks using the keyboard
and checks the muted/unmuted controls while paused. Native seeking advanced to at least 700 ms.
It exercises a missing file and an actual malformed MP4. Unsupported preview does not alter inspection.

The second OS process reopens the same SQLite library, locates the saved title and verifies the same
video and completed review with zero upload attempts. SHA-256 comparisons cover all 24 originals.
Google services and credential/session stores are controlled in-memory providers; unexpected ordinary
HTTP raises an assertion. No real account is accessed by this harness.

Screenshots include top/bottom views at **760×560**, **1180×780** and requested **1600×1000**.
macOS constrains the last to **1600×921** on this display; exact 1600×1000 native rendering is not
claimed. `prepare-sizes.json` and `reopen-sizes.json` record actual sizes. Representative search,
empty-result, metadata, preview and workflow captures were visually inspected. No horizontal overflow
was observed; minimum-size workflows still use vertical scrolling. Controls remain keyboard accessible.
Before: `artifacts/stage12-before/`. Final after: `artifacts/stage12-native-final/`.
The earlier successful focused run is retained in `artifacts/stage12-native/`.

Representative captures:

- [Before: video details](../artifacts/stage12-before/videos-1-1180-top.png)
- [After: searchable library at minimum size](../artifacts/stage12-native-final/many-videos-760-top.png)
- [After: no matches, selected editor retained](../artifacts/stage12-native-final/no-results-760-top.png)
- [After: paused portrait preview and editing](../artifacts/stage12-native-final/preview-paused-1180-bottom.png)
- [After: schedule navigation at actual 1600×921](../artifacts/stage12-native-final/step-4-1600-top.png)

All **16 existing native regression commands passed**: Private/Public prepare/reopen/history,
account prepare/reopen, planning prepare/reopen, foreground execution prepare/reopen, background
prepare/continuation, service controls and entry-point smoke. The background continuation opens
additional separate GUI processes and exercises the real isolated LaunchAgent and synthetic Keychain.
All Google responses are controlled. Harness-created services/credentials are removed by their final
phases. Log: `artifacts/stage12-evidence/native-regressions-final.log`.
The final two small UI cleanup changes were subsequently covered by the focused native run and full
suite; the underlying upload/service implementations did not change.

## Performance and preservation

On the 24-record native fixture, a measured read snapshot took **0.602 ms**. Across 100 in-memory
search/sort runs, mean time was **0.036 ms**, maximum **0.091 ms**. These are local projection timings,
not end-to-end input latency or a claim about very large libraries. SQLite reading runs off the GUI
thread; keystrokes do no database or media work. Qt retains the existing asynchronous media backend
and one preview player. There is no thumbnail cache or accumulating decoder per selected video.
Resource cleanup is checked by released source/frame/range state; no exhaustive memory profiling was
performed. Evidence: `artifacts/stage12-evidence/performance.json`.

Read-only before/after hashes match all **15 live-library tables**, all three genuine source videos,
and all **250 retained ShortsStudio reference files**. The live database was already schema 8 at
this stage's start and remains unchanged. The genuine Stage 6 Private `bfNDN1Gq540`, Stage 7 Public
`OxgK8zkDmkI` and Stage 9 automatic Public `vmbsEJ4E9Rc` records are preserved. No real credential or
resumable session was read or modified. ShortsStudio git status remains clean. The production
LaunchAgent and login plist are absent, and the Stage 12 test labels were removed.
Evidence: `preservation-before.json`, `preservation-after.json`, `service-cleanup.json` under
`artifacts/stage12-evidence/`.

## Deferred work

No thumbnail generator, fullscreen/expanded player, library pagination or saved search preferences
was added. No new playback format support is claimed; native playback checks used generated H.264/AAC.
HDR, unusual rotation, huge libraries, slow network-mounted sources and all codec failures were not
exhaustively tested. List status is intentionally a saved snapshot. The existing 18+ guidance remains:
use YouTube Studio to apply that restriction; “not made for kids” is not an 18+ setting. No writable
API field or automatic publication path for additional restrictions was invented.

A separately requested next usability stage could use hands-on beginner feedback to refine the
Details/Review forms and remaining terminology. Stage 13, packaging, new upload capabilities and
other platforms remain outside scope. Stop after Stage 12.
