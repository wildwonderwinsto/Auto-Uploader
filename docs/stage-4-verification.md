# Stage 4 verification — 2026-10-15

Stage 4 implementation and independently verifiable integration checks are complete. **Live Google
authorization was not performed.** No real Desktop client was supplied for an interactive test, and
no Google sign-in or consent screen was opened automatically. Controlled OAuth/API tests are not
proof of a live Google connection. Stage 5 was not implemented.

## Implemented scope

Accounts now supports Desktop-client JSON setup, system-browser authorization, explicit channel
confirmation, macOS Keychain storage, startup restoration, channel refresh, cancellation, and
confirmed local disconnect with optional remote revocation. It requests only
`https://www.googleapis.com/auth/youtube.readonly`. No upload, scheduling, analytics, modification,
or other-platform endpoints were added.

The interface distinguishes disconnected, connecting, verification pending, recently verified,
temporarily offline and reconnect-required states. Channel title, stable ID, available handle,
description and last verification time come from the API result. Multiple channels require selection;
even a single new channel requires explicit confirmation. Optional thumbnails are fetched from
allowed Google image hosts with size limits and no authorization header. Cached identity is labeled.
A five-minute freshness timer clears recently-verified status without polling YouTube.

Read-only reference work used ShortsStudio's worker boundaries and existing design language.
All 250 source/reference hashes in the retained reference snapshot match; its git status is clean.
No ShortsStudio files, credentials, caches or components were copied or modified.

## Files and dependencies

Created:

- `src/shortspublisher/auth.py`: client validation, token model, Google OAuth and refresh/revoke.
- `src/shortspublisher/credentials.py`: credential-store protocol and explicit macOS Keychain backend.
- `src/shortspublisher/youtube.py`: read-only channel adapter, identity parsing, bounded retries/images.
- `src/shortspublisher/accounts.py`: account persistence, confirmation and serialized service operations.
- `src/shortspublisher/ui/accounts.py`: Accounts page and background worker.
- `tests/account_support.py`: explicitly controlled OAuth/API and in-memory credential providers.
- `tests/test_accounts.py`, `tests/test_accounts_ui.py`: account, OAuth, API, migration and UI tests.
- `tests/native_accounts_smoke.py`: native separate-process workflow with isolated real Keychain.
- `tests/fixtures/library-v2.sqlite3`: genuine Stage 3 native database backup containing saved review.
- `docs/stage-4-verification.md`: this report.

Modified:

- `library.py`: transactional schema v2→v3 migration.
- `app.py`, `ui/window.py`: composition, Accounts route, startup restore and coordinated shutdown.
- `tests/test_library.py`, `tests/test_preparation.py`: current schema expectations.
- `tests/test_shell.py`: functional Accounts route, isolated entry-point data and worker cleanup.
- `pyproject.toml`, `uv.lock`, `.gitignore`, `README.md`, `docs/architecture.md`.

New direct dependencies and installed versions:

| Dependency | Version | Purpose |
| --- | --- | --- |
| google-auth-oauthlib | 1.5.0 | Maintained Google flow, PKCE S256 and code exchange |
| keyring | 25.7.0 | Maintained macOS Security/Keychain integration, explicit backend |
| requests | 2.34.2 | HTTPS API, refresh and revocation with timeouts |
| filelock | 3.32.7 | Serialize account operations across app instances sharing a library |

The lockfile includes transitive dependencies. No generic plugin framework or future account
providers were introduced. New packages were installed with:

```sh
uv add 'google-auth-oauthlib>=1.2,<2' 'keyring>=25,<26' 'filelock>=3.16,<4' 'requests>=2.32,<3'
```

## Authentication and credential boundaries

The application validates an `installed` client configuration and Google's fixed authorization
and token endpoints. Only required public desktop-client fields are retained. The source JSON
path and unused fields are not persisted. The desktop client secret is public-client configuration,
not a securely embedded application secret or user authorization token.

The official Google Flow generates PKCE S256. Request state uses a cryptographically secure random
value. The callback binds exclusively to 127.0.0.1 on an ephemeral port, checks path, Host, one exact
state value and unambiguous code/error fields. Invalid state/path responses cannot consume the valid
callback. The listener suppresses access logs, limits socket reads, times out after three minutes,
and closes on success, failure or cancellation. Google login is never embedded or automated.

