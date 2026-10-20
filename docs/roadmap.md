# ShortsPublisher development roadmap

This records the user's scheduling architecture decision. It is not authorization to implement
future stages or execute uploads. Stage 6 completed interactive Google authorization, one directly
approved Private upload, independent result verification and restart checks on 2026-10-17.
Stage 7 now supports explicit manual Private and Public uploads; live Public verification and restart preservation passed on 2026-10-17.
Each later stage needs explicit approval, implementation, debugging and independent verification.

## Application-owned scheduling

The intended workflow is:

**Videos → Details → Review → Schedule → Calendar/Queue → Upload History**

Users will select a reviewed video, destination channel and local date/time, then approve a durable
publishing job. ShortsPublisher will control when the official API upload begins. For YouTube, the
chosen future design initiates the upload with `status.privacyStatus=public` and omits
`status.publishAt`. YouTube's native scheduling is not part of this design.

The scheduled time means **upload start time**, not guaranteed public availability. Transfer time,
YouTube processing and platform restrictions can delay or prevent availability. Record scheduled
start, actual start, transfer completion, observed visibility and processing state distinctly; do
not invent an exact public-release timestamp from a successful upload response.

Approval must bind the exact source, reviewed metadata revision and declarations, destination,
date/time and Public visibility. That approval authorizes that specific future upload without
another execution-time confirmation. Changes to those approved terms require renewed approval;
the engine must not silently substitute content or channels. The completed Stage 6 Private test used its own direct final confirmation.

## Durable execution and safeguards

The eventual SQLite-backed queue will own approved jobs, dates, dependencies, progress, outcomes
and recovery. Calendar and queue views must use persisted records. Requirements include:

- UTC execution timestamps with the chosen time zone and correct handling of ambiguous or nonexistent
  daylight-saving local times. Approval must make the resolved execution time clear.
- Editable schedules, cancellation and rescheduling, with immutable approved source/metadata
  snapshots, SHA-256 integrity checks and an auditable execution history.
- One claimed execution per job across processes, recovery after crashes/restarts and reuse of
  existing resumable sessions. Unknown remote outcomes require reconciliation, not new uploads.
- Preflight checks for the intended channel, permissions, secure token refresh, source integrity,
  valid reviewed declarations, conflicting attempts and known quota/account restrictions where
  observable. Failed checks preserve a recoverable job; no quota or policy bypasses.
- User-controlled publishing limits, useful errors/notifications and an emergency **Pause All
  Publishing** control. Pause prevents new work; the separate Stop action cancels an active transfer after its request returns.
  Neither can undo already accepted remote content.
- Explicit missed-job states and a user-approved missed-job policy. Do not unexpectedly publish
  hours or days late after restart, wake or reconnection.
- Result verification of the returned video ID, expected channel, actual visibility and processing
  state. An accepted transfer is not proof of completed processing or public availability.

Reuse the existing upload engine's integrity, resumability and reconciliation logic when the
relevant stage is approved. Stage 7 preserves explicit visibility intent and independent Public verification; later scheduling
must reuse those safeguards.

## Optional macOS background service

Stage 10 provides an explicitly enabled service that runs independently of the visible window and share the
durable database, job state and execution ownership safeguards. ShortsPublisher should manage its
safe start/stop/restart lifecycle and show service health in the UI. A closed window must not imply
that publishing is stopped when the user has enabled the service.

The service cannot upload when the Mac is asleep, powered off, disconnected or otherwise unable
to execute. Do not promise those capabilities. Missed times follow the approved policy, not an
implicit catch-up publishing loop. Background installation/execution is not enabled by this roadmap.

## Platform boundaries

Keep the eventual scheduling engine independent of YouTube-specific fields and API behavior.
Platform adapters should own credentials, rules, payloads, uploads and result interpretation; the
central engine should own approval, timing, execution ownership and recovery coordination.
Future targets are YouTube Shorts, TikTok, Instagram Reels, Facebook Reels, Snapchat Spotlight and
other supported platforms. No other-platform integration or speculative adapter framework is being
implemented now; each requires its own official capability review and verified stage.

## Proposed stage boundaries

| Stage | Scope | Gate |
| --- | --- | --- |
| 6 — complete | Real Google authorization, one explicitly approved Private upload, independent result verification and restart without duplication | User participation and all live acceptance criteria |
| 7 — complete | Explicitly confirmed direct Public upload through the official API; no scheduling | 249 controlled tests passed; one user-confirmed Public upload independently verified, processing succeeded, restart preserved the same attempt |
| 8 — implemented | Persistent queue, date/time and calendar controls, exact job approval and deterministic scheduler tests | Local plans, DST-safe approval, persistent queue/calendar and due-time evaluation; execution remains disabled |
| 9 — complete | Connect the verified scheduler to automatic scheduled Public uploads, with recovery, notifications and safeguards | 390 controlled tests and 11 native checks passed; one user-authorized automatic Public upload independently verified with successful processing and restart without duplication |
| 10 — implemented | Optional per-user macOS LaunchAgent, shared execution ownership and persistent service controls | Controlled actual-launchd verification; real background YouTube upload remains unverified |

