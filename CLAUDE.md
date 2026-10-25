# CLAUDE.md — ShortsScheduler (package `shortspublisher`)

Read this first. It is a map, not the full history. Then read `docs/current-status.md` and, only
if the task needs it, the relevant section of `docs/architecture.md`. Stage reports are references.

## What it is

A local-first macOS desktop app (PySide6) that imports short vertical videos, prepares YouTube
metadata, and publishes them — manually, on an approved schedule, from recurring queues, or from
watched folders under standing approval. **YouTube** is implemented and live-verified. **TikTok**
is implemented through Buffer's API (Stage 19) and live-verified once: an automatic Public post to
@owner_tiktok (TikTok video <tiktok-video-id>). **Instagram Reels and Facebook Page Reels** use the
same Buffer path (Stage 20), verified with controlled services only; their first live posts need
the user's Buffer setup and authorization. Further real posts still need the user's authorization.
Snapchat is future work. It ships as source on GitHub: `./install.sh` sets up `.venv`, checks
FFmpeg and builds `ShortsScheduler.app` (`scripts/make_app.py`, bound to the checkout; ad-hoc
signed, not notarized). Its executable must stay the universal native stub `assets/launcher`
(source `scripts/launcher.c`), which execs `Resources/launch.sh` — a script executable makes
Apple silicon Macs prompt to install Rosetta. On 2026-10-22 the owner had every earlier workspace, backup and
Keychain sign-in deleted, so the app now starts as for a brand-new user.

Stages 1–20 are done (Stage 17's optional live folder-to-YouTube test is still pending the user's
personal authorization). After Stage 19 the UI was redesigned as an all-platform uploader named
**ShortsScheduler** (display name only: the internal app/organization name, data folder, Keychain
services, LaunchAgent and package stay `ShortsPublisher`/`shortspublisher` — never rename them).
Never start a new stage without explicit instruction.

## Commands

