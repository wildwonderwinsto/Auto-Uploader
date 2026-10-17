# Stage 2 verification — 2026-10-15

Stage 2 implementation is complete. Verification passed within the limits below; this is
not a claim that every operating system, media format, or possible damaged file was tested.
No Stage 3 features were implemented.

## Implemented scope

| Requirement | Implementation / evidence |
| --- | --- |
| Single local import | Native `QFileDialog`, MP4/MOV/WebM filters plus All files; cancel is a no-op |
| Real inspection | FFprobe 9.0.2; actual frame counting, metadata parsing, timeouts, cancellation, safe argument list |
| Metadata | Name/path, bytes, duration, encoded dimensions, display aspect, rotation, FPS, codecs/audio, UTC import/inspection dates |
| Invalid media | Rejects empty, unsupported, damaged, audio-only, incomplete/non-finite metadata and decoder errors; no partial row |
| Technical readiness | Pure dated YouTube rules; vertical/square and ≤180 seconds; silent/unknown metadata warnings; clear limits |
| Persistence | SQLite schema version 1, transactional initialization/writes, future-version refusal, canonical-path uniqueness |
| File safety | References only; no source mutation or deletion; hash verified before/after native workflow |
| Missing/changed sources | Checked on selection/refresh; preserved record and metadata, clearly unverified |
| Refresh / remove | Real reinspection, failed-refresh retention and recovery; removal confirmation affects only the row |
| Responsive UI | One worker, queued UI slots, indeterminate activity, guarded conflicting controls, cancellation and safe close/Quit |
| Useful library UI | Empty card, selected list item, inline details, warnings/errors, refresh/remove; scrollable small layout |
| Stage 1 preserved | All seven routes, selection state, keyboard actions, resizing, credential-free startup and clean close tested |
| Read-only reference | ShortsStudio source/reference hashes unchanged; clean git status |

YouTube rules were checked against [official YouTube Shorts guidance](https://support.google.com/youtube/answer/15424877?hl=en)
on October 15, 2026. The inspector follows [FFprobe's official documentation](https://ffmpeg.org/ffprobe.html).
No recommended codec, resolution or frame rate is treated as a mandatory Shorts requirement.

## Exact checks and results

Environment: macOS Apple Silicon, Python 3.12.14, PySide6 6.12.0, FFprobe/FFmpeg 9.0.2.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Results: **50 tests passed in 8.173 seconds, no skips**; Ruff passed; 20 Python files already
formatted; mypy found no issues in 11 source files. Expected error tracebacks from deliberate
corrupt-file, missing-tool, database-failure and logging-fallback tests are not test failures.

Coverage includes real playable MP4/MOV/WebM files; correct H.264/AAC metadata, 48 decoded
frames for a 2-second 24 FPS video; silent media; portrait/landscape/square and duration boundary
rules; rotation/pixel aspect; invalid JSON/metadata; unavailable executables; timeouts and process
reaping; corrupt/truncated/empty/unreadable/missing/changing files; Unicode/long names; symlink
aliases; concurrent duplicate insertion; future/corrupt/locked databases; persistence; unchanged
original hashes; safe removal; refresh failure/recovery; UI navigation, multiple selections,
blocked conflicting actions, event-loop responsiveness, and close/Quit during work.

Entry-point smoke checks:

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m shortspublisher --smoke-test --data-dir /tmp/shortspublisher-stage2-entry-smoke
.venv/bin/python -m shortspublisher --smoke-test --data-dir /tmp/shortspublisher-stage2-native-entry
```

Both logged startup and `Desktop closed with code 0`. The second ran natively with Cocoa.

Native workflow, final run (no `QT_QPA_PLATFORM` override, so Cocoa on this Mac):

```sh
.venv/bin/python tests/native_library_smoke.py import --root artifacts/stage2-native
.venv/bin/python tests/native_library_smoke.py reopen --root artifacts/stage2-native
```

Both phases passed with exit code 0 in **separate OS processes**. The first imported a generated
portrait MP4, showed real duration/dimensions/codecs and passing readiness, imported a silent
landscape WebM and showed warnings, removed that extra library entry, then exercised corrupt
import failure without losing the good entry. The second reopened the saved SQLite library,
selected the MP4, verified displayed metadata, refreshed inspection, and removed its library entry.
The resulting library was empty; the original MP4 still existed with its original SHA-256 hash.

Native captures were generated at **1180×780** and **760×560**, with top and detail views where
applicable. Visually inspected empty, inspecting, populated, error, and warning states. No
horizontal overflow was detected; long paths wrap and details remain reachable by scrolling.
The final empty card and selected navigation focus were reviewed after polish. Evidence and
reusable generated test videos remain in ignored `artifacts/stage2-native/`; an intentionally
invalid disposable fixture was removed after verification. Unit-test temporary libraries and
media are automatically cleaned up.

Default production storage was confirmed via `QStandardPaths`:

`/Users/winston/Library/Application Support/ShortsPublisher/ShortsPublisher`

The production library was not populated with test data. Native harnesses use an explicit test
root; ordinary launches use the OS application-data location outside the source tree.

## Reference safety and changes

SHA-256 comparison of 250 ShortsStudio Python/source-reference files before and after showed
**zero changes**. `git -C ../ShortsStudio status --porcelain` returned no changes. No commands
modified that project. All media used for tests were generated specifically for verification;
no existing user video was opened for writing or deleted.

New modules: `media.py`, `readiness.py`, `library.py`, `ui/videos.py`.
Updated: `app.py`, `ui/window.py`, `ui/theme.py`, README and architecture documentation.
New tests/helpers: `test_library.py`, `test_videos_ui.py`, `support.py`, `native_library_smoke.py`.
Existing shell tests and capture helper now isolate library data and wait for worker shutdown.
No new Python dependencies; `pyproject.toml` and `uv.lock` did not require changes. FFprobe is
an external runtime prerequisite, already installed on this machine; no automatic installation
or dependency on ShortsStudio was introduced.

## Limits and remaining manual checks

- The native harness supplies the file-picker path and confirmation response automatically.
  **Manual interaction with the macOS file chooser itself remains unverified.** All subsequent
  workers, media inspection, SQLite operations, widget updates and screenshots are real.
- Windows/Linux, installer packaging, other displays/scaling settings, very large media and
  remote/network-mounted source paths were not validated. Tests covered representative containers,
  not every format or codec FFprobe supports.
- FFprobe frame counting with no reported errors is a technical check, not proof of perfect
  playback or detection of every possible corruption. Large/slow files may hit the 120-second
  timeout and must not be described as passing.
- Saved inspection status is historical. Source availability/size/mtime is checked when selected,
  not by a background watcher. Content hashing/deduplication across different paths is outside scope.
- No real YouTube acceptance, copyright, account, upload, distribution or publishing behavior
  was tested or claimed. There are no network API operations in Stage 2.

To perform the remaining manual picker check, launch `.venv/bin/python -m shortspublisher`,
open Videos, click Import Video, select a video, then try cancelling a second picker. Close and
reopen to inspect persistence, and remove the library entry while confirming the original stays.

Suggested Stage 3: persistent editable publishing metadata and a review workflow. Stop here
until the next explicit instruction.
