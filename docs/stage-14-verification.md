# Stage 14 — Watched folders and persistent duplicate detection — 2026-10-17

**561 tests passed, 0 failures, 0 skips.**

Stage 14 implements local discovery and importing only. No automatic review, metadata generation,
schedule creation, upload attempt or remote request is authorized by monitoring. Stage 15–16,
packaging and distribution are not implemented. The native macOS picker acceptance itself remains
an explicitly noted verification limitation below; the native application workflow after chooser
selection, background execution and cross-process persistence were exercised.

## Behavior and architecture

Multiple folders can be explicitly enabled from Videos → Automatically Import Videos → Watch a
Folder. The chooser uses `QFileDialog.getExistingDirectory` with native defaults. A confirmation
shows the canonical path, eligible existing-file count, initial existing/new-only choice, quiet
period and separate optional background permission. No default existing-file choice or checked
consent is supplied. Monitoring requires both. Root identity is rechecked before recording consent.

Configurations persist a stable ID, selected and canonical paths, display name, root device/inode,
enabled/removed flags, initial choice, foreground/background permissions, consent/creation/update
instants, quiet period, revision, last successful scan, result and error. Pause/resume uses optimistic
revision checks. Remove is a retained configuration tombstone: original files, library entries and
content history remain. Change Folder requires pausing the old root and giving fresh consent for
the new location. The old root stays paused until removed. Re-add can explicitly include formerly
ignored files or exclude existing waiting files; previously confirmed identities remain known.

`watching.py` is independent of Qt and remote publishing. It uses the existing `LibraryService`
Inspector and the extracted `Library.save_in_transaction`, preserving normal video records,
inspection results, preview, search/sort/filter, Details and Review. No incompatible second library
exists. Auto-import does not infer Shorts readiness from a filename or mark review complete.

`ui/watching.py` runs all discovery/inspection/hash/database work on a cancellable worker thread.
The app starts reconciliation on opening, including while Dashboard is visible. Notifications use
QFileSystemWatcher with a one-second debounce; periodic reconciliation runs every ten seconds and
reattaches watches after disappearance. A busy scanner skips competing ticks instead of building
an unbounded queue. Root changes are detected on the next scan. Headless discovery uses the same
scanner and ten-second polling without importing Qt. Notifications are hints, not durable events.

