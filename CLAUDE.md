# CLAUDE.md — ShortsPublisher

Read this first. It is a map, not the full history. Then read `docs/current-status.md` and, only
if the task needs it, the relevant section of `docs/architecture.md`. Stage reports are references.

## What it is

A local-first macOS desktop app (PySide6) that imports short vertical videos, prepares YouTube
metadata, and publishes them — manually, on an approved schedule, from recurring queues, or from
watched folders under standing approval. **YouTube is the only implemented platform.** TikTok,
Instagram, Facebook and Snapchat are future work. No packaging/distribution exists yet.

Stages 1–18 are done (Stage 17's optional live folder-to-YouTube test is still pending the user's
personal authorization). Never start a new stage without explicit instruction.

## Commands

```sh
.venv/bin/python -m shortspublisher --data-dir /tmp/sp-test        # isolated run
./"Start ShortsPublisher.command"                                   # USER's live workspace — don't run
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests   # 727 tests, ~4 min
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
| Shared UI | `ui/window.py`, `ui/theme.py`, `ui/components.py`, `ui/formatting.py` (dates/times) |

Tests: `tests/test_*.py` (unittest, discovered), `tests/native_*.py` (manual native harnesses),
`tests/*support.py`/`*_fixture.py` (shared fixtures), `tests/fixtures/library-v1…v7.sqlite3`
(migration fixtures — keep).

## Database

SQLite, `PRAGMA user_version`; current schema **11** (`library.SCHEMA_VERSION`). Opening a
library migrates it forward inside one `BEGIN IMMEDIATE` transaction; newer unknown versions are
refused; failures never reset data. Add migrations as a new `*_schema.py`-style additive step and a
fixture/test. The user's live library reached schema 11 when the user opened it on 2026-10-17 17:41; the
schema-9 copy is `artifacts/stage6-live/backups/library-before-launch-20261017T224140686820Z.sqlite3`.
**Any future migration of the live library needs the user's explicit approval and a fresh backup.**

## Protected real data — never delete, move, overwrite or migrate without approval

- `artifacts/stage6-live/` — the live StonkYa workspace (launcher `--data-dir`), backups, logs.
- `artifacts/stage6-preflight/`, `stage7-live-preflight/`, `stage9-live/` — genuine source videos
  of real uploads `bfNDN1Gq540`, `OxgK8zkDmkI`, `vmbsEJ4E9Rc`. The live DB stores their **absolute
  paths**, so these folders cannot be renamed or moved.
- `artifacts/stage7-preflight/`, `stage9-final/`, `stage10-final/`, `stage17-evidence/`,
  `stage17-human-picker/`, `stage17-prepared/` — live preservation evidence / pending live test.
- macOS Keychain entries, the real channel `UCoj4BJDrrcuE3gTvpi-7QFA`, and its YouTube videos.
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

## Conventions

- The interface is English. `use_english_interface()` (in `app.build`) replaces Qt's
  Region-derived default locale with an English one; `time_pattern()` keeps the Region's
  12/24-hour clock. Use `ui/formatting.timestamp()` for every displayed time. User data (titles,
  filenames, the `雪` test fixtures) is never translated.
- Action buttons: `ui/components.tidy_buttons` turns stacked buttons into wrapping rows (window and
  every dialog); don't hand-stretch buttons full width. Automation status wording comes from
  `ui/automation.headline()` and must never say Active while posting is blocked.
- Workers own I/O; GUI code receives queued signals. SQLite connections never cross threads.
- Keep changes small and match existing style (ruff line length 100, strict mypy on `src`).
- Run focused tests while working; run the full suite, lint, mypy and relevant native harnesses
  before declaring a stage complete. Never delete tests to make things pass.

## Where to look

`docs/current-status.md` (capabilities, limits, priorities) · `docs/architecture.md` (design
decisions by stage) · `docs/development.md` (commands, native checks) · `docs/roadmap.md` ·
`docs/stage-N-verification.md` (history; read only when relevant) · `README.md` (user guide).
