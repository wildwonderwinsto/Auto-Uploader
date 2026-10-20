# Development

## Environment

- macOS, Python 3.12 virtual environment at `.venv` (managed with uv; `uv.lock` pins versions).
  The package is installed editable, so `src/` changes take effect without reinstalling.
- PySide6 6.12 (Qt 6.12). FFmpeg/FFprobe on `PATH` (Homebrew) or `--ffprobe PATH`.
- Dev tools: Ruff (lint + format, line length 100) and mypy (strict on `src/`).

Do not delete or rebuild `.venv` to save space; the LaunchAgent stores absolute interpreter paths.

## Run

```sh
.venv/bin/python -m shortspublisher --data-dir /tmp/sp-dev   # isolated library + logs
.venv/bin/python -m shortspublisher --smoke-test --data-dir /tmp/sp-dev
```

Without `--data-dir` the app uses Qt's default location
`~/Library/Application Support/ShortsPublisher/ShortsPublisher/`. The user's real workspace is
opened only by `Start ShortsPublisher.command` (`artifacts/stage6-live`); do not run it yourself.

## Automated checks

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Focused run (tests import siblings such as `support`, so run from `tests/`):

```sh
cd tests && QT_QPA_PLATFORM=offscreen PYTHONPATH=../src ../.venv/bin/python -m unittest test_watching -v
```

Tests use temporary libraries, generated FFmpeg fixtures, local loopback HTTP servers and fake
Google providers. Unicode fixtures (for example `雪`) are intentional. `tests/fixtures/library-v*.sqlite3`
are migration fixtures for older schema versions.

## Native macOS checks

Run with `QT_QPA_PLATFORM=cocoa .venv/bin/python <command>`. Use a new disposable `--root` each
time (a scratch directory, not `artifacts/` unless you intend to keep evidence). Run every phase
of a pair: later phases verify persistence and remove synthetic Keychain entries / test services.

| Area | Commands |
| --- | --- |
| Library browse/preview | `tests/native_library_browse.py prepare\|reopen --root R` |
| Import/inspect | `tests/native_library_smoke.py import\|reopen --root R` |
| Metadata/review | `tests/native_metadata_smoke.py prepare\|reopen --root R` |
| Accounts | `tests/native_accounts_smoke.py prepare\|reopen --root R` |
| Private upload | `tests/native_upload_smoke.py prepare\|reopen\|history --root R` |
| Public upload | `tests/native_public_smoke.py prepare\|reopen\|history --root R` |
| Individual scheduling | `tests/native_schedule_smoke.py prepare\|reopen --root R` |
| Automatic scheduled (foreground) | `tests/native_execution_smoke.py prepare\|reopen --root R` |
| Background LaunchAgent | `tests/native_background_smoke.py prepare --root R`, then `tests/native_background_continue.py --root R` |
| Service controls | `tests/native_service_controls.py --root R` |
| Watched folders | `tests/native_watching.py prepare\|reopen --root R` |
| Recurring queues | `tests/native_recurring.py prepare\|reopen --root R`; background: `background-prepare`, then `tests/native_recurring_continue.py --root R` |
| Automatic folder posting | `tests/native_automation.py prepare\|reopen --root R`; background: `tests/native_automation_background.py prepare\|continue --root R` |
| Page/dialog audit | `tests/native_ui_audit.py --root R` (screenshots of every route at three sizes) |
| TikTok via Buffer | `tests/native_tiktok.py --root R` (controlled Buffer/Cloudinary; connect, hosting, publish, automation, queue) |
| Entry point | `-m shortspublisher --smoke-test --data-dir R` |

`native_automation.py prepare --native-picker [--picker-cancel-first]` needs a person to operate
the real macOS folder chooser. Background harnesses install real per-user LaunchAgents under
isolated test labels and remove them; confirm afterwards that `launchctl list | grep -i shorts`
is empty and `~/Library/LaunchAgents` has no ShortsPublisher plist.

To check locale handling, Cocoa honours per-process defaults overrides, e.g.
`... tests/native_ui_audit.py --root R -AppleLocale zh_CN` behaves like a Chinese macOS Region
(argparse-based harnesses need a wrapper that strips the `-Apple*` pair from `sys.argv`).

## Migrations

`library.Library.initialize` reads `PRAGMA user_version`, refuses newer versions, and applies each
step in one `BEGIN IMMEDIATE` transaction (v1–v6 inline, then `execution_schema` v7,
`background_state` v8, `watch_schema` v9, `recurring_schema` v10, `automation_schema` v11,
`tiktok_schema` v12). Never use `executescript()` in a migration: it commits the open transaction.
New migrations must be additive, must not create publishing authority, and need a fixture-based
upgrade test. Bump `SCHEMA_VERSION`.

## Working with an AI assistant

1. Read `CLAUDE.md`, then `docs/current-status.md`.
2. Search (`grep`) for the modules relevant to the task; read those, not the whole tree.
3. Read a stage report only when its decision is relevant; `docs/architecture.md` has the summary.
4. Preserve the architectural and safety decisions listed in `CLAUDE.md`.
5. Run focused tests while working; full suite, lint, mypy and relevant native checks at the end.