Research sources were [Qt QFileSystemWatcher](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QFileSystemWatcher.html)
and [Apple FSEvents guidance](https://developer.apple.com/library/archive/documentation/Darwin/Conceptual/FSEvents_ProgGuide/UsingtheFSEventsFramework/UsingtheFSEventsFramework.html).
Watch removal, event coalescing and resource limits motivate periodic reconciliation. No new FSEvents
binding or dependency was added.

## Stability and recovery

Discovery is nonrecursive. Candidate extensions are MP4, MOV, WebM, MKV, M4V and AVI; dotfiles,
other/temporary suffixes are ignored. Symbolic-link roots/ancestors are rejected and file symlinks
are not imported. Filesystem roots are rejected. A replaced directory at the same path is not
silently trusted: its pinned device/inode must match or it needs removal and renewed consent.

Before new content is imported, it needs nonzero size, at least two matching observations and a
15-second default quiet period (configurable 1–3600 seconds). Signatures contain size, nanosecond
mtime/ctime, device and inode. Hashing uses SHA-256, an opened descriptor and O_NOFOLLOW on the final
component. Descriptor/path signatures are checked, FFprobe must successfully inspect the new media,
and file/root identity is checked again after inspection and before the final commit. No database
transaction is held while reading video bytes or running FFprobe.

States distinguish waiting, ready for inspection, importing, imported, already in the library,
duplicate content, known publishing history, unsupported/invalid video, missing file and attention.
Transient progress is cleared at scan completion. Technical FFprobe output remains in diagnostics;
ordinary activity gives readable recovery guidance. Invalid media is retryable, with exponential
30-second-to-one-hour delay. A changed signature resets evaluation. Zero-byte and changing files
continue waiting. Cancellation commits no partial new video; configuration revision/permission is
checked again at commit. Disk/database failure rolls back the library and registry together.
Permission and root failures remain visible and recover on later scans. Shutdown cancels and joins
workers. Filesystem calls on an unresponsive mount may still wait on the OS.

Stability is a heuristic, not proof that an external writer has closed the file. A writer can pause
longer than the quiet interval. The recommended handoff is to complete and close a temporary file,
then atomically rename it to the final video extension. Network/cloud-mounted sources, provider
placeholders and hostile filesystem races are not certified; use ordinary local directories.

## Migration and duplicate identity

Schema 9 adds `watch_folders`, `watch_files`, `content_registry` and `content_videos` with uniqueness
constraints/indexes. The existing initialize transaction performs the additive migration; rollback
is tested. No old table is rebuilt or reset in this migration. A schema-8 headless worker refuses
to run until the GUI migrates the database. Actual live databases were deliberately not migrated
by verification and remain schema 8; isolated databases exercised version 9.

A per-database OS file lock coordinates GUI and headless scans across processes. The final SQLite
BEGIN IMMEDIATE transaction rechecks permission/revision, current library identities, publishing
history and source identity, then atomically inserts at most one new library video and registry
record. SHA-256 is authoritative for identical bytes; paths and filesystem metadata are preliminary
checks/cache keys. Confirmed unchanged files are not repeatedly hashed. Registry links survive
removal/rename/restart; existing duplicate library IDs and metadata are retained, never merged.

Existing manual library files are lazily fingerprinted before unknown intake. Cached signatures
avoid rehashing. An unavailable legacy source without a registry/hash snapshot blocks unknown
intake conservatively; restore it or explicitly remove its library entry. Existing source paths and
metadata are never silently replaced. Renaming an imported original therefore leaves its saved
path missing while the renamed discovery links to known content. This prevents duplicate ingestion
but does not silently relink the editor to another file.

Upload snapshots and scheduled source hashes provide channel-specific evidence, including queued,
scheduled, uploaded and uncertain outcomes. Diagnostics retain each channel and record separately.
All matching history is conservatively treated as known content, including old cancelled/draft
plans; no attempt to infer new publication eligibility is made. Uncertain history cannot become a
new automatic candidate. This stage adds no universal cross-channel publication result and no
upload outside-app discovery. SHA-256 does not identify visually similar reencodes. Existing manual
publishing preflight is unchanged; Stage 15 must explicitly apply content/channel eligibility when
allocating any future recurring opportunity.

## UI and background behavior

Videos retains its library landing. Selecting/importing still intentionally opens Details. Discovery
refreshes entries without replacing selection, step, search/filter/sort or unsaved metadata. Results
and counts describe the latest per-file snapshot, not lifetime totals. Full paths, IDs, hashes and
per-channel history are available under Folder Settings and Diagnostics. Expanded activity scrolls
vertically at minimum size; it introduces no horizontal overflow in the captures checked.

Background permission is per folder and unchecked by default. It does not install/start a service
or authorize publishing. When the existing separately authorized LaunchAgent is running, its local
scanner runs in a separate cancellable thread, independent of publishing pause. Folder Pause stops
local discovery; Stop stops the worker. Publishing ownership and approved job dispatch remain
unchanged. The worker now resolves FFprobe using its PATH or standard Homebrew locations. A custom
GUI `--ffprobe` path is not persisted to launchd; nonstandard installations can remain attention-only.

An initial actual launchd run caught FFprobe being absent from launchd's restricted PATH. It failed
closed with a persisted attention result. Standard macOS path lookup corrected this. A fresh
background prepare/continuation run tested GUI exit, worker parent PID 1, no Qt import, local import,
worker restart, duplicate arrival, GUI reopen and service removal. No account was configured and
all Requests HTTP was patched to fail closed in every process. No test credential was needed.

## Automated verification

The final complete suite passed **561 tests in 155.837 seconds, with 0 failures and 0 skips**:
all 507 baseline tests plus 54 new tests. Log:
`artifacts/stage14-evidence/verified-suite-final.log`. The final focused suite passed **54 tests in
8.410 seconds** (`watching-focused-final.log`). Ruff passes; formatting reports **104 files already
formatted**; mypy passes **40 source files**. The final activity-list scrollbar-only adjustment was
also covered by the focused suite and final native prepare/reopen run. No backend code changed
after the full-suite run started.

New tests cover consent/config persistence, both initial-file choices and explicit re-consent,
quiet/stability/slow writes, symlinks, nonrecursive scanning, temporary and invalid files, retry,
permission/missing/root replacement, hash-time mutation, cancellation, pause during inspection,
transactional migration and disk-full rollback, manual-library and retained-duplicate integration,
per-channel uploaded/uncertain/scheduled history, background permission, concurrent threads and
separate processes, SQLite uniqueness, native chooser boundary, missing-notification reconciliation,
notification debounce/reattachment, responsive UI during hashing and unsaved editor preservation.
The original 507 tests remain; schema-version expectations changed to 9 without deleting/skipping
prior assertions. New network behavior is not exercised because none was added.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

The first sandboxed full run failed only the nine localhost OAuth tests because callback sockets
were blocked. Unsandboxed runs use synthetic providers and loopback listeners, not real Google
consent. Intermediate development logs are retained. Only the final log is authoritative.

## Native and cross-process evidence

`tests/native_watching.py prepare/reopen` runs the real Cocoa application, SQLite, generated media,
FFprobe and Qt preview. A physical file is incrementally written/flushed; deterministic clock
advancement verifies that each changed chunk remains pending, then the completed stable video
imports once. Notification callbacks are disabled in this deterministic-clock fixture; a separate
native notification/timer harness verifies automatic discovery without manual scans. Native playback advances beyond 300 ms. Search finds it. Copies and renames do not
create another independent record. A second OS process reopens the persisted folder and registry,
imports a distinct video once, verifies Pause blocks another arrival, then resumes it safely.
Source hashes are checked and no preparation, schedule or upload attempt is created.

The native folder chooser API was opened, but the available computer-use surface could not attach
to this unbundled Python GUI (invalid application/timeout). It was closed without a verified OS-level
selection. The automated Cocoa workflow supplies the chooser's returned isolated path, then uses
the actual consent controls/dialog and real services. Unit coverage verifies cancellation and both
required consent inputs. **Manual acceptance of the macOS picker itself remains unverified.** No
claim is made that the injected selection tested the OS picker. `--native-picker` remains available
for a hands-on run against a new isolated root.

`tests/native_watch_notifications.py` separately observed **one real native directory event** and
**one normal periodic timer tick**, importing a new generated video in **10.388 seconds** with
**zero manual scan calls**. It used an explicitly configured one-second quiet interval. Evidence:
`artifacts/stage14-notifications/result.json` and `native-notifications.log`. This complements the
deterministic slow-copy fixture and the missed-notification UI regression.

Final native captures use 760×560, 1180×780 and requested 1600×1000 (actual 1600×921 on this display).
JSON records actual dimensions. Configuration, waiting, import, duplicate, invalid, paused and
resumed views were captured; representative images were visually inspected. No general accessibility
or exhaustive large-library usability audit is claimed.

- [Consent](../artifacts/stage14-native-final-pass/consent.png)
- [Waiting for completion](../artifacts/stage14-native-final-pass/prepare-waiting-760-top.png)
- [Duplicate results](../artifacts/stage14-native-final-pass/prepare-duplicates-1180-bottom.png)
- [Invalid media recovery](../artifacts/stage14-native-final-pass/prepare-invalid-760-bottom.png)
- [Paused folder](../artifacts/stage14-native-final-pass/reopen-paused-760-top.png)
- [Reopened background import](../artifacts/stage14-watch-background-verified/reopened-final.png)

All **16 existing native regression commands passed**: Private/Public prepare/reopen/history,
account prepare/reopen, local planning prepare/reopen, foreground execution prepare/reopen,
background prepare/continuation, service controls and entry smoke. These use controlled Google
providers with isolated synthetic credentials/services and cleanup. Log:
`artifacts/stage14-evidence/native-regressions-final.log`. The native library prepare/reopen tests
also passed with 24 generated videos, real decoding/seeking and unchanged original hashes
(`library-prepare.log`, `library-reopen.log`). Final watched-folder/background runs cover subsequent
local scanner/presentation corrections; uploader/scheduling implementations were not altered.

A native harness iteration also exposed timer activity racing a disabled Pause button. The final
deterministic fixture stops competing callbacks and asserts the actual persisted pause/resume states.
This changed test scheduling, not production pause semantics.

An intermediate background verification process loaded modules while development edits were in
progress; its reopening screenshot exposed an old/new scan-signature mismatch. A fresh process run
on the final code added explicit folder/error assertions and replaced that evidence. Likewise, an
early preview harness called the unloaded media player directly; it was corrected to exercise the
normal preview action. These intermediate attempts are not the final acceptance evidence.

## Performance and preservation

On 151 real local fixture files (150 identical small videos plus a distinct 33,623,030-byte padded
valid video), initial observation took **0.081 s**, stable ingestion **0.387 s**, and unchanged rescan
**0.046 s**. Exactly two library entries were created. The unchanged scan performed **zero hashes**;
all fixture source hashes matched afterward. Evidence: `performance.json`. These local timings
are not an end-to-end UI latency benchmark.
A separate UI test keeps its event loop responsive while a hash is delayed. No multi-gigabyte,
network filesystem, exhaustive memory or very-large-library claim is made.

Read-only Stage 14 before/after snapshots match all **15 tables in both genuine databases**, all
three genuine source videos and **250 ShortsStudio reference files**. Both schemas remain 8. The
existing StonkYa account row and remote IDs `bfNDN1Gq540`, `OxgK8zkDmkI`, `vmbsEJ4E9Rc` are retained.
Evidence: `preservation-before.json`, `preservation-after.json`, `preservation-result.json`.
No original source media or real remote record was modified. Real Keychain/session secrets were
not read; credential preservation rests on unchanged credential code and isolation, not comparison
of secret values. No real OAuth or YouTube operation was performed. No prior plan gained authority.
Production login plists remain absent; temporary LaunchAgents were removed
(`service-cleanup.json`). ShortsStudio git status remains clean. No source, dependency
or runtime from ShortsStudio was copied.

New production files: `watch_schema.py`, `watching.py`, `ui/watching.py`. Modified production files:
`library.py`, `worker.py`, `app.py`, `ui/videos.py`, `ui/window.py`. New tests:
`test_watching.py`, `test_watching_ui.py`, `native_watching.py`, `native_watch_background.py`,
`native_watch_notifications.py`.
Existing migration assertions, README, architecture and roadmap were updated. This report and
`stage-14-plan.md` document scope and evidence.

## Manual usage and remaining boundaries

1. Open Videos → Automatically Import Videos → Watch a Folder and select a local directory.
2. Review existing-file count, choose include-existing or new-only, choose quiet time, then check
   local-import consent. Background discovery is a separate optional checkbox.
3. Finish a video under a temporary extension and rename it into the folder. Wait for reconciliation
   or use Scan Now; stability still requires multiple observations and the quiet interval.
4. Review Import Results, then select the video in the ordinary library to preview/edit/review.
5. Pause/resume locally as needed. To change location/options, pause/remove and explicitly consent
   again; retained library entries and duplicate history are preserved.

Excluded paths remain excluded across restart; changing that policy requires explicit re-add.
Folder moves/replacements need fresh consent. Monitoring state is persistent; browsing/navigation
preferences retain their prior session-local behavior. Background discovery depends on an already
running authorized worker, local source access, FFprobe and an awake Mac. OS resource limits,
cloud-provider behavior, all codecs and every race with an external writer were not exhaustively
tested. No sources are rewritten, transcoded, renamed or deleted by this feature.

Stage 15 may add explicitly approved recurring opportunities for already eligible reviewed videos,
with channel/content uniqueness, missed-slot skipping, DST/day/window/daily limits and the existing
publisher ownership/preflight/reconciliation. Stage 16 may separately bind reusable profiles with
precise declarations and renewed consent. **Neither is implemented. Stop after Stage 14.**