These are suggested boundaries and may be split further when findings justify it. They do not
authorize beginning another stage. Uploading privately in advance and later changing visibility
is not the chosen initial design; any such extension would need separate scope, permissions and
verification while still avoiding YouTube-native scheduling.

[Stage 6 is verified](stage-6-verification.md). See [Stage 7 results](stage-7-verification.md).
Stage 8 implements local plans only. See [Stage 8 verification](stage-8-verification.md).
Stage 9 implements foreground automatic Public uploading with explicit per-job activation and a
paused-by-default global switch. Existing Stage 8 approvals remain disabled. See [Stage 9 verification](stage-9-verification.md).
Stage 10 adds optional background execution with separate service and per-job consent. See [Stage 10 verification](stage-10-verification.md). A real background upload needs separate authorization. Stage 11 refines the existing desktop UI, adds local preview, and preserves the verified backend boundaries. See [Stage 11 verification](stage-11-verification.md). Any live background test still requires separate authorization. Packaging and other platforms require separate scope.


Stage 12 is the focused library/preview usability stage: local search, sorting, saved-status filters,
selection retention and playback feedback, preserving upload/scheduling authorization boundaries.
See [Stage 12 verification](stage-12-verification.md). Stage 13 supersedes the previous navigation
behavior; packaging and new platforms remain outside scope.

## Stages 14–15 and the next independently authorized stage

| Stage | Scope | Verification boundary |
| --- | --- | --- |
| 14 (implemented) | Watched-folder discovery, stability, local import and persistent content deduplication | Local-only; native OS picker acceptance remains unverified |
| 15 (implemented) | Finite approved Public batches, persistent ordered queues, daily/whole-hour rules, DST/windows, atomic opportunities, existing foreground/background execution | Controlled native/automated transport; no live recurring YouTube upload |
| 16 (implemented) | Versioned future-folder authorization, metadata rules, bounded enrollment and existing recurring Public uploader | 699 tests pass; native foreground/background verified with controlled Google services |

See [Stage 14 verification](stage-14-verification.md) and
[Stage 15 verification](stage-15-verification.md). Stage 15 preserves individual scheduling and
manual uploads. New watched-folder files are never silently approved for review or publishing.
Daily and 1–24-hour intervals use selected local days and inclusive same-day windows. DST gaps
skip, overlaps use the first occurrence. Empty, blocked and missed opportunities do not accumulate
a backlog. Default global automatic claims remain three per UTC day (supported 1–10); existing
uploader preflight, ownership, quota handling and reconciliation remain authoritative.

Stage 16 implements exact future-folder/channel/Public scope, deterministic title templates, literal
metadata, explicit declarations, per-source immutable approval and revocation checks. Review Before
Publishing remains the default; standing automatic approval is distinct from human review. Existing
files are excluded, conflicting/uncertain content is held, and the existing allocator controls timing.
Profile edits require renewed consent. Monitoring alone never supplies publishing authority.

See [Stage 16 verification](stage-16-verification.md): all 699 tests passed, with zero failures or
skips. Native Cocoa, separate-process persistence, real isolated LaunchAgent, all 16 prior native
regressions and eight additional native commands passed. Actual OS picker selection remains an
explicit manual acceptance limitation. No production live upload or Google compliance certification
is claimed. Prioritize hands-on picker acceptance and long-form usability before later feature work.

Stage 17 controlled acceptance is complete: user-operated picker cancel/select and restart passed;
setup is divided into compact pages; unchanged review-waiting files avoid repeated hashing without
weakening enrollment/upload verification. All 708 tests and native regressions passed. See
[Stage 17 verification](stage-17-verification.md). The historical Stage 16 picker limitation above
is closed; live folder-to-YouTube verification remains pending personal in-app approval/activation.
No production service or live migration was performed. Stop after Stage 17. No Stage 18, packaging,
distribution or new platform integration.

Stage 18 was a maintenance stage: English interface on non-English macOS Regions, regenerable
cache cleanup, a classified `artifacts/` inventory with an approved 542 MB cleanup, and
`CLAUDE.md` / `current-status.md` / `development.md`. No feature, schema or publishing change.
See [Stage 18 verification](stage-18-verification.md). Next work needs explicit instruction.

Stage 19 adds TikTok through Buffer's official API (no unofficial TikTok access): Buffer PKCE
sign-in via an HTTPS relay page, Cloudinary public hosting, exact-time automatic posts, status and
reconciliation, duplicates per SHA-256 + account, and folder automation with separate consent.
Verified with controlled services only; live use awaits the user's setup and authorization. See
[Stage 19 verification](stage-19-buffer-tiktok-verification.md).
