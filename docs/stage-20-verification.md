# Stage 20 — Instagram and Facebook through Buffer — 2026-10-22

**Verdict: GO, for development.** Instagram Reels and Facebook Page Reels post through the same
path TikTok uses live: `local MP4 → Cloudinary public copy → Buffer createPost (automatic) →
platform → status`. Everything is verified against **controlled** Buffer and Cloudinary
endpoints. **No real Buffer, Cloudinary, Instagram or Facebook request was made and nothing was
posted.** The live acceptance posts need your setup (below) and separate authorization.

Plan and research: [stage-20-plan.md](stage-20-plan.md). Why Buffer rather than Meta's Graph API:
it reuses the live-verified sign-in, hosting, duplicate, reconciliation and automation code;
Meta's API would need a second OAuth stack with an app secret, 60-day tokens and app review.

## What was built

| Part | Result |
|---|---|
| 1. Generalized service | `social.py`: `Platform` spec + `BufferPostService`. `tiktok.py` keeps every Stage 19 name; all TikTok tests pass unchanged. |
| 2. Schema 13 + services | `social_schema.py` adds `instagram_*` / `facebook_*` (drafts, posts + unique index, profiles, decisions). No existing table changes; nothing enabled. Instagram: `type: reel`, `shouldShareToFeed: true`, explicit AI label; 3 s–15 min, 9:16–4:5, ≤ 1920 px wide, ≤ 300 MB, MP4/MOV, ≤ 2,200 chars, ≤ 5 hashtags. Facebook: Page Reels (`type: reel`; Groups/profiles excluded), no AI label (its API has none); 3–90 s, 9:16, ≥ 540×960, 24–60 fps, MP4, ≤ 5,000 chars. |
| Shared Buffer sign-in | One Buffer grant serves every platform. Keychain keys are deleted only when no connection uses them; "Reconnect required" is set on every connection sharing a key, so a possibly spent refresh token is never reused; a fresh sign-in re-points other platforms whose channels it can see. YouTube rows are never touched. |
| 3. Folders + background | The scanner reconciles TikTok, Instagram and Facebook after YouTube (desktop and LaunchAgent worker), each isolated: one platform's outage doesn't stop the others or the scanner. |
| 4. UI controller | `ui/social.py`: one `SocialController` per platform (TikTok's owns the Buffer-wide Settings, renamed "Buffer connection", "Buffer view counts", "Video hosting for Buffer"). Real Accounts cards replace "Coming soon" (Snapchat remains). Connect offers **Use My Buffer Sign-In**. Queue and automation cards per platform. |
| 5. Video editor | Upload to: YouTube · TikTok · Instagram · Facebook; pages 6 and 7; "Fits on" pills for all four; Details boxes "Only used on Instagram/Facebook"; library logos and the Uploaded filter cover every platform. |
| 6. Auto Upload, Posting times, Dashboard | Instagram/Facebook targets with their own consent, approved in turn (an approved platform is never approved twice); Posting times schedules each Buffer platform after its own confirmation; Dashboard platform cards, timeline and view counts for all. |

Also fixed:
- **Agenda crash (existing bug).** The Schedule agenda crashed ("year 10000 is out of range") in
  time zones behind UTC whenever a Buffer post had no due time (any Post Now while it publishes).
  The recent-uploads sort had the mirror bug ahead of UTC. A regression test covers both sides.
- **Automation headline.** An automation no longer reads "Active" when video hosting is missing.

## Verification

| Check | Result |
|---|---|
| Baseline before changes | 813 tests OK (362 s) |
| Full suite after | **850 tests OK** (485 s), 0 failures. New: `test_social.py` (23), `test_social_ui.py` (12), 2 activity tests. Eight tests' "current schema" assertions moved 12 → 13; five UI tests also wait for the new controllers at teardown. |
| Ruff / format / mypy | pass / 158 files formatted / no issues in 59 source files |
| Native Instagram/Facebook audit (Cocoa) | **PASS**: `tests/native_social.py`; 80 screenshots at 760×560, 1180×780, 1600×921; max horizontal overflow 0; Instagram `shareNow` automatic reel → "Sent by Buffer"; Facebook `customScheduled` automatic reel → "Scheduled in Buffer"; one shared sign-in; duplicate blocked; 360×640 refused by Facebook only; both consent dialogs start disabled; paused automations never read Active; source unchanged. Evidence: `artifacts/stage20-evidence/native-social/` |
| Native TikTok regression (Cocoa) | **PASS**: `artifacts/stage20-evidence/native-tiktok/` |
| Flakiness checked | `test_accounts_ui` close tests ran 40× without failure after waiting for the Buffer controllers' startup checks |

The full suite is about 35 % slower than the baseline. The new tests account for roughly 40 s;
the rest comes from every UI test window now starting three Buffer controllers.

**Real data:**
- The user's app (`artifacts/stage6-live`, started 09:49) kept running throughout. It was never
  stopped and runs the code it loaded at start.
- Its library is still schema 12. Its only writes are its own folder-scanner timestamps; no
  Instagram/Facebook lock files exist there.
- No LaunchAgent was installed (`launchctl list` and `~/Library/LaunchAgents` show none).
- No real credential was read.

## Steps you need to complete

1. In Buffer (<https://account.buffer.com/channels>) connect an **Instagram Business or Creator**
   account and a **Facebook Page**. Buffer Free allows 3 channels: TikTok + Instagram + Facebook.
2. Quit and restart ShortsScheduler. The launcher backs up the library and it migrates to **14**
   (additive tables only).
3. Accounts → **Connect** on the Instagram or Facebook card. It reuses your Buffer sign-in and one
   window connects both (and TikTok, if needed).
4. Tell me when you want the live tests and which disposable 9:16 video to use: one Instagram and
   one Facebook post, each separately authorized.

## Not verified live / limitations

- Buffer's Instagram/Facebook payloads and the channel `type` field come from Buffer's published
  schema and haven't been exercised live.
- A personal Instagram account is reported as **Manual completion required** (a phone reminder).
- Instagram Reels always also appear on the profile grid.
- Not settable through Buffer's API: tags, collaborators, topics, product tags, Instagram music,
  Facebook locations or first comments on Reels, and privacy on either platform.
- Calendar day markers still cover YouTube only; the agenda lists every platform.

## Follow-up (2026-10-22): daily limits and Settings logos

- **No daily upload limit of the app's own.** The Settings "Daily upload limit" and the Auto
  Upload / Posting-times "Daily limit" fields are gone; the schedule decides how often videos go
  out. Only the platforms' own ceilings apply, without a setting: YouTube 100 uploads a day (its
  API, since its quota change), TikTok 25, Instagram 50, Facebook 35 (what Buffer accepts).
  Approval summaries state the ceiling as the platform's own limit.
- **Schema 14** (`limits_schema.py`) sets the global value to YouTube's 100 and loosens the two `BETWEEN 1 AND 10` CHECK constraints in place
  with SQLite's documented `writable_schema` procedure (a table rebuild fails: `recurring_plans`
  is referenced by four tables). It changes no rows, checks integrity, rolls back on any
  unexpected definition, and skips a table that is already loosened. The live library's table
  definitions were confirmed (read-only) to match.
