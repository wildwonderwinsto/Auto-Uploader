# Stage 6 verification — completed 2026-10-17

**Stage 6 is complete.** The user interactively authorized Google, confirmed the intended channel,
granted upload permission and approved one real Private upload. Independent live API verification
confirmed its ID, destination, Private privacy and successful processing. After the user restarted
the application, the same connection and upload result persisted with exactly one recorded attempt.
No Stage 7, scheduling, Public uploading or automatic posting was implemented.

## Real integration results

| Check | Observed result |
| --- | --- |
| Channel | **StonkYa**, `UCoj4BJDrrcuE3gTvpi-7QFA` |
| Interactive connection | User completed browser authorization and channel confirmation |
| Permissions | `youtube.readonly` and `youtube.upload`, recorded in the scoped Keychain credential |
| Renewable authorization | Refresh credential present; its value was never displayed/exported |
| Upload attempt | `90743697aeec433f91580769a2b89a7d` |
| Attempt created | `2026-10-17T14:28:10+00:00` |
| Transfer completion | `2026-10-17T14:28:23+00:00` |
| Acknowledged bytes | **72,353 / 72,353** |
| Actual returned video ID | [`bfNDN1Gq540`](https://www.youtube.com/watch?v=bfNDN1Gq540) |
| Independent videos.list verification | `2026-10-17T14:29:12+00:00` |
| Actual privacy | **private** |
| Actual processing status | **succeeded** |
| Account verification after user restart | `2026-10-17T14:30:04+00:00`, same channel |
| Attempts after restart | **1**, same attempt and video ID; no new attempt created |
| Original source integrity | SHA-256 still matches approved snapshot |

The insertion response initially reported upload status `uploaded`. A separate process subsequently
called the production **verify** operation, rechecked the authorized channel and requested the saved
video through **videos.list**. It returned the expected channel, `private` privacy and `succeeded`
processing. This was a real Google response, not a controlled fixture. Verification did not create
a new session, transfer media or modify remote content.

The user then closed and reopened the app with the same live-test data directory. Read-only SQLite
inspection confirmed that startup had refreshed the channel verification timestamp, retained the
same completed result and left the attempt count at one. This establishes persistence and no new
application upload attempt during the observed restart. It is not a universal guarantee against
all possible remote duplicates outside the application.

The real upload used the existing official resumable implementation and securely stored its session
before transferring media. Persisted acknowledgement/completion and subsequent independent retrieval
establish end-to-end acceptance. Raw HTTP headers, token bodies and session URLs were deliberately
not captured. A second upload was not performed to repeat the test, and the successful live transfer
was not intentionally interrupted.

## Source and user-approved metadata

Source: `artifacts/stage6-preflight/private-integration-test.mp4`.

- Original locally rendered slate: PRIVATE INTEGRATION TEST / ShortsPublisher — Stage 6.
- Six seconds, 540×960 portrait, 24 fps, H.264, AAC low-volume generated sine tone.
- 72,353 bytes and 144 decoded frames, verified by the application's FFprobe inspector.
- SHA-256: `08627815bda4005b3ec34030bb9f3015bd1a69a9aed2601ed3cecb8f8db0b5fe`.
- User-saved title: `testing`; description: `test description`; tag: `test tag`.
- Made for Kids: No; synthetic disclosure: No; privacy: Private.
- Metadata revision 3 was reviewed at revision 3 before the attempt.

The user chose the saved metadata rather than the earlier proposed wording. No production Short or
third-party footage/music was used. The file remained unchanged. FFmpeg lacked drawtext, so Qt
rendered the text slate and FFmpeg encoded it. Media evidence is retained alongside the source.
Available local space at preparation was 733,554,556,928 bytes; that measurement was not repeated
immediately before the user clicked upload. Actual media inspection and SQLite writes succeeded.

## Integration defects and resolutions

The confirmation screen omitted file size. It now displays exact source path and byte size alongside
filename, metadata, destination, declarations and forced Private privacy. The existing UI test asserts
these fields. No redesign, dependency change or schema migration was introduced.

The supplied Google Desktop-client download contained the official legacy authorization URI
`https://accounts.google.com/o/oauth2/auth`. The validator previously accepted only the modern URI
and rejected this download. It now accepts exactly either official URI and normalizes the Flow
configuration to the fixed modern endpoint. HTTP, lookalike hosts and query-modified URIs remain
rejected. A focused regression test covers normalization and rejection. The original JSON was not
modified or copied into the checkout; credential values were not printed.

The first live browser attempt returned Google's Testing-mode `403 access_denied` message for
non-approved testers. The supported resolution was to add the signing-in account under Google Auth
Platform → Audience → Test users in the client-owning project. Subsequent user authorization
succeeded. Console settings were not independently inspected; successful live channels.list and
videos.insert demonstrate that the tested client/account could access the required API operations.
No public production launch, verification bypass or restriction workaround was used.

When the user initially could not upload, inspection found a complete review and zero attempts.
The exact initial UI message was not captured. The app's five-minute account-verification timer
was a possible eligibility blocker, not a conclusively diagnosed defect. Reopening restored live
channel verification; the user then completed the real upload. No unexplained critical error remains.

## Automated regression

Environment: macOS Apple Silicon, Python 3.12.14, PySide6 6.12.0, FFmpeg/FFprobe 9.0.2.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Initial Stage 6 preparation: **211 passed, 0 failed, 0 skipped in 39.820 seconds**.
After the real-client compatibility fix: **212 passed, 0 failed, 0 skipped in 40.241 seconds**.
Ruff passed; final formatting reported 48 files already formatted; mypy found no issues in 22 source
files. Local OAuth listener tests ran with the necessary permission. All automated Google responses
were controlled; these tests were not counted as live integration evidence.

Logs: `artifacts/stage6-preflight/automated-tests.log` and
`artifacts/stage6-preflight/client-regression-tests.log`.

Coverage retains prior account restoration, OAuth/PKCE/state, identity/scope enforcement, Private-only
payloads, serialization, resumability, acknowledged offsets, bounded retries, failed grants,
reconciliation, duplicate prevention, database migration/persistence, Keychain failures/redaction,
source/review integrity and UI/shutdown regression tests. Unusual failures remain controlled tests;
no live failure was deliberately induced to exercise them.

## Native macOS verification

```sh
.venv/bin/python tests/native_upload_smoke.py prepare --root artifacts/stage6-controlled
.venv/bin/python tests/native_upload_smoke.py reopen --root artifacts/stage6-controlled
.venv/bin/python tests/native_upload_smoke.py history --root artifacts/stage6-controlled
.venv/bin/python tests/native_accounts_smoke.py prepare --root artifacts/stage6-accounts
.venv/bin/python tests/native_accounts_smoke.py reopen --root artifacts/stage6-accounts
.venv/bin/python -m shortspublisher --smoke-test --data-dir /tmp/publisher-stage6-entry
```

All six controlled native processes passed. The smoke entry point exited with code 0 at 18:23:10
local time on October 16. Existing Stage 5 harnesses were reused with fresh roots; their output
therefore still says Stage 5. They use real Cocoa, SQLite, FFprobe and isolated Keychain services,
with controlled Google responses and a separate three-second fixture. Test Keychain entries were
removed by their final phases. These checks preceded the client-URI fix; the actual subsequent
browser connection/upload exercised that fix in the running native application.

Screenshots at 760×560 and 1180×780 cover confirmation, progress, interruption, reconciliation,
success and history; representative confirmation/result captures were inspected. They show
readable details and scrollable controls. They are controlled screenshots, not screenshots of the
live uploaded video. Native accessibility inspection of the live app timed out, so live evidence
comes from user interaction, production persistence and independent API verification instead.

All 250 retained ShortsStudio reference hashes matched and its git status remained clean. The
normal user database and unrelated credentials were preserved. The isolated live workspace and its
legitimate connection were retained; its real credential was not deleted. No remote video was
modified or deleted after creation.

## Safe continuation and remaining limitations

Reopen the verified live workspace with:

```sh
.venv/bin/python -m shortspublisher --data-dir artifacts/stage6-live
```

The existing video is already uploaded. Inspect history or use Verify Remote Result; do not create
another attempt merely to check it. Keep the original source and the live database. Authorization
and resumable capabilities remain in their existing macOS Keychain stores, not reports or logs.
The live database contains account configuration/identity and must not be treated as a public fixture.
No access/refresh token, authorization code, session URL or sensitive request header is included here.

- This verifies one account, channel, small video and successful real transfer. It does not prove
  every account, Brand Account, quota restriction, large upload or future API behavior.
- Production refresh-token expiry/rotation, revocation, live interruption and session expiry were
  not forced; controlled tests cover these failure/recovery paths.
- Independent Private/processing status is true at the observed check, not a promise that remote
  state can never change. The test did not exercise Public upload or publishing eligibility.
- Native checks were macOS only. Windows/Linux secure account storage remains unimplemented.
- SQLite, Keychain and Google operations are not one atomic transaction. Unknown remote outcomes
  still require the existing reconciliation workflow; exactly-once remote creation is not claimed.
- Optional remote deletion of this disposable test video requires separate explicit approval.

## Official references checked

- [Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Manage App Audience](https://support.google.com/cloud/answer/15549945?hl=en)
- [channels.list](https://developers.google.com/youtube/v3/docs/channels/list)
- [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)
- [Resumable upload protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)
- [videos.list](https://developers.google.com/youtube/v3/docs/videos/list)

## Next stage — not started

The [roadmap](roadmap.md) proposes Stage 7 for explicitly confirmed direct Public uploads through the
official API, with applicable restrictions verified and no scheduling yet. Later stages cover the
application-owned queue/calendar, approved scheduled execution and optional background service,
without YouTube-native scheduling. Each requires explicit approval. Stage 6 ends here.
