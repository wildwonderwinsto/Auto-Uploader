# CLAUDE.md — ShortsScheduler (package `shortspublisher`)

Read this first. It is a map, not the full history. Then read `docs/current-status.md` and, only
if the task needs it, the relevant section of `docs/architecture.md`. Stage reports are references.

## What it is

A local-first macOS desktop app (PySide6) that imports short vertical videos, prepares YouTube
metadata, and publishes them — manually, on an approved schedule, from recurring queues, or from
watched folders under standing approval. **YouTube** is implemented and live-verified. **TikTok**
is implemented through Buffer's API (Stage 19) and live-verified once: an automatic Public post to
@mrcenterr (TikTok video 7698923752935017741). Further real posts still need the user's authorization. Instagram, Facebook
and Snapchat are future work. No packaging/distribution exists yet.

Stages 1–19 are done (Stage 17's optional live folder-to-YouTube test is still pending the user's
personal authorization). After Stage 19 the UI was redesigned as an all-platform uploader named
**ShortsScheduler** (display name only: the internal app/organization name, data folder, Keychain
services, LaunchAgent and package stay `ShortsPublisher`/`shortspublisher` — never rename them).
Never start a new stage without explicit instruction.

## Commands

```sh
.venv/bin/python -m shortspublisher --data-dir /tmp/sp-test        # isolated run
./"Start ShortsPublisher.command"                                   # USER's live workspace — don't run
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests   # 812 tests, ~6 min
cd tests && QT_QPA_PLATFORM=offscreen PYTHONPATH=../src ../.venv/bin/python -m unittest test_shell  # focused
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
```

Native macOS harnesses (`tests/native_*.py`, run with `QT_QPA_PLATFORM=cocoa`) are listed in
`docs/development.md`. They use disposable `--root` directories, controlled Google responses and
isolated Keychain/LaunchAgent identifiers. Python 3.12 in `.venv` (uv), PySide6 6.12, FFprobe on PATH.

## Layout

`src/shortspublisher/` is flat by domain; `ui/` holds Qt pages. Services never import `ui`.

| Area | Modules |
| --- | --- |
| Entry | `app.py` (composition, CLI), `__main__.py`, `worker.py` + `worker_entry.py` (launchd worker) |
| Library/media | `library.py` (SQLite + migrations, `SCHEMA_VERSION`), `media.py` (FFprobe), `readiness.py`, `library_view.py`, `source.py` |
| Metadata/review | `preparation.py`, `ui/metadata.py`, `ui/videos.py`, `ui/preview.py` |
| Google | `auth.py` (OAuth), `credentials.py` (Keychain), `youtube.py`, `accounts.py`, `ui/accounts.py` |
| Upload | `uploads.py`, `upload_http.py` (resumable protocol), `upload_store.py`, `ui/uploads.py` |
| Scheduling | `scheduling.py`, `execution.py` (+`_schema`), `ui/scheduling.py`, `ui/execution.py` |
| Background | `background.py` (LaunchAgent), `background_state.py`, `ui/background.py` |
| Folders/recurring/auto | `watching.py`, `recurring.py`, `cadence.py`, `automation.py` (+ `*_schema.py`), matching `ui/*.py` |
| TikTok (Buffer) | `buffer.py` (PKCE OAuth, rotating refresh under a file lock, GraphQL), `hosting.py` (Cloudinary public copy), `tiktok.py` (posts, duplicates, status, automation), `tiktok_schema.py`, `ui/tiktok.py`; relay page `docs/buffer-oauth-relay.html` |
| Dashboard | `activity.py` (pure cross-platform timeline), `ui/dashboard.py`; read-only view counts via `AccountService.statistics` |
| Auto upload | `ui/autoupload.py` (one editor → YouTube profile + TikTok profile, each with its own consent) |
| Shared UI | `ui/window.py`, `ui/theme.py`, `ui/components.py` (cards, pills, `Section`, `CardGrid`, `flow`), `ui/icons.py` (SVG icons + platform logos), `ui/formatting.py` (dates/times) |

Tests: `tests/test_*.py` (unittest, discovered), `tests/native_*.py` (manual native harnesses),
`tests/*support.py`/`*_fixture.py` (shared fixtures), `tests/fixtures/library-v1…v7.sqlite3`
(migration fixtures — keep).

## Database

SQLite, `PRAGMA user_version`; current schema **12** (`library.SCHEMA_VERSION`; v12 = TikTok tables). Opening a
library migrates it forward inside one `BEGIN IMMEDIATE` transaction; newer unknown versions are
refused; failures never reset data. Add migrations as a new `*_schema.py`-style additive step and a
fixture/test. The user's live library is at schema 12 (the user opened it 2026-10-20 13:17; pre-launch backups in
`artifacts/stage6-live/backups/`). Its Buffer app client is saved; TikTok is not yet connected. **Any migration of the live library
needs the user's explicit approval and a fresh backup.**