Code exchange uses the library's request hook to disable redirects at the HTTP layer. A regression
test verifies this: extra `fetch_token` keyword arguments would otherwise become OAuth form fields.
Refresh and revocation send tokens only in POST bodies; channel requests use the Authorization
header. No tokens enter URLs, SQLite, ordinary JSON files, UI text or diagnostic messages.
Account exceptions are sanitized instead of logging SDK bodies or tracebacks. SDK wire logging is
suppressed. Token object representations omit their values.

Private credentials live only in the explicitly selected macOS Keychain backend, service
`ShortsPublisher.YouTube`, under random credential identifiers. Backend autodetection and plaintext
fallback are not used. A read/write/delete failure is reported. Missing credentials do not confirm
a connection, even when cached channel information exists. New authorization is held in memory
until channel confirmation; confirmation expires after five minutes.

A per-library file lock prevents concurrent refresh, replacement and disconnect operations from
writing stale credentials. Refresh-token rotation is saved before another channel request. HTTP 401
permits at most one refresh-and-retry. Expired/revoked refresh grants require reconnecting; transient
network failures retain credentials. Replacement requires explicit disconnect first.

SQLite and Keychain cannot commit in one shared transaction. A failed account-row write attempts
to remove the newly created Keychain entry. A crash between the two writes can leave an unreferenced
Keychain entry; no connected status is emitted before both writes succeed. A failure to save a
server-rotated token may require reconnection. These crash windows are documented, not claimed away.

Disconnect removes the referenced local Keychain item before clearing the account row; it leaves
videos and preparation untouched. Optional remote revocation warns that other permissions for the
same Cloud project can be affected. Only HTTP 200 is reported as Google-confirmed revocation.
An offline or failed revocation is distinguished from successful local removal.

Official sources researched October 15, 2026:

