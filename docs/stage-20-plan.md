# Stage 20 plan — Instagram and Facebook through Buffer (2026-10-22)

## Decision

Post Instagram Reels and Facebook Page Reels **through Buffer**, reusing the live-verified TikTok
path: `local MP4 → Cloudinary public copy → Buffer createPost (automatic) → platform → status`.

| Option | Verdict |
|---|---|
| **Buffer (chosen)** | Same OAuth grant, rotating-token authority, Cloudinary hosting, duplicate registry, reconciliation, automation and status code that already posted live to TikTok. Buffer's `createPost` takes `metadata.instagram` (`type: reel`, `shouldShareToFeed`, `isAiGenerated`) and `metadata.facebook` (`type: reel`). Buffer Free allows 3 channels: TikTok + Instagram + Facebook fits exactly. |
| Meta Graph API directly | Rejected for now: a new Meta developer app, Facebook Login with an app secret a desktop app cannot keep, 60-day user tokens to renew, Page tokens, app roles/review, a second resumable-upload protocol and new reconciliation rules. Its one advantage (no public hosted copy) does not outweigh a second, unverified publishing stack. Can be revisited as its own stage. |

## Research (official sources, read 2026-10-22)

| Question | Finding | Source |
|---|---|---|
| Services in Buffer's API | `Service` includes `instagram`, `facebook`; `ChannelType` = account, business, channel, group, page, profile | developers.buffer.com/types/Service.md, ChannelType.md |
| Instagram metadata | `InstagramPostMetadataInput`: `type: PostType!` (post/story/reel), `shouldShareToFeed: Boolean!`, `isAiGenerated`, `firstComment`, `link`, `geolocation`, `stickerFields` | InstagramPostMetadataInput.md |
| Facebook metadata | `FacebookPostMetadataInput`: `type: PostTypeFacebook!` (post/reel/story), `firstComment`, `linkAttachment`, `annotations`; no AI flag | FacebookPostMetadataInput.md, PostTypeFacebook.md |
| Instagram automatic publishing | Only Business or Creator accounts; personal accounts are notification-only. Reels 3 s–15 min, aspect between 4:5 and 9:16, ≤ 1920 px wide, ≤ 300 MB; ≤ 5 hashtags in the caption; Share to Feed can't be changed after publishing; tags/topics/product tags/effects/links force notification | support.buffer.com "Scheduling Instagram posts, reels, stories, and notifications" |
| Facebook | Pages only (Buffer can't connect profiles; Groups are notification-only). Reels 3–90 s, 9:16, MP4, caption ≤ 5,000; no first comment on Reels; no locations; 35 posts / 24 h in Buffer | support.buffer.com "Using Facebook with Buffer" |
| Meta Reels spec | 9:16, min 540×960, 24–60 fps, 3–90 s, MP4; Pages only; 30 API posts / 24 h; Public is the only privacy | developers.facebook.com/docs/video-api/guides/reels-publishing |
| "Sent" | For notification posts "Sent" only means a phone reminder; the app already maps `schedulingType ≠ automatic` to **Manual completion required** | Buffer Instagram article |

Fixed policies (stated in the UI, not guessed per video): Instagram posts are Reels with
`shouldShareToFeed: true` (they also appear on the profile grid); Facebook posts are Page Reels.
The AI label is an explicit per-video/per-automation choice on TikTok and Instagram; Facebook's API
has none, so nothing is sent. Visibility is public on both; no fake privacy controls.

## Shared Buffer resources (one owner each)

- **Buffer app client:** the existing `account_config` row (`platform='tiktok'`) is the Buffer
  client for every Buffer platform. No data is moved.
- **Keychain:** `ShortsPublisher.TikTok` keeps holding all Buffer/Cloudinary secrets (name must not
  change). Instagram and Facebook connections may **reuse the same Buffer grant** (same Keychain
  key). A key is deleted only when no connection references it; *Reconnect required* is applied to
  every connection on that key (a possibly-spent refresh token is never used by another platform);
  a fresh sign-in re-points other connections whose channels it can see.
- **Hosting, rate-limit backoff, token lock:** the existing single `media_hosting`, `buffer_control`
  and `.buffer.lock` are shared. Each platform has its own operation lock and its own tables.

## Parts (each ends green: focused tests, ruff, format, mypy)

1. **Generalize the service, no behavior change.** Move the TikTok service into a
   platform-parameterized `social.py` (`Platform` spec + `BufferPostService`); `tiktok.py` keeps
   `TikTokService` and every name tests import. `BufferAPI.channels(service)`; platform-neutral
   Buffer error wording. All existing TikTok tests pass unchanged.
2. **Schema 13 + Instagram/Facebook services.** `social_schema.py` adds `instagram_*` and
   `facebook_*` (drafts, posts + unique index, profiles, decisions); no existing table changes and
   nothing is enabled. Specs: limits, caption rules, payload metadata, consent text. Shared-grant
   connect (reuse, ref-counted delete, status propagation, re-point). Migration test (v12 → v13
   keeps TikTok rows) and service tests for both platforms.
3. **Watched folders and background.** `WatchService` reconciles every Buffer platform after
   YouTube, each isolated so one outage never blocks another or the scanner. Tests.
4. **UI controller + Accounts/Settings/Queue/Auto Upload page.** Parameterize `TikTokController`
   into a per-platform controller; the TikTok instance keeps owning the Buffer-wide Settings
   (client, view-count key, hosting) with neutral wording. Real Instagram/Facebook cards replace
   "Coming soon" (Snapchat stays). Queue and automation cards per platform. Window/app wiring.
5. **Video editor.** Upload-to buttons and pages 6 (Instagram) and 7 (Facebook) — pages 0–5 keep
   their indices; "Fits on" pills; Details boxes ("Only used on Instagram/Facebook"); library logos
   and the Uploaded filter cover every platform.
6. **Auto Upload, Posting times, Dashboard.** Instagram/Facebook targets with their own approvals
   in sequence; Posting-times batch scheduling per platform; activity timeline, platform cards and
   view counts for every Buffer platform.
7. **Verification and docs.** Full suite, ruff, format, mypy; native harness
   `tests/native_social.py` (Cocoa screenshots at 760×560, 1180×780, 1600×921, zero horizontal
   overflow) plus `native_tiktok.py` regression; update CLAUDE.md, current-status, architecture,
   development, README; write `stage-20-verification.md`.
8. **Live (needs the user).** In Buffer, connect an Instagram **Business/Creator** account and a
   **Facebook Page**. Opening the live app migrates it to schema 13 (the launcher backs it up first).
   Then one separately authorized live post per platform.

## Safety (unchanged rules apply)

No real Buffer, Cloudinary, Instagram or Facebook request, no live library migration and no
LaunchAgent change during development. Controlled fakes only; no real credential is read. Never
resend an uncertain `createPost`; duplicate key = source SHA-256 + Buffer channel, per platform.
Don't stop the user's running app.

**Status:** parts 1–7 complete (850 tests, native audits pass); see
[stage-20-verification.md](stage-20-verification.md). Part 8 awaits the user's Buffer setup and
authorization.
