# Stage 3 verification — 2026-10-15

Stage 3 is complete within the verification limits below. This stage adds local publishing
metadata and explicit review; no accounts, API calls, uploads, queues, or scheduling were added.

## Implemented functionality

Each imported video has editable YouTube title, multiline description, individual tags,
explicit audience and synthetic-content declarations, and planned privacy (Private by default).
Live counters enforce review limits of 100 title characters, 5,000 UTF-8 description bytes,
and 500 combined tag characters, including commas and the documented quotation allowance
for tags containing spaces. Duplicate tags are normalized and avoided. Empty descriptions
are allowed. Incomplete or invalid drafts can be saved without claiming review eligibility.

Rules were checked against the official [video resource documentation](https://developers.google.com/youtube/v3/docs/videos),
[insert documentation](https://developers.google.com/youtube/v3/docs/videos/insert), and
[disclosure guidance](https://support.google.com/youtube/answer/14328491) on October 15, 2026.
Title/description angle brackets are rejected for review. Both declarations require an explicit
choice; animation or AI assistance alone does not automatically imply disclosure is required.

The Videos page now has Video Information, Publishing Details, and Review tabs. Save Draft
and Review Preparation form a direct workflow. Saved/unsaved feedback, inline validation,
blocking errors, nonblocking warnings, and local review status are visible. Save/Discard/Cancel
guards protect selection changes, navigation, refresh/reload, removal, close, and application Quit.
Saving only resumes a pending action after the database write succeeds.

Review checks saved metadata, source availability, inspection validity, Shorts readiness,
and explicit declarations. Silent video is a warning. Draft, Needs Attention, and Review Complete
are local states. Approval is bound to a metadata revision and media state, and changes invalidate
it. Checks and writes run through the existing background operation boundary.

## Files created and modified

Created:

- `src/shortspublisher/preparation.py`: independent metadata rules, review evaluation, storage.
- `src/shortspublisher/ui/metadata.py`: editor, counters, tags, and active-tab sizing.
- `tests/test_preparation.py`: real SQLite persistence, migration, validation, review and race tests.
- `tests/test_metadata_ui.py`: editor and guard workflows against actual storage.
- `tests/fixtures/library-v1.sqlite3`: genuine Stage 2 schema fixture with generated-media metadata.
- `tests/native_metadata_smoke.py`: native prepare/reopen workflow and screenshot capture.
- `docs/stage-3-verification.md`: this report.

Modified:

- `src/shortspublisher/library.py`: schema migration, inspection revisions, metadata operations.
- `src/shortspublisher/ui/videos.py`: tabs, review, save guards and worker result handling.
- `src/shortspublisher/ui/window.py`, `src/shortspublisher/app.py`: navigation/close/Quit guards.
- `src/shortspublisher/ui/theme.py`: consistent editor and tab styling.
- `tests/test_library.py`: current schema expectation.
- `README.md`, `docs/architecture.md`: usage, storage, boundaries and limitations.

No Python dependencies were added; `pyproject.toml` and `uv.lock` are unchanged.

## Database migration and data safety

Schema version 2 migrates version 1 inside one transaction. It preserves existing video IDs,
paths, inspection data and timestamps while rebuilding the video table with AUTOINCREMENT
and adding an inspection revision. IDs are not reused after removal, preventing delayed work
for a deleted video from targeting a newly imported video.

The preparations table uses `(video_id, platform)` as its key, with a cascading foreign key,
YouTube metadata JSON, revision/modification time, and reviewed revision/media token/time.
Only YouTube is implemented. Saving uses an expected revision to reject stale concurrent edits.
A recheck cannot silently adopt another window's revision for an unsaved draft. Successful and
failed inspections invalidate reviews transactionally. Removal deletes associated metadata only.

The v1 fixture was backed up from the genuine Stage 2 native database and populated with a
known generated-media row. Tests verify migration preserves it, repeated initialization works,
failed migration rolls back DDL/data, and future schema versions are refused. Injected write
failures verify rollback and recovery. No existing user database was reset or used for test data.

## Exact checks and results

Environment: macOS Apple Silicon, Python 3.12.14, PySide6 6.12.0, FFmpeg/FFprobe 9.0.2.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Final full suite: **92 passed, 0 failed, 0 skipped in 18.283 seconds**. Ruff: all checks passed.
Formatting: 26 files already formatted. Mypy: no issues in 13 source files. Deliberately induced
storage/media errors may produce diagnostic logs; they are expected test paths.

The suite includes existing Stage 1 navigation/lifecycle and Stage 2 media/library tests, plus
Unicode/emoji byte boundaries, tag rules, all declarations/privacy, incomplete drafts, exact
persistence, review completion/invalidation, missing/changed sources, silent-video warnings,
refresh and removal, failed saves, stale revisions, wrong-video prevention, and small layouts.
Real media tests executed with generated playable fixtures; none were skipped.

Native application entry-point smoke also passed with startup and clean exit code 0:

```sh
.venv/bin/python -m shortspublisher --smoke-test --data-dir /tmp/publisher-stage3-native-entry
```

## Native macOS workflow

Final confirmation commands (Cocoa, two separate OS processes):

```sh
.venv/bin/python tests/native_metadata_smoke.py prepare --root /tmp/publisher-stage3-completion
.venv/bin/python tests/native_metadata_smoke.py reopen --root /tmp/publisher-stage3-completion
```

Both printed their passed assertion summary and completed with exit code 0. The first generated
and imported a playable two-second portrait H.264/AAC MP4, entered all fields (including Unicode,
line breaks, URL and tags), saved and explicitly completed review. The second process verified
exact persisted metadata and completed review, changed the title, saved, confirmed invalidation,
and completed a new review. A 101-character title visibly blocked completion. Both processes
verified the source SHA-256 was unchanged and the window closed successfully.

The harness supplies the system file-picker result and drives actual Qt widgets; inspection,
storage, workers and review logic are real. This is automated native integration, not a claim of
manual OS file-picker interaction.

Screenshots from the same final implementation are retained in `artifacts/stage3-verified/`,
at 1180×780 and 760×560, with top/bottom captures for editing, saved, review complete,
reopened review, invalidation, repeat review and blocking errors. Representative captures at
both sizes were visually inspected. Text wraps; save/review/edit controls are reachable through
vertical scrolling; no horizontal overflow was detected. Active-tab height now follows its
content rather than leaving the editor's excess height in the review tab; a regression test covers it.

All 250 tracked reference-file hashes in the task's ShortsStudio snapshot remain unchanged.
The retained generated source hash also matches its pre-workflow hash. Original user videos
were not used or modified.

## Remaining limitations

- Windows and Linux were not tested. This remains a development checkout, not an installer.
- Local review does not establish platform acceptance, policy compliance, or rights clearance.
- Source changes are checked on selection/recheck/review operations, not by a continuous watcher.
  The review token binds inspection and file-stat data; it is not a full video content hash.
  An external change after a check can only be detected at a later check.
- FFprobe inspection retains Stage 2's timeout and defect-detection limits.
- Transaction failure/recovery and restart persistence were tested; abrupt power-loss and every
  possible disk failure were not simulated.
- Native widget automation does not verify manual OS picker or all assistive-technology interactions.
- Explicit official-guidance links open a browser only on user action; no automatic network service
  or YouTube integration is implemented.

## Launch and manual check

```sh
.venv/bin/python -m shortspublisher
# Optional isolated library:
.venv/bin/python -m shortspublisher --data-dir /tmp/publisher-stage3-manual
```

FFprobe must be on PATH, or pass `--ffprobe /opt/homebrew/bin/ffprobe`.

1. Open Videos, import a playable portrait or square Short, and open Publishing Details.
2. Enter title, description and tags; explicitly choose both declarations and planned privacy.
3. Save Draft, then open Review and Mark Review Complete when no blockers remain.
4. Close and reopen; select the same video and verify its fields and review state.
5. Edit a field to see Draft; save and complete a new review. Try an invalid title to see blockers.
6. With unsaved edits, select another video, navigate away, refresh, or close; exercise Save,
   Discard and Cancel. Confirm cancelled actions keep edits and failed saves do not navigate away.
7. With a disposable source, remove/change it externally and recheck; restore and refresh inspection.
8. Resize to 760×560 and scroll through both editor and review actions.

## Suggested Stage 4

After explicit approval, consider official YouTube OAuth and read-only channel identity with
secure credential storage and disconnect/error handling as a separate tested increment.
Uploading and scheduling should remain a later stage. No Stage 4 work was implemented.
