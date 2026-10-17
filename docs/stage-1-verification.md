# Stage 1 verification — 2026-10-15

Environment: macOS Apple Silicon, Python 3.12.14, PySide6 6.12.0.

Executed checks:

- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v`
  — 8 tests passed. Covers all seven routes, exclusive selection, keyboard activation,
  dashboard action, invalid route rejection, rendered resizing at 760×560, 1180×780,
  and 1600×1000, closing, credential-free subprocess startup, logging reconfiguration,
  and fallback when the logging path is unusable. The fallback traceback is intentional.
- `.venv/bin/ruff check .` — passed.
- `.venv/bin/ruff format --check .` — 11 files already formatted.
- `.venv/bin/mypy` — no issues in 7 source files.
- `.venv/bin/python tests/capture_ui.py` — rendered 14 offscreen screenshots.
- `QT_QPA_PLATFORM=cocoa .venv/bin/python tests/capture_ui.py` — native macOS rendering,
  all seven pages at 760×560 and 1180×780; completed with exit code 0.
  Screenshots in `artifacts/` were overwritten with native Qt captures. Inspected the
  dashboard, minimum-size queue and native page contact sheet; no clipping observed.
- `.venv/bin/python -m shortspublisher --smoke-test --log-dir artifacts/native-smoke-logs`
  — native entry point launched and closed cleanly with code 0.
- A separate interactive native window was launched with
  `.venv/bin/python -m shortspublisher --log-dir artifacts/native-logs`.
- Compared SHA-256 hashes of 250 ShortsStudio source/reference files before and after:
  unchanged. Its git working tree was clean both times. No write commands targeted it.

Limitations: the desktop automation tool timed out attaching to the Python app, so native
rendering was captured through Qt's test harness instead. Windows/Linux, packaged installs,
high-DPI configurations beyond this machine, and future publishing behavior are unverified.
The font-alias message under offscreen Qt is informational, not a rendering failure.

New files: pyproject.toml, uv.lock, .gitignore, README.md, docs/architecture.md,
this report, seven Python source files under src/shortspublisher, tests/test_shell.py,
and tests/capture_ui.py. The local .venv and ignored artifacts contain installation
and verification output only. No pre-existing Auto Uploader files were modified.

Stage 1 ends here. No OAuth, media processing, persistence database, scheduling,
network calls, uploads, analytics data, or future platform adapters are implemented.