- **Buffer budget:** the first status check after a due time moved from 90 s to 3 minutes, so one
  check usually suffices (≈ 2 calls per post); 15 posts a day on each of three platforms is
  ≈ 2,700 calls per 30 days, inside Buffer Free's 3,000.
- **Settings:** each setup section shows the logos of the platforms that need it (YouTube
  connection → YouTube; Buffer connection and view counts → TikTok, Instagram, Facebook; video
  hosting already had them).
- Tests: `test_limits.py` (migration from a v13 library with referenced rows, from the oldest
  fixture, refusal on an unexpected definition, platform maximums) and new cases in `test_social`,
  `test_social_ui`; the global-limit, recurring-editor and status-timing tests were updated for the
  intended new behavior. Schema assertions now use `SCHEMA_VERSION`.

## Follow-up (2026-10-22): easier onboarding

- **One sign-in, one window.** Connect on any TikTok, Instagram or Facebook card reuses a working
  Buffer sign-in without asking (no browser). Without one, Buffer's sign-in opens in the browser
  straight away. Either way, one "Choose accounts to connect" window lists every account in that
  Buffer account by platform, pre-selects the platform clicked and any not yet working, leaves out
  Facebook Groups and profiles, links to Buffer when an account is missing, and offers "Use a
  Different Buffer Account". Nothing posts on connect.
- **Setup steps open their pages.** YouTube (YouTube Data API, test users, OAuth clients), Buffer
  (Channels, API settings) and Cloudinary (sign-up, API keys) setup steps each have a button for
  the exact page; the Cloudinary field takes the pasted `cloudinary://…` variable.
- **"Open Setup" scrolls** to the Settings section that step needs.
- **Not built:** automatic fallback across several Cloudinary accounts. Cloudinary's Acceptable
  Use Policy §4.1 forbids "opening multiple accounts to bypass usage restrictions" and §6.4 allows
  suspending affiliated accounts.

**Stop after Stage 20.**
