# Stage 18 — Cleanup, organization, AI context and UI text repair — 2026-10-17

**Unexpected Chinese text is fixed.** The cause was the macOS **Region**, not the code or the data.
**714 tests passed** (708 baseline plus 6 new), with zero failures or skips. Ruff, format and mypy are clean.
**All 28 native macOS commands passed**, including real isolated LaunchAgent runs. No real upload,
YouTube request or production LaunchAgent change occurred. **The approved cleanup removed 80 paths
(3,057 files, 542.2 MB); see [Approved cleanup results](#approved-cleanup-results).**

## Starting state

- Stage 17 controlled acceptance was complete (708 tests). Its **optional live
  folder-to-YouTube test is still pending** user authorization. So Stage 18 avoided all
  destructive work except regenerable caches, and left `artifacts/stage17-prepared/` untouched.
- No ShortsPublisher GUI or worker was running. No ShortsPublisher LaunchAgent was loaded, and none
  was in `~/Library/LaunchAgents`. An idle Codex (ChatGPT app) session was attached to the folder;
  it was left alone.
- Git: `main`, one commit, clean tree. `artifacts/`, `.venv` and caches are gitignored.
- Live library `artifacts/stage6-live/library.sqlite3`: schema **9**, 3 videos, 3 upload attempts,
  no watched folders. It was inspected from a copy in a scratch directory; its SHA-256 was the same
  until the first report was written (`90c071f2…`). Application schema: **11**. Afterwards the user
  opened the live workspace (see [Approved cleanup results](#approved-cleanup-results)), which
  migrated it to schema 11.
- Repository: **2.6 GB, 14,931 files**. That breaks down as `.venv` 1.4 GB, `artifacts/` 1.2 GB
  (186 entries), `.mypy_cache` 15 MB, `tests/` 1.9 MB, `src/` 1.5 MB (including caches) and docs 360 KB.
- Baseline suite: 708 tests OK (`artifacts/stage18-evidence/baseline-suite.log`).

## Root cause of the Chinese text

Qt's default `QLocale` comes from the macOS **Region** (`AppleLocale`), not from the interface
language or `LANG`. The code was isolated by setting per-process defaults, without changing any
system setting:

| Region (language English) | `ui/formatting.timestamp()` before | after |
| --- | --- | --- |
| en_US | `Sat, Oct 17, 2026 · 6:30 PM EDT` | unchanged |
| zh_CN | `周六, 10月 17, 2026 · 18:30 EDT` | `Sat, Oct 17, 2026 · 18:30 EDT` |
| zh_TW | `週六, 10月 17, 2026 · 6:30 下午 EDT` | `Sat, Oct 17, 2026 · 6:30 PM EDT` |

The same locale drove `QCalendarWidget` (`十月`, `周日…周六`) and the month and AM/PM text in
`QDateEdit`/`QTimeEdit`. Those widgets appear in schedule editors, recurring and automation time
windows, the Calendar, the Queue, Upload History, the approval dialogs and Accounts. Standard
buttons (OK/Cancel) were never affected, because no Qt translator is installed. On this Mac the
Region is currently en_US, so the bug shows on a Chinese Region (or a Mac previously set to one).

Ruled out:
- `src/` contains no CJK literals, mojibake or U+FFFD.
- The live database has no CJK text.
- Fonts render correctly, and no translation resources exist.

One CJK character is intentional: the tests and native harnesses use **雪** in filenames,
titles, tags and the controlled channel `Controlled test channel — 雪` as Unicode fixtures.
Evidence screenshots from earlier stages show it. It is legitimate user-style data and is unchanged.

### Fix

- `ui/formatting.py`: adds `english_locale()` and `use_english_interface()`.
  - An English Region keeps its own locale (en_GB, en_IN…).
  - Any other Region gets English for its territory; Qt falls back to en_US for China and Taiwan.
  - The original Region is remembered only to choose the 12/24-hour clock in `time_pattern()`.
  - `timestamp()` passes its explicit `locale` argument to `time_pattern()`, so tests that pass a
    locale behave as before.
- `app.py`: `build()` calls `use_english_interface()` before any widget is created. This covers
  the desktop app and every harness. The worker does not use Qt formatting.
- Time-zone conversion, approval data and stored values are unchanged. Displayed strings are never
  parsed back (see the module docstring).

The macOS 24-hour toggle (`AppleICUForce24HourTime`) was never honoured by Qt, before or after.
The clock convention follows the Region, as before. Native macOS dialogs, such as the file chooser,
follow the macOS *language*, which is outside the app's control.

### Regression coverage

- `test_workflow_ui.EnglishInterfaceTests` (5 tests): locale choice; zh_CN/zh_TW timestamps,
  `QTimeEdit`, `QDateEdit` and `QCalendarWidget` in English with each Region's clock; en_US/en_GB
  unchanged; repeated activation keeps the original clock; user Unicode untouched.
- `test_shell.test_chinese_region_renders_english_interface`: builds the real main window under
  zh_CN and scans every page's labels, buttons and combo boxes for CJK or U+FFFD.
- With the fix disabled, these tests fail (3 failures: locale stayed Chinese). With it, they pass.

### Native before/after

`tests/native_ui_audit.py` ran in Cocoa through `artifacts/stage18-evidence/region_run.py`, which
sets `-AppleLocale` for one process. It ran once with the fix disabled (zh_CN) and three times
with it enabled (zh_CN, zh_TW, en_US): 78 screenshots per run, all exits 0.

- [Before — zh_CN Calendar](../artifacts/stage18-evidence/before-zh_CN-calendar.png): `十月`, `周日…周六`
- [Before — zh_CN approval dialog](../artifacts/stage18-evidence/before-zh_CN-activation.png): `Upload starts: 周三, 1月 2, 2030 · 19:00 UTC`
- [After — zh_CN Calendar](../artifacts/stage18-evidence/after-zh_CN-calendar.png): `October`, `Sun…Sat`
- [After — zh_CN approval dialog](../artifacts/stage18-evidence/after-zh_CN-activation.png): `Wed, Jan 2, 2030 · 19:00 UTC` (24-hour kept)
- [After — zh_TW](../artifacts/stage18-evidence/after-zh_TW-activation.png) and [en_US](../artifacts/stage18-evidence/after-en_US-activation.png): `7:00 PM UTC`

The 雪 fixture text stays visible in these dialogs by design.

## Cleanup inventory

Every top-level `artifacts/` entry was classified by its contents, by real-account IDs in its
files or databases, and by references from docs, tests, scripts and other evidence logs.
Full per-entry data, including size, files, videos, databases, references, reason and proposal,
is in `artifacts/stage18-evidence/artifacts-manifest-before.json`.
Production code never references `artifacts/`. The only test reference is `tests/capture_ui.py`,
which writes the 13 root PNGs. No credential files or token/secret JSON keys were found.

| Category | Entries | Size |
| --- | ---: | ---: |
| A — Essential (protected real data / live evidence) | 10 | 4.6 MB |
| B — Historical, keep (cited by reports, or newest run of a kind) | 97 | 677 MB |
| B — Historical, proposed tier 2 (older reruns of the same harness) | 47 | 352 MB |
| C — Disposable candidates, proposed tier 1 | 32 | 190 MB |

**Protected:** `stage6-live`, `stage6-preflight`, `stage7-live-preflight`, `stage9-live` (genuine
sources; the live DB stores their absolute paths), `stage7-preflight`, `stage9-final`,
`stage10-final`, `stage17-evidence`, `stage17-human-picker` and `stage17-prepared`. Real-ID files and
databases exist only in these folders. All three genuine source hashes and the prepared Stage 17
source still match Stage 17's records. ShortsStudio's `git status` is clean, and it was not read.

**Removed automatically (regenerable caches):** `.mypy_cache` (15 MB), `.ruff_cache`, and
`__pycache__` in `src/shortspublisher`, `src/shortspublisher/ui` and `tests` (1.8 MB). Total 16.7 MB.
The verification runs regenerated them, as expected, so net disk savings are about zero.
**Retained:** `.venv` (the LaunchAgent uses absolute interpreter paths) and the fixture SQLite files.

**Uncertain / retained:** `src/shortspublisher.egg-info` (64 KB, stale Oct 15 setuptools output;
the venv uses its own `dist-info`, so it is safe to remove on approval).

### Proposed tier 1 — 32 entries, 189.8 MB (needs approval)

| Path | Size | Files | Synthetic videos | Why it appears unnecessary |
| --- | ---: | ---: | ---: | --- |
| `artifacts/*.png` (13 root files: accounts/analytics/calendar/dashboard/queue/settings/videos at 760 & 1180, native-contact-sheet) | 1.5 MB | 13 | 0 | Stage 1 placeholder captures from `tests/capture_ui.py`; regenerable |
| `artifacts/stage11-after` | 10.8 MB | 70 | 0 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage11-after-first` | 10.4 MB | 68 | 0 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage13-after` | 10.7 MB | 72 | 0 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage13-after-final` | 10.5 MB | 72 | 0 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage13-native-workflow` | 3.7 MB | 26 | 0 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage14-native` | 3.3 MB | 25 | 2 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage14-performance` | 44.2 MB | 154 | 152 | Generated 152-video performance workspace; results kept in `stage14-evidence/performance.json` |
| `artifacts/stage14-performance-final` | 44.2 MB | 154 | 152 | Generated 152-video performance workspace; results kept in `stage14-evidence/performance.json` |
| `artifacts/stage14-watch-background` | 0.3 MB | 9 | 1 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage14-watch-background-final` | 0.5 MB | 12 | 2 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage15-native` | 10.9 MB | 53 | 2 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage15-recurring-background` | 9.4 MB | 50 | 2 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage16-native` | 0.3 MB | 2 | 0 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage16-native-accepted` | 13.8 MB | 78 | 4 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage16-offscreen` | 0.3 MB | 6 | 0 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage16-offscreen-final` | 7.5 MB | 78 | 4 | Earlier/abandoned controlled run; not cited by any report or evidence log |
| `artifacts/stage16-offscreen-verified` | 7.5 MB | 78 | 4 | Earlier/abandoned controlled run; not cited by any report or evidence log |

### Proposed tier 2 — 47 directories, 352.4 MB (needs approval)

These are earlier runs of regression harnesses that later stages re-ran unchanged. Each stage's
pass/fail logs in `stageN-evidence/` remain. Only the run directories (generated media,
throwaway SQLite files, screenshots) would go.

| Kept (newest run) | Proposed for removal (older reruns of the same harness) | Size |
| --- | --- | ---: |
| `stage13-final-accounts` | `stage12-final-accounts` | 4.6 MB |
| `stage13-final-background` | `stage12-final-background` | 8.8 MB |
| `stage13-final-controls` | `stage12-final-controls` | 1.1 MB |
| `stage13-final-entry` | `stage12-final-entry` | 0.1 MB |
| `stage13-final-foreground` | `stage12-final-foreground` | 12.5 MB |
| `stage13-final-planning` | `stage12-final-planning` | 7.6 MB |
| `stage13-final-private` | `stage12-final-private` | 8.1 MB |
| `stage13-final-public` | `stage12-final-public` | 8.9 MB |
| `stage17-library` | `stage14-library`, `stage15-library`, `stage16-library` | 59.7 MB |
| `stage17-recurring-background-final` | `stage16-recurring-background-final` | 9.5 MB |
| `stage17-recurring-native` | `stage16-recurring-native` | 11.0 MB |
| `stage17-verified-accounts` | `stage13-…`, `stage14-…`, `stage15-…`, `stage16-verified-accounts` | 17.4 MB |
| `stage17-verified-background` | `stage13-…` to `stage16-verified-background` | 34.9 MB |
| `stage17-verified-controls` | `stage13-…` to `stage16-verified-controls` | 4.8 MB |
| `stage17-verified-entry` | `stage13-…` to `stage16-verified-entry` | 0.9 MB |
| `stage17-verified-foreground` | `stage13-…` to `stage16-verified-foreground` | 49.6 MB |
| `stage17-verified-planning` | `stage13-…` to `stage16-verified-planning` | 30.1 MB |
| `stage17-verified-private` | `stage13-…` to `stage16-verified-private` | 30.6 MB |
| `stage17-verified-public` | `stage13-…` to `stage16-verified-public` | 33.5 MB |
| `stage17-watching` | `stage15-watching`, `stage16-watching` | 18.6 MB |

`stage7-public` and `stage14-native-final` matched the same name pattern, but their own stage's
evidence log cites them, so they are kept. Verification after any approved removal: rerun the
manifest script and check that every `artifacts/` link in `docs/` and `README.md` still resolves.
Then re-hash the protected sources and the live DB, and rerun the full suite. **Potential saving:
542 MB (tier 1 + tier 2), about 44 % of `artifacts/`.**

Archiving instead of deleting is not recommended. Moving cited folders would break about 220
report links, and moving the protected folders would break the live DB's absolute source paths.

## Code organization

The package is already reasonably structured, so no files were moved.
- `src/shortspublisher/` is flat by domain, with a `ui/` subpackage that services never import.
- Every module has a live entry point: `__main__.py` via `-m`, and `worker_entry.py` via the
  absolute path in the LaunchAgent plist (`background.py:52`). Moving it would break installed
  services.
- Ruff found no unused imports, and there are no orphan modules or resource files.
- The tests are flat: `discover` runs only `test_*.py`, and the `native_*.py` harnesses import
  sibling fixtures by script directory. Many reports and evidence logs cite their documented paths.

Moving files would add churn and break references without improving maintainability.

## Documentation

- **New `CLAUDE.md`** (about 100 lines): purpose, commands, module map, schema/migrations, protected
  data, safety rules, conventions, and where to look.
- **New `docs/current-status.md`**: verified capabilities, pending live work, limitations and priorities.
- **New `docs/development.md`**: environment, checks, a 15-row native harness catalogue, the
  locale-override technique, the migration chain, and an AI-assistant workflow.
- **`docs/architecture.md`**: the title "Stage 10 boundaries" is now "ShortsPublisher architecture",
  with a short guide. The stale "Schema version 8" is corrected to 11. A Stage 18 interface-language
  section was added.
- **`README.md`**: the Stage-17-specific header is replaced by a current overview with links. The stale
  schema 8/9 statements are corrected, and the module list now covers recurring, automation,
  background and formatting.
- **`docs/roadmap.md`**: Stage 18 note.

## Files changed

`src/shortspublisher/ui/formatting.py`, `src/shortspublisher/app.py`, `tests/test_workflow_ui.py`,
`tests/test_shell.py`, `README.md`, `docs/architecture.md`, `docs/roadmap.md`; new `CLAUDE.md`,
`docs/current-status.md`, `docs/development.md` and this report (7 tracked files changed: +178/−21
before this report). Evidence is in `artifacts/stage18-evidence/` (1.2 MB). No schema, publishing,
credential, uploader, scheduler or LaunchAgent code changed.

## Verification

| Check | Result |
| --- | --- |
| Full suite | **714 tests OK in 190.0 s**, 0 failures, 0 skips (`final-suite.log`) |
| Ruff / format / mypy | pass / 131 files formatted / 47 source files, no issues |
| Native regressions (Cocoa) | **28/28 commands exit 0** (`native-regressions.log`) |
| Native locale audit | 4 runs × 78 screenshots, exit 0 |

The 28 native commands cover:
- Public, Private and history flows, accounts, individual planning, and foreground automatic publishing.
- Real isolated LaunchAgent background publishing and continuation, plus service controls.
- The `--smoke-test` entry point and library browse/preview/search/sort/filter.
- Watched folders with SHA-256 duplicate detection, and recurring foreground/background.
- Automatic folder profiles foreground/background: approval, enrollment, renamed-duplicate skip,
  pause/resume and restart persistence.

All use isolated roots in a scratch directory, controlled Google responses and synthetic
Keychain/LaunchAgent identifiers. Afterwards, `launchctl list` and `~/Library/LaunchAgents` had no
ShortsPublisher entries, and no ShortsPublisher processes remained.

**Background worker:** `worker_entry.py` is unchanged and was exercised by the real isolated
LaunchAgent harnesses (Stage 10, 15 and 16 background flows passed). The production label
`com.shortspublisher.publisher` was never installed.

**User data:** the live DB and its backups/logs were unchanged (same hash). All 3 genuine source
videos and the prepared Stage 17 source match their recorded hashes. Real credentials were not read,
and no Google/YouTube request was made with them. Stage 18 never launched the real app on the
live workspace; the later migration came from the user's own launch (see below).

Not verified: a person operating the real macOS picker this stage (not affected by the change);
real Chinese-Region system settings (simulated per process instead); and real YouTube behaviour.

## Approved cleanup results

The user approved tier 1, tier 2 and `src/shortspublisher.egg-info`.

**Unexpected event, handled before any deletion.** The first pre-deletion check found the real app
running on `artifacts/stage6-live`. It had been started at 17:41 from a Terminal by the user's
launcher, not by Stage 18. Opening it migrated the live library from schema 9 to **11** (file
header `0x0b`). The launcher's schema-9 backup is
`stage6-live/backups/library-before-launch-20261017T224140686820Z.sqlite3`. No upload occurred
(the log shows navigation only). Deletion was paused and reported. The user then said to proceed,
and the app had closed normally at 17:48. The migration was kept; no restore was requested.

**Revalidation (read-only, before deletion).** All 79 candidates were checked against
`artifacts-manifest-before.json`:
- bytes, file counts and modification times were unchanged since the manifest;
- no symlinks, no paths outside `artifacts/`, and no real-account IDs;
- not cited by any doc (other than this report), code, test, launcher or `pyproject.toml`;
- tier 1 had no evidence-log citations;
- every tier-2 superseding run still exists;
- no candidate is a live-DB source folder, and all protected paths exist.

**Zero problems were found.** The egg-info was confirmed unused: `importlib.metadata` resolves
`shortspublisher` to `.venv/.../shortspublisher-0.1.0.dist-info`, the editable `.pth` points to
`src/`, and nothing references the egg-info.

**Removed** (each explicit path, one at a time; log in `artifacts/stage18-evidence/cleanup-removed.json`):

| Group | Paths | Bytes |
| --- | ---: | ---: |
| Tier 1 (tables above) | 32 | 189,802,782 |
| Tier 2 (tables above) | 47 | 352,432,150 |
| `src/shortspublisher.egg-info` | 1 | 12,837 |
| **Total** (3,057 files) | **80** | **542,247,769** |

| Measure | Before cleanup | After |
| --- | ---: | ---: |
| Project on disk (`du`) | 2,678,064 KiB (2.55 GiB) | 2,140,992 KiB (2.04 GiB) |
| `artifacts/` | 1,235,900 KiB, 186 entries | 698,864 KiB, 108 entries |
| Files | 14,972 | 11,920 |
| `.venv` (untouched) | 1,422,440 KiB | 1,422,440 KiB |

The actual reduction is 537,072 KiB (550 MB on disk). The "after" figures include the new
post-cleanup logs.

**After deletion:**
- All 107 files in the 10 protected folders hash identically to the pre-deletion baseline. That
  covers the live DB and its backups/logs, the three genuine source videos and the prepared Stage 17
  source. The source hashes still match Stage 17's records.
- All 107 kept entries exist, and all 86 doc links resolve.
- `tests/fixtures/library-v1…v7.sqlite3` are present. The only code/test reference to `artifacts/`
  remains `tests/capture_ui.py`.
- One doc mention, `artifacts/stage16-picker-accepted`, was already absent before Stage 18. It is
  only the `--root` of a suggested manual command in the Stage 16 report.
- Full suite: **714 tests OK** (181.4 s, 0 skips). Ruff passes; 132 files are formatted; mypy
  reports no issues in 47 files.
- **All 28 native commands exit 0** (`post-cleanup-native-regressions.log`).
- No ShortsPublisher LaunchAgent is loaded or installed, and no process remains. No credentials
  were read, and no YouTube request was made.

## Remaining items and recommendations

- Tier 1, tier 2 and `src/shortspublisher.egg-info` were approved and removed (below).
- **Pending from Stage 17:** the authorized live folder-to-YouTube test. The live library is
  already at schema 11.
- `tests/capture_ui.py` still writes into the `artifacts/` root. A future change could give it
  `--root` like the other harnesses.
- Limitations unchanged: Mac must be awake/logged in; no 18+ API setting; schedule page scrolls at
  small sizes; no packaging. See `docs/current-status.md`.
- Recommended next: (1) the Stage 17 live acceptance, (2) the approved artifact cleanup,
  (3) live-use polish. Packaging and new platforms come later.

**Stop after Stage 18.** No Stage 19, packaging, distribution or new platform work was started.
