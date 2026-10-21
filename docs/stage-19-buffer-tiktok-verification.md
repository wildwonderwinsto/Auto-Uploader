# Stage 19 — TikTok through Buffer — 2026-10-20

**Verdict: GO, for development.** Buffer's official API supports the whole path from a local MP4
to an automatic TikTok post with status tracking:

`local MP4 → media host (public HTTPS URL) → Buffer createPost (automatic) → TikTok → status`

All of it is implemented and verified against **controlled** Buffer and Cloudinary endpoints.
**No real Buffer, Cloudinary or TikTok request was made, and nothing was posted.** Live use needs
the external setup listed under "Steps you need to complete". The live acceptance test was not
run and needs your separate authorization.

## 1. Research findings (official sources, read 2026-10-17/20)

| Question | Finding | Source |
|---|---|---|
| Automatic Public TikTok video posting? | Yes. "Schedule your video in Buffer, and we'll automatically publish it for you". Videos "publish publicly by default". | [Using TikTok with Buffer](https://support.buffer.com/articles/using-tiktok-with-buffer-oGEroY9Of2) |
| Through the public API? | Yes: GraphQL `createPost` with `schedulingType: automatic`. `notification` is the manual reminder type. | [Create video post](https://developers.buffer.com/examples/create-video-post.html), [Post type](https://developers.buffer.com/types/Post.md) |
| Modes | `ShareMode` = `addToQueue`, `customScheduled`, `shareNext`, `shareNow`. `customScheduled` takes an exact `dueAt` (ISO-8601 UTC). | [ShareMode](https://developers.buffer.com/types/ShareMode.md), [Posts and scheduling](https://developers.buffer.com/guides/posts-and-scheduling.html) |
| Status reporting | `PostStatus` = `draft`, `error`, `needs_approval`, `scheduled`, `sending`, `sent`. Each post also has `sentAt`, `externalLink` ("external URL of the post at the destination service"), `error.message` and `schedulingType`. | [PostStatus](https://developers.buffer.com/types/PostStatus.md) |
| Submission vs publication | Distinguishable. Creation returns a Buffer post ID and `scheduled`/`sending`. Only a later `sent` (per Buffer: "successfully published to the social platform") counts as published. A Buffer post ID is not a TikTok video ID. | as above |
| TikTok settings in the API | Only `metadata.tiktok.isAiGenerated` (and `title`, for photo posts). **Not exposed:** privacy, comments, Duet, Stitch, commercial-content disclosure. | [TikTokPostMetadataInput](https://developers.buffer.com/types/TikTokPostMetadataInput.md) |
| Separate TikTok connection? | Yes. TikTok is connected inside Buffer (Channels). The help center describes TikTok **Business** accounts; Buffer's FAQ says Business is not required. This is unresolved, so check your own account. | support article; Buffer TikTok FAQ |
| Notification-only content | Music, trending audio, polls, and videos over 10 minutes. | support article |
| OAuth | Authorization code with **PKCE (S256) is required**. Public clients get a client ID only and never send a secret. Endpoints: `auth.buffer.com/auth`, `/token`. Scopes include `account:read posts:read posts:write` plus `offline_access` for refresh. | [Authentication](https://developers.buffer.com/guides/authentication.html), [Building apps](https://developers.buffer.com/guides/building-apps.html) |
| Desktop / redirect | Public clients are intended "for mobile, desktop" apps. Redirect URIs are pre-registered and compared "character for character". **"HTTPS is required, including on localhost."** | Building apps |
| Refresh tokens | "Every successful refresh returns a new refresh_token and invalidates the one you sent". "Reusing an old refresh token revokes all tokens for that grant." Concurrent refreshes collide, so one component must own refreshing. | Authentication, Building apps |
| Limits | API (per client): Free 100 / 15 min, 250 / 24 h, 3,000 / 30 days. Essentials 7,500 / 30 days, Team 15,000 / 30 days. 429 responses include `Retry-After`. Free plan: 3 channels and 10 scheduled posts per channel; TikTok is not plan-gated. TikTok via Buffer: 25 posts per 24 h; videos 3 s–10 min, ≤ 1 GB, ≥ 360 px, 23–60 fps, MP4/MOV/WEBM; captions ≤ 2,200 characters and ≤ 5 hashtags. | [API limits](https://developers.buffer.com/guides/api-limits.md), [Pricing](https://buffer.com/pricing), support article |
| Media | "There's no upload endpoint." The URL must be public, unauthenticated, direct HTTPS and stable "until the post publishes". Avoid signed or expiring URLs. Buffer suggests Cloudinary or Cloudflare R2. | [Hosting media](https://developers.buffer.com/guides/hosting-media.html) |
| Idempotency | `CreatePostInput` has **no** idempotency key, so a timed-out request must be reconciled, not resent. | API reference |
| Terms for folder-based posting | Buffer's API pages state no restriction on automated posting from an app. They require consent screens and fair use within the rate limits. I found no explicit statement either way about folder-driven automation. | Building apps |

The `createPost` input, `post`, `posts` and `deletePost` shapes were taken from the published
schema pages. They **have not been exercised against the live API**. Verify them once in Buffer's
API Explorer during the live test.

## 2. Account connection (real architecture)

**Flow:** Accounts → **Connect TikTok via Buffer…** → a dialog with **Open Buffer Channels…** (to
connect TikTok inside Buffer first) and **Sign In to Buffer…**.
1. The system browser opens `auth.buffer.com/auth` with a fresh PKCE S256 pair and a 256-bit
   `state`. Scopes: `account:read posts:read posts:write offline_access`.
2. After consent, Buffer redirects to **your registered HTTPS relay page**
   (`docs/buffer-oauth-relay.html`). The relay forwards the unchanged query to a one-shot listener
   on `http://127.0.0.1:47633/buffer/callback`.
3. The app compares `state` in constant time and exchanges the code with the verifier (no secret).
4. It lists your Buffer organizations' channels and keeps **TikTok only**. One usable channel is
   selected automatically; with several, you choose. With none, the sign-in is discarded and you
   are told to connect TikTok in Buffer first.
5. The Accounts card shows "@username · Connected through Buffer". IDs appear only under
   "Account details".

**Why a relay page:** Buffer requires HTTPS even on localhost. A self-signed localhost certificate
would show a browser warning, or require changing macOS trust settings, which would weaken system
security. The relay is a static page with a hash-pinned CSP, no external loads and `no-referrer`.
The code it forwards is useless without the verifier, which only the app holds (PKCE). If the
browser blocks the hop to `127.0.0.1`, or the port is busy, the page shows the text to paste into
the dialog. The pasted text is validated against `state` the same way.

**Refresh safety:**
- Tokens are stored in Keychain as JSON under `ShortsPublisher.TikTok`, separate from YouTube.
- Every token use takes a cross-process file lock, re-reads Keychain inside it, refreshes only if
  the token is within 2 minutes of expiry, and saves the rotated pair before releasing the lock.
- A **timeout or 5xx during refresh** is treated as "possibly rotated". The connection is marked
  **Reconnect required** and the stored refresh token is never used again, because Buffer revokes
  the grant on reuse.
- `invalid_grant` gives the same result. A 429 waits.
- Tested: two simulated processes refreshing concurrently make exactly one refresh, with no
  revocation; an uncertain refresh makes exactly one call, then refuses until reconnect.

**Setup check (2026-10-20):**
- The relay page is live at
  `https://wildwonderwinsto.github.io/autouploaderhtml/buffer-oauth-relay.html`, byte-identical to
  the project file.
- You saved your Buffer client in your live workspace. The redirect URI matches exactly.
- One unauthenticated GET of the authorization URL returned **303 to Buffer's sign-in
  interaction**, meaning the client ID, redirect, scopes and PKCE parameters were accepted. Nothing
  was signed in, posted or stored.
- Buffer's documented example request includes `prompt=consent`, which governs the refresh-token
  grant. It was missing and has been added, with a test.
- Not yet done: Connect TikTok, and media hosting.

## 3. Posting capabilities, and what is not supported

- **Publish now:** `mode: shareNow`.
- **Schedule:** `mode: customScheduled` + `dueAt` (at least 2 minutes ahead).
- Every post uses `schedulingType: automatic`, `needsApproval: false`, one video asset and
  `metadata.tiktok.isAiGenerated`. You must choose the AI disclosure explicitly; it is never
  guessed.
- If Buffer answers with `schedulingType: notification`, the post shows **Manual completion
  required**, never "sent automatically".
- Visibility: Buffer posts publicly by default and **privacy cannot be chosen through the API**. The
  UI says so, and offers no fake privacy, comments, Duet/Stitch or commercial-disclosure controls.
- Video and caption limits are checked before anything is uploaded.
- Content needing TikTok music or trending sounds cannot be detected from the file. Buffer reports
  it as an error, which the app shows as **Failed**; it is never downgraded to notification mode.

## 4. Media hosting

**Choice: Cloudinary, using your own account.**
- Why: Buffer recommends it. The Free plan needs no card and has hard caps, so there are no
  surprise charges. 25 credits per month; free-plan uploads are limited to 100 MB per video, which
  fits Shorts.
- Cloudflare R2 was the alternative: 10 GB free and free egress, but it needs a payment method, the
  `r2.dev` URLs are rate-limited and "not for production", and a custom domain plus S3 SigV4 would
  be needed. The hosting module is small, so R2 can be added later if needed.

**Upload lifecycle:**
1. Hash the source (SHA-256).
2. Reserve the post record with a random object ID (`shortspublisher/<192-bit token>`, no
   filename).
3. Stream the original read-only, hashing exactly the bytes sent.
4. If the hash, the size or the source signature (before/after) differs, the copy is **deleted and
   not used**.
5. Check the delivery URL anonymously, as Buffer will: a HEAD request expecting 200, `video/*` and
   the right length.
6. Only then is Buffer contacted.

Originals are never modified. **Exposure:** the hosted copy is public by link until deleted. The
UI states this and Settings requires an explicit checkbox; nothing is uploaded until you publish
or an approved automation enrolls a video.

**Retention:** the default deletes the copy **24 hours after Buffer reports `sent`**. It is never
deleted on mere acceptance, nor for failed posts. Cancelled posts delete their copy. "Keep" never
deletes. **Check Account and Usage** calls Cloudinary's usage API, showing credits used without
uploading. Nothing is uploaded during tests.

## 5. Manual publishing, automation and watched folders

**Videos → TikTok tab:**
- Caption with a live 2,200-character / 5-hashtag check, the AI disclosure, and the stated
  limitations.
- **Publish to TikTok Now…**, **Schedule on TikTok…** and Save.
- The confirmation names the account, the PUBLIC visibility and the hosting exposure; Cancel is
  the default.
- Per-video history shows Cancel Scheduled Post…, Allow Another Attempt… (after an explicit
  warning) and Buffer's `externalLink` when reported.

**Automatic Posting → TikTok automations:**
- **New TikTok Automation…** has three steps: Folder and account, Caption, Schedule and limits.
  Then **Review and Enable…** leads to a consent dialog with two unchecked boxes: exact scope, and
  public hosting.
- The scope includes folder, account, caption template (`{filename}` only), AI disclosure, every
  N hours within a daily window and time zone, weekdays, a 1–10 daily cap, a filename pattern and
  background permission.
- Existing files are excluded at approval. A later file reusing an excluded name is also never
  posted automatically.
- YouTube consent is never inherited. For both platforms, create one automation of each on the same
  folder; each keeps independent status, results and duplicate rules.

**Scheduling design (authoritative):**
- ShortsPublisher picks the video and the exact time using the existing cadence `Rule`. Buffer runs
  it via `customScheduled` + `dueAt`. Buffer's queue (`addToQueue`) is never used.
- Each automation has **at most one unsent Buffer post**. The next video is submitted only after
  Buffer reports the previous one sent (or it ends), at the next free slot after
  `max(now + 5 min, last scheduled time)`, within the daily cap.
- Missed slots while the Mac was off are skipped, never burst. A post whose time passes before
  Buffer accepts it becomes **Missed — not sent**.
- Upside: a submitted post goes out even if the Mac sleeps. Pausing affects only one pending post.

**Pause semantics:**
- The global switch (sidebar "Pause All") and pausing an automation stop *new* submissions.
- A post already scheduled in Buffer stays scheduled until cancelled. Cancelling deletes it from
  Buffer only while its status is `scheduled`.
- The UI says this wherever pause or revoke is offered.

**Watched folders:** TikTok reconciliation runs after each folder scan, after YouTube's, in the
desktop app and in the background worker. In the background it needs both the automation's and
the folder's background permission. A Buffer or Cloudinary outage never stops the local scanner.

## 6. Duplicate rules and recovery

- **Key:** source SHA-256 + TikTok account (Buffer channel ID), enforced by a partial unique index
  (`WHERE released = 0`).
- Pending, scheduled, sent, failed, uncertain and needs-attention records all block a new attempt.
  Only **Cancel** (before publishing) or an explicit **Allow Another Attempt…** releases one.
- Renamed or copied files with the same bytes are blocked. YouTube and TikTok are independent, so
  the same video may go to each once.
- **Uncertain `createPost`** (timeout or 5xx) → "Needs reconciliation":
  - the app searches the channel's recent Buffer posts for the post's unique hosted URL;
  - exactly one match is adopted, and the request is **never resent automatically**;
  - no match keeps it pending until you check Buffer.
- A crash between the durable "Sending to Buffer" mark and the response is handled the same way.
- 429: wait for `Retry-After` (saved app-wide), then resubmit once; the request was refused, not
  processed.
- Status checks run 90 s after the due time, then back off (2, 5, 15, 60 minutes, then hourly).
  After 48 h without a result the post shows **Needs attention**.

## 7. Database (schema 12, additive)

`tiktok_schema.py` adds `tiktok_drafts`, `media_hosting`, `tiktok_posts` (+ unique index),
`tiktok_profiles`, `tiktok_decisions` and `buffer_control`. The Buffer client config and connection
reuse the platform-keyed `account_config` / `account_connection` rows with `platform='tiktok'`. No
YouTube table changed, and the migration creates no connection, hosting or consent.

**The live StonkYa library (schema 11) will migrate to 12 the next time you open it.** Back it up
first. The launcher does this automatically.

## 8. UI changes

- **Accounts:** a TikTok card under YouTube, with Buffer app client setup and account details in
  disclosures.
- **Settings:** a "TikTok media hosting" card.
- **Videos:** a "TikTok" editor tab.
- **Automatic Posting:** the YouTube button is now "New YouTube Automation…", plus a "TikTok
  automations" section.
- **Publishing Queue:** a "TikTok posts (via Buffer)" section. Each row shows caption, TikTok,
  account, state and time; Calendar markers for TikTok are not added yet.
- **Fixes found in native screenshots:** the button rows and checkboxes inside cards drew a dark
  band. They are now transparent.

**UI parity fixes after the first pass (native screenshots):**
- **Checkbox squares had become invisible.** A side effect of the first card-background fix: making
  checkboxes transparent hid their indicator across the app. Checkboxes inside cards now use the
  card colour, and the indicators are visible again.
- **Settings:** TikTok Media Hosting is now a plain section with a Title Case heading, like
  Background Publishing, instead of a card.
- **Automatic Posting:** a "YouTube Automations" heading mirrors "TikTok Automations", and both
  "New … Automation…" buttons are primary. The TikTok setup can now choose and authorize a new
  watched folder in place, like the YouTube setup.
- **Natural-width buttons:** buttons inside form rows ("Choose and Authorize a New Folder…" in both
  editors) keep their natural width.
- **Empty states:** the empty YouTube automation list is hidden until an automation exists, like
  TikTok's.
- **Dashboard:** Publishing status gains a TikTok line, and the sidebar footer reads "Local library ·
  YouTube · TikTok".

**Hosting setup fixes (2026-10-20, after your first try):**
- TikTok had connected live as @mrcenterr via Buffer, but no hosting was saved, so publishing stayed
  unavailable.
- Cloudinary input is now normalized: a pasted `cloudinary://KEY:SECRET@CLOUD` string fills all
  three fields, whitespace is trimmed (the secret was not trimmed before), and wrong-field values
  get specific messages.
- **Save verifies with Cloudinary** (usage call, no upload) before storing anything.
- **Check Account** works on unsaved values.
- Errors from your own clicks open a dialog as well as the inline text. A typed secret is kept when
  saving fails, so you can correct it.
- TikTok errors are now written to the application log (messages only, never secrets).
- Tests: 792 pass.

## 9. Verification

| Check | Result |
|---|---|
| Full suite | **786 tests OK** in 274.2 s, 0 failures, 0 skips (727 baseline + 59 new: 49 in `test_tiktok.py`, 10 in `test_tiktok_ui.py`) — `suite.log` |
| Ruff / format / mypy | pass / 144 files formatted / no issues in 52 source files |
| New tests | `test_tiktok.py` (OAuth/PKCE, relay page, callback listener, connection, rotation/concurrency, hosting integrity, publishing modes, duplicates, reconciliation, rate limits, notification mode, cancel, retention, automation, background) and `test_tiktok_ui.py` (real window) |
| Native TikTok audit (Cocoa) | **PASS** — 59 screenshots; actual window sizes 760×560, 1180×780, 1600×921; max horizontal overflow 0; one Buffer create (`shareNow`, `automatic`); duplicate blocked: True; consent initially disabled: True; source unchanged: True; final state “Sent by Buffer” — `native-tiktok/` |
| Existing native regressions | **28/28 commands exit 0** (`native-regressions.log`) |
| Region audit | en_US and zh_CN page/dialog audits exit 0 |

The native screenshots cover Accounts (not connected, connected), the connect dialog, Settings
hosting (empty, ready), the Videos TikTok tab (ready, publishing, sent), all three automation
setup steps, the consent dialog, Automatic Posting (paused app-wide) and the Queue. Each was taken
at 760×560, 1180×780 and 1600×921 (the display's limit for the requested 1600×1000), with zero
horizontal overflow. Evidence is in `artifacts/stage19-evidence/`.

**Real data:**
- No ShortsPublisher process or LaunchAgent was running before or after; no production service
  was installed.
- No real Buffer, Cloudinary, Google or TikTok request was made, and no real credential was read.
- Hashes of the protected folders match the post-cleanup baseline, except the live database, its
  log and one new launcher backup (`library-before-launch-20261017T235359710344Z.sqlite3`). Those
  came from **your** session: opened Oct 17 at 18:54 and active until 19:01. Files were last
  touched Oct 20 at 07:54, with no normal-quit line in the log.
- The live library was still schema 11 at the end of the first pass. You then opened it with the launcher on 2026-10-20 at 13:17 (backup `library-before-launch-20261020T181748221482Z.sqlite3`), which migrated it to **schema 12**. A copy passes `integrity_check`, and YouTube's connection and three upload records are intact.
- All three genuine source videos and the prepared Stage 17 source are unchanged.

## 10. Live testing status — PASSED (2026-10-20, authorized by you)

You chose a generated clip and asked me to run it. One Publish Now was made through the app's own
code against your live workspace, with your window open and coordinating through the app's locks.

| Step | Result |
|---|---|
| Source | New 6 s 540×960 H.264/AAC 30 fps clip `ShortsPublisher_TikTok_test_20261021T011602Z.mp4`, SHA-256 `8f44b539…34e1`, never published before; unchanged afterwards |
| Backup | `artifacts/stage19-live/library-before-live-tiktok.sqlite3` (integrity ok, schema 12) |
| Hosting | Uploaded to your Cloudinary (`rjgyiytv`); anonymous HEAD returned 200 `video/mp4`, 1,347,628 bytes (exact size) |
| Buffer | `createPost` accepted: post `6ad8126daff466fe09f630ac`, **`schedulingType: automatic`**, `shareNow`, status `sending` at 01:16:28 UTC |
| Result | Buffer reported **`sent` at 01:18:11 UTC** (about 1 min 43 s), `externalLink` <https://tiktok.com/@mrcenterr/video/7698923752935017741> |
| Independent check | TikTok's public oEmbed endpoint returned 200 with an embeddable player for video 7698923752935017741. Author, title and thumbnail fields were not yet populated (consistent with processing). Visual confirmation on the account is yours to do. |
| Afterwards | No automation created or enabled; global publishing left paused. The hosted copy will be deleted automatically 24 h after `sent` the next time the app runs. The test clip remains in your library as video 4. |

This confirms the live schema shapes (`createPost`, `post`) and that this TikTok account allows
**automatic** publishing through Buffer. The Business-account question is answered in practice for
@mrcenterr. Evidence: `artifacts/stage19-live/` (`publish-result.json`, `status-log.json`,
`tiktok-oembed*.json`, `live-result.json`).

## 11. Steps you need to complete

1. Create or sign in to Buffer. Connect TikTok at <https://account.buffer.com/channels> (convert to
   a TikTok Business account if Buffer asks).
2. Host `docs/buffer-oauth-relay.html` at an HTTPS address you control, for example GitHub Pages
   (`https://<you>.github.io/shortspublisher/buffer-callback.html`).
3. In Buffer → Settings → API → App Clients, create a **public** client with permissions
   `account:read`, `posts:read`, `posts:write`, and register the exact relay URL as its redirect URI.
4. In ShortsPublisher → Accounts → TikTok → "Buffer app client setup", enter the Client ID and
   redirect URI and save. Then **Connect TikTok via Buffer…**.
5. Create a free Cloudinary account and copy the cloud name, API key and API secret. Enter them in
   Settings → TikTok media hosting, tick the consent box and save. Use **Check Account and Usage**
   to verify; it uploads nothing.
6. Tell me when you want the single live test, and which disposable video to use.

## 12. Remaining limitations

- Buffer schema shapes and the Business-account requirement are unverified live; the relay page
  must be hosted by you.
- TikTok privacy, interactions and commercial disclosure are not controllable through Buffer.
  Music or trending-sound content cannot post automatically.
- Public visibility on TikTok is inferred from Buffer's `sent` and `externalLink`. There is no
  independent TikTok API check.
- Cloudinary Free: 100 MB per video and 25 credits per month.
- Buffer Free: 3 channels, 10 scheduled posts per channel, and the API limits above.
- Calendar does not mark TikTok posts. Queue and the Videos tab show them.
- A TikTok post already scheduled in Buffer is not stopped by Pause All; cancel it in Queue or
  Videos.
- New automation submissions need the app or the background worker to run at some point between
  posts. Each submitted post is then executed by Buffer, even while the Mac sleeps.
- No packaging, no other platforms.

**Stop after Stage 19.**
