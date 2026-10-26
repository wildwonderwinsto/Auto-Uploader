# Current status — 2026-10-24 (after Stage 20, editable auto uploads and queues)

## Works today

| Capability | Verification |
| --- | --- |
| Import, FFprobe inspection, library search/sort/filter, local preview | Automated + native |
| Metadata drafts, declarations, revision-bound review | Automated + native |
| Google OAuth, channel verification, Keychain storage | Live (the owner's channel) + controlled |
| Manual Private / Public resumable upload with recovery | **Live**: `<youtube-video-1>`, `<youtube-video-2>` |
| Individually scheduled automatic Public upload (app-owned timing) | **Live**: `<youtube-video-3>` |
| Optional background LaunchAgent publishing | Controlled, real isolated LaunchAgent; no live upload |
| Watched folders, completed-file detection, SHA-256 deduplication | Automated + native; real picker by user |
| Recurring Public queues (intervals, weekdays, windows, DST) | Controlled foreground + background |
| Automatic folder → profile → recurring queue publishing | Controlled foreground + background |
| English interface on any macOS Region, Region's 12/24-hour clock kept | Automated + native (Stage 18) |
| **UI redesign (ShortsScheduler)**: icon sidebar, uploader Dashboard (upcoming/recent/needs-you, YouTube view counts), Accounts cards with logos, 3-step video editor with shared details and first-frame preview, Schedule + Calendar agenda for all platforms, one Auto Upload for YouTube + TikTok, organized Settings | Automated + offscreen screenshots; not yet reviewed natively by the user; view counts not yet live-verified |
| **TikTok via Buffer**: connect (PKCE + relay page), Cloudinary hosting, Publish Now / Schedule, status, duplicates, reconciliation, watched-folder automation | **Live**: one automatic Public post to @owner_tiktok via Buffer (video <tiktok-video-id>), plus controlled tests |
| **One upload for every platform**: the Upload step ticks every ready platform (each says why not, otherwise), one Now/Schedule and one button, one confirmation for the Buffer posts plus YouTube's own; the video must be marked ready for all; the queue window (Choose Videos) is now the same step-by-step window as a new auto upload (Videos · Where · When) with every ready platform ticked; the YouTube schedule window, platform rows and empty cards follow the same style | Automated + offscreen screenshots |
| **Clearer scheduling pages**: Calendar has a legend (highlighted days = any platform), jumps from an empty day to the next upload day, and shows the YouTube management card only on days with YouTube uploads; Auto Upload has two plain sections — **Watched folders** ("Watch a Folder…" picks the folder, details, times and platforms; folder import status sits below) and **Individual videos** ("Upload One Video…" picks a library video and opens it on its Upload step; "Choose Videos…" builds a queue: queue name at the top, no channel dropdown, tick-box video list with a chosen count, Tick All Ready / Clear, Add Videos From Your Mac…, and a preview of the first video); the sidebar switch shows On/Off and turns amber with "N … won't upload until you turn this on" while anything is held back, and the same warning bar appears on Dashboard, Schedule, Calendar and Auto Upload; regular refreshes no longer disable buttons or rebuild unchanged lists (no flicker), and clicks made during a refresh run right after it | Automated + offscreen screenshots |
| **Backup Buffer and Cloudinary accounts** (Settings, schema 15): when a Cloudinary account is over its plan's limits the next one takes the video automatically (skipped for 24 h, or until the automatic usage check in Settings finds it under its limits); more Buffer accounts (separate sign-ups with the same social accounts connected, each signed in from Settings): when one Buffer account hits its API limit (429) or refuses a post over a plan limit, the post goes through the next one automatically, to the same account only; the same Buffer account can't be added twice; posts keep their copy's account and their sign-in | Automated with controlled Buffer/Cloudinary; **not yet used live**; the providers' exact over-limit responses are assumed (Cloudinary HTTP 420/429 or a quota/usage-limit message; Buffer 429 or a refusal mentioning a limit/quota/upgrade) |
| **Editable auto uploads and queues** (2026-10-24, schema 17): Auto Upload shows Individual videos first (one "Add Videos…" button: one video, or several in a queue) and Watched folders second; each auto upload and each queue is one card for all its platforms with Edit…, Pause/Resume and Delete… (Delete stops every platform and, when nothing else uses it, the folder; uploaded videos are never touched); edits apply to every video not uploaded yet (YouTube re-enrolls waiting videos with the new details; Buffer sends the next video with the new settings); queues send TikTok/Instagram/Facebook videos to Buffer one at a time, after the previous one posts; queues can change videos, order, times, platforms and name | Automated + offscreen screenshots; not yet used live |
| **Upload fixes** (2026-10-24): a YouTube upload still processing is checked again automatically (every minute, then every 15 minutes, for two days) and no longer blocks other uploads; calendar/dashboard rows line up and show one row per video (the YouTube title comes from its details, not the file name); YouTube "Fits" is decided by the app from length and shape; the Upload step hides its When/Upload controls once nothing is left to upload, and done platforms say Uploaded/Posted/Scheduled; Schedule lists every YouTube upload (now or scheduled) in a "YouTube uploads" card with Open on YouTube; the TikTok, Instagram and Facebook cards match it (Open on <platform> for posted videos, "Posted" instead of "Sent by Buffer", Cancel Post only while a post can still be cancelled) | Automated |
| **Schedule, Details and Settings fixes** (2026-10-25): Schedule shows one "YouTube uploads" card (scheduled and done, like the other platforms; the Calendar's extra YouTube card is gone); scheduling YouTube from the Upload step needs one approval (the plan and its automatic upload together; no separate "Approve Automatic Upload", and clicks made during a background refresh wait instead of being dropped); a YouTube-scheduled video can't be uploaded to YouTube again; "One video → Add Videos From Your Mac…" imports even while the Videos page is busy; Now / Schedule / Upload are even; Details has **Reuse Details…** and **Save as Upload Defaults** (description, tags, YouTube settings, TikTok/Instagram AI labels; never the title; kept in `upload-defaults.json`, no schema change); Dashboard platform cards share four columns (Uploaded · Scheduled · Views · Followers/Subscribers); Settings no longer flashes (the 5-second service check and the 5-minute YouTube re-check are quiet) its **Background uploading** card is plain On/Off (one line on what it's doing, **Turn On…** sets it up and starts it, **Restart** when it stopped or after an update, **Turn Off…**; details in the status tooltip), and it has **Upload defaults** and **Limits & usage** (YouTube per UTC day, Buffer per 24 h and scheduled-at-once, Buffer waits, Cloudinary credits checked automatically, read-only, when Settings opens and every 30 minutes while it is open; the old Check Usage buttons are gone) | Automated + offscreen screenshots; not yet used live |
| **Instagram Reels and Facebook Page Reels via Buffer** (Stage 20): one shared Buffer sign-in, per-platform limits ("Fits on"), Post Now / Schedule (editor pages 6–7), status, duplicates, reconciliation, Auto Upload (folder uploads and upload queues) with separate approvals, Dashboard cards and views | Automated + native Cocoa harness with controlled Buffer/Cloudinary; **not yet used live** |

Tests: 942 automated, plus 28 native macOS harness commands, `native_tiktok.py` and `native_social.py`. See
`docs/stage-19-buffer-tiktok-verification.md` and `docs/stage-20-verification.md`.

## Pending / not verified

- **Fresh start (2026-10-22):** at the owner's request every earlier workspace, backup and
  Keychain sign-in was deleted; the app now opens as for a brand-new user (default data folder,
  nothing connected). Posts already on YouTube/TikTok are unaffected.
- **Distribution:** source on GitHub; `./install.sh` builds `ShortsScheduler.app` (a launcher for
  the checkout, with the app icon). Not signed or notarized, so macOS asks to confirm the first
  open (right-click → Open).
- **Stage 17 optional live test:** a real folder-to-YouTube background upload, still never run.
- No real background-service or recurring/automatic-folder upload has reached YouTube.
- **Instagram/Facebook live use** needs: in Buffer, an Instagram **Business or Creator** account and
  a **Facebook Page** connected (Buffer Free allows 3 channels: TikTok + Instagram + Facebook); then
  Accounts → Connect (one window connects every Buffer account); then one authorized live post per platform. The
  Buffer `type` channel field and the Instagram/Facebook payloads follow Buffer's published schema
  and have not been exercised live.

## Known limitations

- The Mac must be logged in and online; the service doesn't wake a sleeping Mac or run before
  login. If the switched-on service stops (it refuses a library an update made newer than
  its running code, then needs **Settings → Restart…**), the open app uploads on time instead and
  the sidebar says so; with the app closed, nothing uploads until the service is restarted. While auto uploading is on and an upload is planned (activated schedule, queued video,
  active watched folder or an upload running), the app and the background service each hold
  `caffeinate -dis` (`awake.py`), keeping the Mac and display awake, and the app also takes a
  latency-critical NSProcessInfo activity so App Nap never slows it behind other windows; closing
  the lid still sleeps.
- YouTube's upload API cannot set the 18+ age restriction; such content needs manual handling.
- Daily limits: ShortsScheduler sets none of its own (no setting or field); the schedule decides.
  Only the platforms' own ceilings apply: YouTube 100 uploads a day (its API), TikTok 25, Instagram
  50 and Facebook 35 (what Buffer accepts).
  Buffer Free allows 3,000 API calls per 30 days (≈ 2 per post, so ≈ 45 posts a day in total) and
  10 scheduled posts per channel at a time: Auto Upload keeps one pending post per platform, but a
  Posting-times batch of more than 10 posts to one Buffer platform needs a paid Buffer plan.
- Not certified: network filesystems, hostile filesystem mutation, every codec, very large libraries,
  Google account eligibility/quotas beyond the observed responses, logout/login and power loss.
- Views: YouTube counts via read-only `videos.list`/`channels.list`; TikTok counts only with an
  optional Buffer **personal API key** (Settings; Buffer serves metrics only to personal keys,
  refreshed daily, TikTok coverage not confirmed by Buffer's docs). Neither is live-verified.
  The Dashboard adds views up per video across platforms. Native macOS dialogs (file chooser)
  follow the macOS language.
- Video queues (Auto Upload → Individual videos): YouTube uses the approved recurring plan; TikTok,
  Instagram and Facebook videos wait in the app and go to Buffer one at a time (each after the
  previous one posts), so the app or the background service must be running for the next one to
  be sent. A queued video whose details are edited waits until it is marked ready again.
- The YouTube connection re-verifies itself every five minutes, but never while an upload is
  running or one is due within two minutes (the dispatcher needs the account lock); the postponed
  check leaves the connection shown as it is (it used to flash "Allow Uploads…").
- Two YouTube uploads due at the same moment: the second waits for the first and usually misses
  its 60-second start window. Give scheduled videos times that differ from the auto uploads'
  posting times.
- Snapchat appears as a "Coming soon" card only.
- Posting to Instagram/Facebook directly through Meta (no Buffer, no Cloudinary) was evaluated
  and not adopted: public posts need Meta App Review and Business Verification. See
  `docs/stage-21-meta-decision.md`.
- Instagram: automatic posting needs a Business or Creator account (personal accounts become
  **Manual completion required**); Reels always also appear on the profile grid; tags, collaborators,
  topics, product tags and Instagram music can't be set. Facebook: Pages only, Reels 9:16, 3–90 s,
  ≥ 540×960, MP4; no AI label exists in its API. Both are public; privacy can't be chosen.
- Schedule/detail setup pages still scroll at the smallest window size.
- Not signed or notarized; `ShortsScheduler.app` runs this checkout's `.venv`, so the folder must stay put.
- No exactly-once guarantee across SQLite, Keychain and YouTube; uncertain outcomes are reconciled.
- TikTok: privacy/comments/Duet/Stitch/commercial disclosure aren't settable via Buffer; music or
  trending sounds can't post automatically; Cloudinary Free caps videos at 100 MB; Calendar doesn't
  mark TikTok posts; Pause All doesn't stop a post already scheduled in Buffer (cancel it).

## Next priorities (each needs explicit approval)

1. The user-authorized Stage 17 live folder-to-YouTube acceptance.
2. Stage 20 external setup (Instagram + Facebook in Buffer), then one authorized live post each.
3. Usability polish found during live use.
3. Later: packaging/distribution, then additional platforms — each as its own stage.