## Protected real data — never delete, move, overwrite or migrate without approval

- `artifacts/stage6-live/` — the live StonkYa workspace (launcher `--data-dir`), backups, logs.
- `artifacts/stage6-preflight/`, `stage7-live-preflight/`, `stage9-live/` — genuine source videos
  of real uploads `bfNDN1Gq540`, `OxgK8zkDmkI`, `vmbsEJ4E9Rc`. The live DB stores their **absolute
  paths**, so these folders cannot be renamed or moved.
- `artifacts/stage7-preflight/`, `stage9-final/`, `stage10-final/`, `stage17-evidence/`,
  `stage17-human-picker/`, `stage17-prepared/` — live preservation evidence / pending live test.
- macOS Keychain entries (`ShortsPublisher.YouTube`, `.UploadSessions`, `.TikTok`), the real channel
  `UCoj4BJDrrcuE3gTvpi-7QFA`, and its YouTube videos.
- `/Users/winston/CodexProjects/ShortsStudio` — read-only design reference.

`artifacts/` is gitignored and mixed-trust. Everything else there is controlled test evidence;
see `docs/stage-18-verification.md` for the classified inventory and the approved cleanup record.

## Safety rules (non-negotiable)

- No real YouTube upload, update, privacy change or deletion without explicit user authorization.
- No `publishAt`: the app itself starts a Public upload at the approved time.
- Public publishing needs exact approval (video + metadata revision + channel + time) or a standing
  automatic profile bound to folder/channel/rules/cadence. Folder-watching consent is not publishing
  consent. Don't guess Made for Kids or synthetic-content declarations.
- Duplicate prevention: SHA-256 content registry, channel-aware history, queue reservations,
  execution claims. An uncertain previous upload needs reconciliation, never a blind retry.
- Never install, enable or modify the production LaunchAgent (`com.shortspublisher.publisher`)
  unprompted. Background-service consent is separate from global publishing consent.
- Never modify, move, transcode or delete original source videos.
- Don't read real credentials. Tests use synthetic Keychain identifiers and controlled providers.
- Don't kill running ShortsPublisher processes or interfere with an active upload.
- TikTok: never make real Buffer/Cloudinary requests or TikTok posts without explicit authorization.
  Never reuse a Buffer refresh token after an uncertain refresh (it revokes the grant). Never
  resend an uncertain `createPost`; reconcile by the unique hosted URL. Duplicate key = source
  SHA-256 + Buffer channel. Hosted copies are public by link; never delete before Buffer reports
  `sent` (+24 h). Only offer TikTok controls Buffer's API supports (AI disclosure; not privacy).

## Conventions

- The interface is English. `use_english_interface()` (in `app.build`) replaces Qt's
  Region-derived default locale with an English one; `time_pattern()` keeps the Region's
  12/24-hour clock. Use `ui/formatting.timestamp()` for every displayed time. User data (titles,
  filenames, the `雪` test fixtures) is never translated.
- Action buttons: `ui/components.tidy_buttons` turns stacked buttons into wrapping rows (window and
  every dialog); don't hand-stretch buttons full width. Use `icon_button`, `flow`/`row`, `card` and
  `pill`; icons come from `ui/icons.py` (never emoji). Nothing collapses: `Section` is always open
  (the user dislikes reveal/dropdown sections); developer diagnostics go in tooltips or Settings.
- Wording is platform-neutral ("upload", not "publish to YouTube"); settings only one platform
  supports are labelled "Only used on YouTube/TikTok". Video details are shared: the TikTok caption
  is `tiktok.caption_from(title, description, tags)`.
- Lists show one row per video across platforms (`activity.grouped`), with a logo per platform.
  Setup steps use `components.Checklist`; `CardGrid` evens card heights per row.
- Video editor: six internal pages (File, Details, Review, Publish, Schedule, TikTok — tests use
  these indices) shown as three steps (Details · Review · Upload) with a hidden tab bar.
- Preview shows the first frame on selection (muted play → pause). Never call
  `QMediaPlayer.stop()` right after `setSource()`: it can deadlock Qt's FFmpeg backend; clear the
  source instead. Automation status wording comes from
  `ui/automation.headline()` and must never say Active while posting is blocked.
- Workers own I/O; GUI code receives queued signals. SQLite connections never cross threads.
- Keep changes small and match existing style (ruff line length 100, strict mypy on `src`).
- Run focused tests while working; run the full suite, lint, mypy and relevant native harnesses
  before declaring a stage complete. Never delete tests to make things pass.

## Where to look

`docs/current-status.md` (capabilities, limits, priorities) · `docs/architecture.md` (design
decisions by stage) · `docs/development.md` (commands, native checks) · `docs/roadmap.md` ·
`docs/stage-N-verification.md` (history; read only when relevant) · `README.md` (user guide).