```sh
.venv/bin/python -m shortspublisher --data-dir /tmp/sp-test        # isolated run
./install.sh                                                        # env + FFmpeg + ShortsScheduler.app
open ShortsScheduler.app        # the USER's real data (~/Library/Application Support/ShortsPublisher/ShortsPublisher) — don't run
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests   # 937 tests, ~12 min
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
| Background | `background.py` (LaunchAgent), `background_state.py`, `ui/background.py`, `awake.py` (`caffeinate` + an NSProcessInfo App Nap exemption while an upload is planned) |
| Folders/recurring/auto | `watching.py`, `recurring.py`, `cadence.py`, `automation.py` (+ `*_schema.py`), matching `ui/*.py`; `queues.py` + `queues_schema.py` (v17: a queue's name, times, platforms, YouTube plan and waiting Buffer videos) |
| Buffer (TikTok, Instagram, Facebook) | `buffer.py` (PKCE OAuth, rotating refresh under a file lock, GraphQL), `hosting.py` (Cloudinary public copy), `social.py` (`Platform` specs + `BufferPostService`: posts, duplicates, status, automation, shared sign-in), `social_schema.py` (v13), `backups_schema.py` (v15) + `fallback_schema.py` (v16): backup Buffer/Cloudinary accounts, `ui/social.py` (`SocialController` per platform); `tiktok.py`, `tiktok_schema.py`, `ui/tiktok.py` keep Stage 19 names; relay page `docs/buffer-oauth-relay.html` |
| Dashboard | `activity.py` (pure cross-platform timeline), `ui/dashboard.py`; read-only view counts via `AccountService.statistics` |
| Defaults/usage | `defaults.py` + `ui/defaults.py` (Reuse Details, Upload Defaults in `upload-defaults.json` beside the library: no schema change; never the title), `usage.py` + `ui/usage.py` (Settings → Limits & usage: local counts, plus Cloudinary's read-only usage asked automatically when Settings opens and every 30 min while open; no button) |
| Auto upload | `ui/autoupload.py` (one editor → a YouTube profile and a profile per Buffer platform, each with its own consent, approved in turn; also edits an existing one), `ui/autocards.py` (one card per auto upload: profiles sharing folder + name), `ui/queuecards.py` (one card per video queue) |
| Shared UI | `ui/window.py`, `ui/theme.py`, `ui/components.py` (cards, pills, `Section`, `CardGrid`, `flow`), `ui/icons.py` (SVG icons + platform logos), `ui/formatting.py` (dates/times) |

Tests: `tests/test_*.py` (unittest, discovered), `tests/native_*.py` (manual native harnesses),
`tests/*support.py`/`*_fixture.py` (shared fixtures), `tests/fixtures/library-v1…v7.sqlite3`
(migration fixtures — keep).

## Database

SQLite, `PRAGMA user_version`; current schema **17** (`library.SCHEMA_VERSION`; v12 = TikTok tables,
v13 = Instagram/Facebook tables, v14 = daily limits up to 100, v15 = backup Buffer and Cloudinary
accounts, v16 = each backup Buffer account's own sign-in, v17 = cross-platform video queues;
approved by the owner 2026-10-24, applied by the app after its launch backup). Opening a
library migrates it forward inside one `BEGIN IMMEDIATE` transaction; newer unknown versions are
refused; failures never reset data. Add migrations as a new `*_schema.py`-style additive step and a
fixture/test. The real library lives in the default data folder; `ShortsScheduler.app` runs
`--backup` (a checked SQLite copy into `backups/`, newest 10 kept) before every launch. **Any
migration of the user's real library needs the user's explicit approval and a fresh backup.**

## Protected real data — never delete, move, overwrite or migrate without approval

- The default data folder `~/Library/Application Support/ShortsPublisher/ShortsPublisher`
  (library, backups, logs) — the user's real workspace once they use the app.
- macOS Keychain entries (`ShortsPublisher.YouTube`, `.UploadSessions`, `.TikTok` — the last holds
  the Buffer sign-in and Cloudinary secret for every Buffer platform), the user's real channels
  and their posted videos.
- `artifacts/` (gitignored, local only): earlier test evidence and the original source videos of
  past real uploads (`stage6-preflight/`, `stage7-live-preflight/`, `stage9-live/`) — never
  modify or delete those videos.
- `~/CodexProjects/ShortsStudio` — read-only design reference.

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
  The dispatcher OS lock decides who dispatches: a live worker holds it for its whole life, so
  the open app dispatches whenever it gets the lock, even while the service is switched on (a
  stopped service, e.g. after an update migrated the library, never leaves uploads to be
  missed); the sidebar then says the service stopped. Restarting the service is the user's call.
- Never modify, move, transcode or delete original source videos.
- Don't read real credentials. Tests use synthetic Keychain identifiers and controlled providers.
- Don't kill running ShortsPublisher processes or interfere with an active upload.
- Buffer platforms (TikTok, Instagram, Facebook): never make real Buffer/Cloudinary requests or
  posts without explicit authorization.
  Never reuse a Buffer refresh token after an uncertain refresh (it revokes the grant). Never
  resend an uncertain `createPost`; reconcile by the unique hosted URL. Duplicate key = source
  SHA-256 + Buffer channel. Hosted copies are public by link; never delete before Buffer reports
  `sent` (+24 h). Only offer controls Buffer's API supports (AI label on TikTok/Instagram; never
  privacy). Platforms share one Buffer sign-in: delete a Keychain key only when no connection uses
  it, and mark "Reconnect required" on every connection sharing it.
- Backup accounts (Settings): the saved app client / `media_hosting` row stay the **main** Buffer and
  Cloudinary accounts; backups live in `buffer_accounts` / `media_hosts`. Each post records its
  `host_key` (Cloudinary account of its copy) and `buffer_key`/`buffer_org` (sign-in it was sent
  with). Both fail over automatically, only on a definite limit refusal (nothing created):
  Cloudinary on `HostingError` kind `quota`; Buffer on a 429 or a createPost refusal matching
  `social.over_plan`, to the next backup account with its own standing sign-in
  (`buffer_accounts.credential_key`) that has the *same* account (same name) — never a different
  one. Duplicates are also matched by account name across Buffer accounts. A backup sign-in that
  sees an organization another listed account has is refused (same Buffer account twice).

## Conventions

- The interface is English. `use_english_interface()` (in `app.build`) replaces Qt's
  Region-derived default locale with an English one; `time_pattern()` keeps the Region's
  12/24-hour clock. Use `ui/formatting.timestamp()` for every displayed time. User data (titles,
  filenames, the `雪` test fixtures) is never translated.
- Daily limits: the user wants none of the app's own: no setting or field. Only the platforms'
  ceilings apply, invisibly: YouTube 100 (`execution.DAILY_MAX`; schema 14 sets the global value
  to it), Buffer platforms `Platform.daily_max` (TikTok 25, Instagram 50, Facebook 35). Settings
  sections show the logos of the platforms that need them (`Section(..., platforms)`).
- Action buttons: `ui/components.tidy_buttons` turns stacked buttons into wrapping rows (window and
  every dialog); don't hand-stretch buttons full width. Use `icon_button`, `flow`/`row`, `card` and
  `pill`; icons come from `ui/icons.py` (never emoji). Nothing collapses: `Section` is always open
  (the user dislikes reveal/dropdown sections); developer diagnostics go in tooltips or Settings.
- Auto Upload page: two sections, one button each — **Individual videos** first
  (`recurring.add` "Add Videos…" → `AddVideos`: "One video" = `recurring.single` → `PickVideo` →
  `VideosPage.open_upload`; "Several" = `recurring.new` → `RecurringEditor`, whose videos are tick
  boxes in upload order: `checked()`, not selection; Move Up/Down) and **Watched folders**
  (`automation.new` "Watch a Folder…" → the Auto Upload editor; `WatchPanel` is just "Folder
  activity"). Each auto upload and each queue is one card (`views` / `act(action, key)`): Edit…,
  Pause/Resume Auto Upload (or Queue), Delete… — they act on every platform, never on posted
  videos. Editing applies to everything not uploaded yet; Buffer gets one video at a time per auto
  upload or queue. The sidebar switch
  (`ExecutionController.rail`) and page banners (`ExecutionController.banner()`) warn whenever
  something is held back by the global pause. Timer-driven refreshes ("load", "scan", quiet
  Buffer polls) must never disable buttons or rebuild unchanged lists (flicker); clicks made
  during one are queued.
- Schedule page: one "YouTube uploads" card (scheduled jobs + uploads, like each Buffer card;
  buttons appear only when the chosen row can use them; diagnostics in `SchedulePage.diagnostics`
  / the row tooltip). The Calendar shows no YouTube card (its day agenda lists every platform).
  A YouTube schedule from the Upload step is `ScheduleController.schedule`: one
  `ScheduleConfirmation(activate=True)` approves the plan *and* its automatic upload (the consent
  is shown there; Auto uploading is only turned on if its box is ticked). A YouTube plan for a
  video blocks a second YouTube upload of it in the Upload step.
- Dashboard platform cards show the same four columns everywhere: Uploaded · Scheduled · Views
  (of videos uploaded from the app) · Followers (Subscribers on YouTube; "—" for Buffer platforms).
- Wording is platform-neutral ("upload", not "publish to YouTube"); settings only one platform
  supports are labelled "Only used on YouTube/TikTok". Video details are shared: the TikTok caption
  is `tiktok.caption_from(title, description, tags)`.
- Lists show one row per video across platforms (`activity.grouped`), with a logo per platform.
  Setup steps use `components.Checklist`; `CardGrid` evens card heights per row.
- Video editor: nine internal pages (File, Details, Review, Publish, Schedule, TikTok, Instagram,
  Facebook, Upload — tests use these indices) shown as three steps (Details · Review · Upload) with
  a hidden tab bar. The Upload step is page 8 (`ui/upload_all.py`): every ready platform ticked,
  one Now/Schedule, one button; it only starts each platform's own action (Buffer posts confirmed
  together, YouTube still through its own confirmation / schedule approval) and requires the
  video to be marked ready for every platform. Pages 3–7 are each platform's "Options" page.
- Uniform UI: platforms are chosen with `components.platform_choice`/`set_choice` everywhere
  (Upload step, queues, auto uploads); multi-step windows use `step_page`/`step_pills` with Cancel
  left and Back/Next/primary right; empty platform cards are hidden rather than repeated.
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
