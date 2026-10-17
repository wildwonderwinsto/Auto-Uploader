# Stage 10 verification — 2026-10-17

**Stage 10 implements optional macOS background publishing and has passed controlled native
LaunchAgent verification. No real background YouTube upload was authorized or performed.**
The real StonkYa service was not installed or enabled. Its existing workspace was not migrated.
Stage 11, packaging, remote visibility changes and remote deletion were not implemented.

## Architecture and implemented scope

The development checkout uses a per-user LaunchAgent, `com.shortspublisher.publisher`, in the
current user's `gui/<uid>` launchd domain. `worker_entry.py` is an absolute script entry launched
with the absolute virtual-environment interpreter and absolute data-directory argument. `worker.py`
composes the existing services and imports no Qt. There is no hidden window, detached shell,
LaunchDaemon, root requirement, public administration endpoint, second uploader or new dependency.

The worker reuses AutomaticPublisher, ScheduleService, UploadService, account locking and the existing
Keychain backends. It retains exact reviewed source/metadata/time/channel binding, Public payloads,
no `publishAt`, UTC timing, the 60-second start window, daily budgets, source rehashing, live account/
scope/channel preflight, acknowledged resumable offsets, bounded retries, independent videos.list
verification, processing distinctions and explicit reconciliation. No automatic session resume or
second initialization is added. Existing manual Private/Public workflows remain available.

Internal milestones were preservation/research, consent and ownership/storage, headless/lifecycle
integration, native Settings and shared status, isolated actual-launchd transfer/failure verification,
and full regressions/documentation. The real workspace and reference were read-only throughout.

New production files: `background_state.py`, `background.py`, `worker.py`, `worker_entry.py`, and
`ui/background.py`. Existing library/execution/upload composition and UI controllers were extended.
New tests include `test_background.py`, `test_background_ui.py`, the native background harnesses,
and `tests/fixtures/library-v7.sqlite3`. No Python dependency or lockfile change was needed.

## Consent and service lifecycle

Migration starts background Disabled, with no installation, start request or login startup. The
existing global publishing preference is preserved. Enable Background Publishing requires separate
consent explaining execution after GUI closure, no execution-time confirmation, awake/login/network
requirements, possible Keychain prompts, and Pause/Stop behavior. Enable writes configuration and
consent; Start is a separate action. Opening Settings only queries status.

Per-job activation has a separate unchecked-by-default background checkbox. Its authorization binds
the existing exact snapshot and approved revision. Older automatic approvals remain desktop-only;
they cannot publish through the service merely because it was enabled. Changing their scope requires
a future reschedule/reapproval and explicit background activation. Global scheduled publishing must
also be Enabled. Each layer is independently checked before an initial POST.

The session-only plist lives under `<data-dir>/background/`. A separate login preference writes the
same managed label under `~/Library/LaunchAgents/`. Start must be selected once after enabling.
Stop clears the execution request, so it stays stopped until Start, including after a later login.
Disable also removes login startup. Remove stops/unregisters and removes only these managed plist
files, preserving media, database/history and Google credentials.

Configurations are written atomically with mode 0600 into an owned directory. Unsafe ownership,
symlinks, writable-by-other-users data directories, invalid identifiers, missing runtimes and
mismatching existing configurations are rejected. One stable label supports one workspace per user;
another workspace/runtime cannot silently overwrite it. Lifecycle operations use a per-user label
lock. There are no credentials, session URLs or arbitrary shell commands in launch arguments/plists.

Supported launchctl operations are bootstrap, print, kickstart, kill SIGTERM and bootout with fixed
validated targets and bounded subprocess timeouts. Stop waits up to approximately 40 seconds plus
bounded OS queries for the process to finish; timeout reports still stopping and does not pretend to
have removed the service. There is no forced takeover. `KeepAlive=false` prevents repeated crash
relaunches; after a crash the user inspects history and explicitly starts it again.

## Ownership, shutdown and recovery

A library-specific OS file lock is held through each foreground dispatch/transfer and through the
entire background worker lifetime. Starting background execution waits for an existing foreground
owner to finish. SQLite records a random owner identity, kind, PID, heartbeat and state. Claimed
executions record the owner identity; the uploader checks it against both the durable owner and its
in-process identity before transfer and initial POST. The existing upload/account locks and unique
job/attempt links remain in force. No network operation holds a SQLite write transaction open.