- [YouTube installed application authorization](https://developers.google.com/youtube/v3/guides/auth/installed-apps)
- [Google native application OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)
- [YouTube channels.list](https://developers.google.com/youtube/v3/docs/channels/list)
- [Google token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)
- [Google OAuth Flow library](https://google-auth-oauthlib.readthedocs.io/en/latest/reference/google_auth_oauthlib.flow.html)
- [Keyring documentation](https://keyring.readthedocs.io/en/latest/)

## Channel identification and migration

The API adapter calls only `channels.list`, `mine=true`, `part=snippet`, with bounded pagination.
No statistics or unrelated identity scopes are requested. No-channel results explain what to fix.
Multiple results require explicit stable-ID selection. Refresh requires the previously confirmed
channel ID to remain in the authorized results; an unexpected channel never silently replaces it.
Brand Account selection takes place in Google's browser authorization flow.

Schema version 3 adds two platform-keyed tables: `account_config` for minimal public configuration,
and `account_connection` for credential reference, channel data and verification time. Tokens are
absent. Version 2→3 runs transactionally; earlier migrations remain supported. Video rows, publishing
payloads, revisions and reviewed-media bindings are preserved byte-for-byte by migration tests.
Failure rolls back the migration, and future schema versions are rejected without resets.

The v2 fixture is a SQLite backup from the actual Stage 3 native workflow, including its generated
video metadata and completed review. Tests verify both migration and disconnect preserve those
records. Existing source-video hash verification still matches the pre-workflow hash.

## Exact verification results

Environment: macOS Apple Silicon, Python 3.12.14, PySide6 6.12.0, FFmpeg/FFprobe 9.0.2.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Final result: **151 passed, 0 failed, 0 skipped in 21.532 seconds**. This includes all 92 prior-stage
tests and 59 new account/OAuth/UI tests. Ruff passed; the final formatting check reported 37 files already formatted;
mypy reported no issues in 18 source files. Local callback tests need socket-listener permission;
the final suite ran outside the restricted sandbox. All network responses remained controlled.

Coverage includes actual loopback requests, S256 derivation/verification, state mismatch and
duplication, unexpected callback paths, cancellation, timeout, listener cleanup, the official SDK's
real exchange path with controlled HTTP responses, sanitized exchange errors, missing scopes,
refresh expiration/rotation, revoked grants, Keychain failure/corruption, API pagination and identity,
401/403/quota/429/5xx/network handling, bounded retry, thumbnail limits, restart persistence,
local/remote disconnect outcomes, configuration races, cross-instance locking, migration rollback,
review preservation, secret-leak assertions, repeated clicks, close/Quit during authorization,
UI counters/states and all existing media/library/editor regressions.

Initial verification exposed and resolved test HTTP-fixture issues and the OAuth HTTP-option/form-field
integration issue. No tests were silently skipped to obtain a passing run.

Native entry-point smoke:

```sh
.venv/bin/python -m shortspublisher --smoke-test --data-dir /tmp/publisher-stage4-entry
```

Passed: startup followed by `Desktop closed with code 0` at 18:46:55 local time.

Native final workflow:

```sh
.venv/bin/python tests/native_accounts_smoke.py prepare --root artifacts/stage4-verified
.venv/bin/python tests/native_accounts_smoke.py reopen --root artifacts/stage4-verified
```

Both separate OS processes passed. They used real Cocoa widgets, application services, SQLite,
and the actual Keychain backend under `ShortsPublisher.Stage4.NativeVerification` with a random
entry key. OAuth and channel responses were controlled and clearly labeled in the UI.

The prepare process checked disconnected/missing configuration, file selection, configured state,
waiting/cancellation, explicit confirmation and successful secure storage. Reopen checked restoration
from Keychain and SQLite, exact channel identity, offline cached presentation, successful refresh,
confirmed disconnect, actual Keychain-entry absence, and shutdown during pending authorization.
The test-created Keychain entry was removed. No user credential was read, changed or deleted.

The file-picker return and disconnect dialog response were supplied by the harness. This does not
assert manual OS picker interaction. Screenshots were captured at **760×560** and **1180×780**, top
and bottom, for disconnected, connecting, cancellation, confirmation, connected, reopened, offline
and post-disconnect states. Representative captures at both sizes were visually inspected: readable
text, no horizontal overflow, clear channel IDs, reachable controls, and genuine indeterminate
activity. Useful nonsecret evidence remains in `artifacts/stage4-verified/`.

## Live authorization and remaining limitations

**Pending manual verification:** real Google client setup, system-browser consent, Brand Account
selection, real channels.list authorization, real token expiration/rotation/revocation, and live
thumbnail retrieval. These could not be established by controlled providers. No successful live
YouTube connection is claimed. The application is ready for an explicitly interactive test.

- Windows/Linux secure credential backends are not implemented; local library features remain
  available. Native UI checks were macOS only.
- Keychain reads/writes/deletion were tested on the real OS; denial, corruption and unavailable
  storage were injected deterministically. Actual OS lock/permission dialogs were not exhaustively tested.
- Network timeouts are finite connect/read timeouts, not proof against every possible OS/network
  stall. Cancellation waits for the active request to return. Keychain prompts may require user action.
- There is no background API polling; recently verified means the last successful check, not a
  guarantee of continuous access. Reconnection is a supported manual workflow.
- SQLite/Keychain crash windows are described above; abrupt power-loss testing was not performed.
- Pending channel confirmation exists only in memory and is lost on exit. OAuth consent can remain
  granted at Google even if confirmation is cancelled; remove project access at Google if desired.
- Separate copies of a database should not be used as simultaneous independent account stores;
  a copied credential reference can still refer to the original Keychain item on the same Mac.
- Real thumbnails and system-browser launch were not asserted by the native controlled harness.
- No packaging, uploads, scheduling, other social platforms or Stage 5 features exist.

## Google Cloud configuration and manual test

1. In [Google Cloud Console](https://console.cloud.google.com/), create/select a project and enable
   **YouTube Data API v3**.
2. Configure Google Auth Platform branding, audience and data access. Use only YouTube read-only
   scope. For External / Testing, add the participating Google account as a test user.
3. Create a **Desktop app** OAuth client and download its JSON outside this checkout. Do not use
   a Web client or service account. The installed application uses an ephemeral loopback port.
4. Launch `.venv/bin/python -m shortspublisher`, open Accounts, and choose the client JSON.
5. With your explicit participation, click Connect YouTube, authorize in the system browser,
   select the intended Google/YouTube/Brand Account, then return and confirm the displayed channel ID.
6. Allow macOS Keychain access if prompted. Verify the actual title/ID/handle and verification time.
7. Restart in another process and verify the same channel is checked live. Test Refresh Channel,
   then briefly go offline and verify cached details are not labeled as a fresh connection.
8. Test cancellation and denied consent. Confirm disconnect preserves videos, drafts and reviews.
   Optionally test Google revocation only after reading its broader project-permission warning.

External Testing tokens requesting YouTube access can expire after seven days. Production removes
that testing-specific rule but may require verification and never guarantees indefinite access.
Any later unattended publishing design must handle revoked/expired authorization instead of
bypassing Google's consent or verification requirements.

## Recommended next step

Complete the pending interactive Google test first. After explicit approval for a later stage,
consider a narrowly scoped manual upload workflow with separate upload consent, channel confirmation,
review enforcement and duplicate-upload safeguards. No such functionality was implemented here.