While background execution is requested, the GUI reads status/history and does not run scheduled
startup recovery or claims. A dead/stale heartbeat never grants another dispatcher ownership. Explicit
Stop clears the service request, and the OS lock must also be released before the open GUI can resume
foreground scheduling. Use global Pause to prevent new starts in both modes. Starting or replacing a
worker never resets a claimed execution to Scheduled or invents a new session for an unknown outcome.

The headless loop waits until the next future scheduled instant, capped at five seconds. A separate
two-second health monitor observes Stop and updates status during blocking requests. SIGTERM sets
cooperative cancellation; shutdown waits for the current request, preserves the upload's actual or
uncertain outcome, then releases ownership. Keychain/OS stalls can outlast network/control timeouts.
Startup conservatively marks already-past unclaimed jobs Missed, including within the old start
window, and marks orphaned active/claimed attempts Interrupted without transferring bytes.

Closing/Quit stops only the GUI's workers and leaves the authorized LaunchAgent running. Settings
provides Stop Background Publishing and Quit; it suppresses new foreground timer ticks, stops the
service and closes only after a successful control result. Failed stopping leaves the interface open.
Stopping locally never deletes remote content. A transfer already accepted remotely may still exist.

## Storage, status, Keychain and diagnostics

Schema 7→8 is transactional and adds four nonsecret tables: background_config, background_owner,
background_consent and execution_owner. No previous table or column is changed. The v7 migration
fixture is a SQLite backup of the genuine controlled Stage 9 native workflow, not live credentials.
Tests compare every prior table value, check disabled defaults and foreign keys, and force migration
failure to prove rollback. Future schemas remain rejected without resetting data.

Settings combines actual launchctl loaded-path/PID results with matching worker PID, initialization
state and heartbeat freshness. A launched PID alone reports Starting; a stale check-in reports
Stale / unresponsive; an OS query failure is an error rather than Stopped. Dashboard shares these
observations. Queue, Calendar and Upload History read the same persisted attempts and acknowledged
bytes regardless of which process transfers them. Credentials/channel are explicitly labeled as
checked during preflight, not continuously monitored.

The production worker uses the same explicit macOS Keychain backend and services as the existing
uploader, without plaintext fallback. Test LaunchAgents retrieved only synthetic entries under
`ShortsPublisher.Stage10.NativeVerification` and `ShortsPublisher.Stage10.NativeSessions`. Real
launchd access succeeded in this logged-in user context without an observed interactive prompt.
This is not proof of every locked-Keychain, ACL-denial, unsigned/signed-runtime or permission-dialog
case. Missing credentials/scopes were tested under actual launchd; prior injected storage-denial and
corruption coverage remains in the full suite. No real StonkYa token/session was read for these tests.

Production logs rotate at 1 MB with three backups under `<data-dir>/background/logs/worker.log`.
They record timestamps, initialization/shutdown, job/upload IDs, state changes and acknowledged byte
counts. Attention/missed/failure states are warnings. Exceptions use sanitized messages; SDK wire
logging remains suppressed. Tokens and session capabilities are absent from database/plist/log output.

## Automated checks

Environment: macOS Apple Silicon, Python 3.12.14, PySide6 6.12.0, FFmpeg/FFprobe 9.0.2.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
```

Final result: **436 passed, 0 failed, 0 skipped in 98.777 seconds**: all 390 prior tests,
37 new service/storage/ownership tests and nine new Qt service/consent tests. Ruff passed; formatting
reported **81 files already formatted**; mypy found no issues in **33 source files**. Output is retained
in `artifacts/stage10-final/automated-tests.log` and `quality-checks.log`. OAuth loopback tests run with listener permission. Google responses are controlled;
ordinary regression tests never access real YouTube credentials or upload endpoints.

Coverage includes explicit service/job/login consent, default-disabled migration and exact preservation,
rollback/future versions, safe plist ownership/path/permission validation, idempotence, lifecycle failures,
missing runtime, stop timeout, OS status errors, stale heartbeat, foreground/background contention,
separate-process lock exclusion, deferred transfer handoff, cancellation of pending handoff, owner-token
fencing before POST, GUI startup while a worker owns a claim, no late catch-up, source/metadata changes,
SQLite lock contention, exact bytes and one attempt, headless imports, cross-thread health reporting,
UI cancellation/close/Stop routes and complete existing uploader/OAuth/private/public recovery coverage.

Early runs exposed outdated Settings object-name/placeholder assertions and old Stage 8 consent wording.
Settings identity and teardown were corrected; the consent test now checks the actual permission text.
The first core run also identified the expected schema-number update. No tests were removed or skipped.
Review caught cross-thread FileLock visibility and made ownership explicitly shared with the health
monitor; a regression exercises it. The absolute production script entry was tested under actual launchd.

## Native macOS evidence

Final background workflow:

```sh
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_background_smoke.py prepare --root artifacts/stage10-native-final
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_background_continue.py --root artifacts/stage10-native-final
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_background_failures.py --root artifacts/stage10-failures-final
.venv/bin/python tests/native_worker_entry_smoke.py --root artifacts/stage10-production-entry
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_service_controls.py --root artifacts/stage10-controls
```

The continuation starts separate `active` and `complete` GUI processes using native_background_smoke.
The setup process imported/FFprobe-inspected a generated portrait, saved Public metadata/declarations,
completed Review, displayed real schedule/activation dialogs with background scope, enabled the isolated
service and global publishing, and exited. Its PID was confirmed absent before advancing the injected
clock. The launchd worker remained alive, parent PID 1, with no Qt imports and a successful real synthetic
Keychain read. No system clock or sleep setting was modified.

The worker called the existing uploader and transferred exactly **1,762,359 bytes**. The new GUI
process reopened while **262,144 bytes** were acknowledged, displaying the same attempt without taking
ownership. Controlled independent verification returned synthetic ID `TestVid123_`, Public privacy and
processed status. This is not a YouTube video or a live Google observation. Exactly one POST and one
attempt were recorded. Explicit worker restart retained the same attempt and did not create another
session. A third GUI process verified history and source bytes/hash, paused global publishing, removed
the service through Settings and removed/checked absence of its synthetic Keychain entries.

Final main-workflow attempt: `992728e62d7648bd8b2abd59d857d1cb`. Worker PID 75051 and reopened GUI PID
75104 were distinct. Evidence includes prepare-result.json, worker-identity.json, active-result.json,
complete-result.json, process-evidence.json and controlled request counts under `artifacts/stage10-native-final`.

Seven separate actual-launchd failure cases passed: wrong channel, missing credential, missing upload
scope, changed source, missed startup time, worker SIGKILL during partial transfer followed by explicit
restart, and SIGSTOP/stale heartbeat followed by recovery. Blocked cases created no POST. The crash
case retained one Interrupted attempt and one original initiation after restart; it never resumed
implicitly. Each test service was stopped/unregistered and its synthetic Keychain entries removed.
Evidence: `artifacts/stage10-failures-final/results.json` and native-regressions.log.

The exact production worker entry also started under launchd against an empty, globally paused library
with no account configured. It initialized, reported Paused, wrote the rotating production log and
handled SIGTERM normally before removal. A separate real Cocoa check displayed a missing-runtime error,
then started that production entry and verified Stop Background Publishing and Quit stopped its PID
before closing the GUI. No Google operation was possible in these empty-library checks.

Eleven prior native/entry processes passed:

```sh
.venv/bin/python tests/native_public_smoke.py prepare --root artifacts/stage10-public
.venv/bin/python tests/native_public_smoke.py reopen --root artifacts/stage10-public
.venv/bin/python tests/native_public_smoke.py history --root artifacts/stage10-public
.venv/bin/python tests/native_upload_smoke.py prepare --root artifacts/stage10-private
.venv/bin/python tests/native_upload_smoke.py reopen --root artifacts/stage10-private
.venv/bin/python tests/native_upload_smoke.py history --root artifacts/stage10-private
.venv/bin/python tests/native_execution_smoke.py prepare --root artifacts/stage10-foreground
.venv/bin/python tests/native_execution_smoke.py reopen --root artifacts/stage10-foreground
.venv/bin/python tests/native_schedule_smoke.py prepare --root artifacts/stage10-planning
.venv/bin/python tests/native_schedule_smoke.py reopen --root artifacts/stage10-planning
.venv/bin/python -m shortspublisher --smoke-test --data-dir artifacts/stage10-entry
```

These retain real Cocoa, SQLite, media inspection and isolated Keychain with controlled Google responses.
Entry point closed with code 0 at 11:45:47 local time. Logs are in `artifacts/stage10-final/`.
Harnesses supply picker selections and confirmation answers; manual OS picker/consent interaction is
not asserted. Screenshots at 760×560 and 1180×780 cover Settings, activation, running/removed service,
reattached active Dashboard/Queue, completed Calendar and runtime error. Representative screenshots
were visually inspected for readable wrapped text, scrollable controls and no horizontal overflow.

## Preservation, limitations and use

Read-only before/after hashes match every table in the live library exactly. All three original
source hashes match. All 250 retained ShortsStudio hashes match and its git status is clean. The
18 isolated test service labels and all test worker processes are absent; the production label and
login plist are absent. All native harnesses removed their synthetic Keychain entries. A scan of
generated databases, plists, logs and JSON evidence found no synthetic token/session capabilities.
Superseded exploratory roots and their test cache locks were removed; final evidence remains.
See `artifacts/stage10-final/preservation.json`. The live library remains schema 7 with the
original Stage 6 Private `bfNDN1Gq540`, Stage 7 Public `OxgK8zkDmkI` and Stage 9 automatic Public
`vmbsEJ4E9Rc` records. No real service is installed, no prior job gains background permission, and no
remote content was uploaded, modified or deleted during Stage 10 development.

To use the feature, close the old application and back up its database before explicitly launching
updated code against that same directory. Open Settings, review Enable consent, choose login startup
only if desired, then Start and confirm fresh health. Review a new Public video, approve a future plan,
select its separate background permission, and enable global publishing. Closing the interface then
leaves the requested service running. Pause prevents new starts; Stop waits for in-flight work; Disable
removes login startup; Remove deletes only managed service configuration. Use Upload History to resolve
interrupted/uncertain outcomes rather than creating a second upload.

A future real background test requires separate user authorization for a new disposable video, actual
StonkYa identity/scopes, exact metadata/time/Public/background approval and service/global enablement.
Verify GUI exit, worker-owned initiation, actual returned ID, independent Public/channel/processing
results and restart without duplication. No such real background test occurred here.

Remaining limits include macOS-only user-session operation, no wake/pre-login/powered-off execution,
no actual logout/login or exhaustive sleep/OS clock testing, no signed/notarized package, and no
universal wall-clock bound on Keychain/network/OS stalls. Login plist generation is tested but a real
logout/login cycle was not performed. Crash/restart was tested with an actual killed test worker;
power-loss and SQLite/Keychain/YouTube atomicity are not guaranteed. Public API observations do not
guarantee perpetual visibility or playback. Service status is a bounded observation, not a guarantee
of future execution. Same-user malicious code can already access that user's workspace; this is not
a sandbox against the owning user. Separate uncoordinated database copies remain unsupported.

Keep the source checkout/virtual environment at their registered paths. Stop/remove before moving them;
a missing executable is reported instead of assumed healthy. A future bundle should use a stable
installed runtime and evaluate SMAppService, embedded helpers, signing, Background Items approval and
Keychain authorization under the final identity. No installer was built.

## Official research

Apple's archived [Creating launchd jobs](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
describes per-user agents and launchd lifecycle. Current [SMAppService](https://developer.apple.com/documentation/servicemanagement/smappservice)
and [registration](https://developer.apple.com/documentation/servicemanagement/smappservice/register())
cover bundled helper registration subject to user approval. The
[background-process overview](https://developer.apple.com/documentation/appkit/managing-ongoing-background-processes-in-your-mac)
and [Service Management](https://developer.apple.com/documentation/servicemanagement/) were reviewed
alongside local launchctl help. Apple's [Security result codes](https://developer.apple.com/documentation/security/security-framework-result-codes)
distinguish unavailable interaction; access denial is handled without weakening Keychain security.
Research and native observations are dated October 17, 2026. These references do not establish that a
Python development checkout is a signed, packaged or distribution-approved app.

Recommended Stage 11 scope, only if separately requested: an explicitly approved real background test
and focused fixes from its observations. Packaging, other platforms and additional features remain
out of scope. **Stop after Stage 10.**
