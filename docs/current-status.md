# Current status — 2026-10-21 (after Stage 19 + UI redesign)

## Works today

| Capability | Verification |
| --- | --- |
| Import, FFprobe inspection, library search/sort/filter, local preview | Automated + native |
| Metadata drafts, declarations, revision-bound review | Automated + native |
| Google OAuth, channel verification, Keychain storage | Live (StonkYa) + controlled |
| Manual Private / Public resumable upload with recovery | **Live**: `bfNDN1Gq540`, `OxgK8zkDmkI` |
| Individually scheduled automatic Public upload (app-owned timing) | **Live**: `vmbsEJ4E9Rc` |
| Optional background LaunchAgent publishing | Controlled, real isolated LaunchAgent; no live upload |
| Watched folders, completed-file detection, SHA-256 deduplication | Automated + native; real picker by user |
| Recurring Public queues (intervals, weekdays, windows, DST) | Controlled foreground + background |
| Automatic folder → profile → recurring queue publishing | Controlled foreground + background |
| English interface on any macOS Region, Region's 12/24-hour clock kept | Automated + native (Stage 18) |
| **UI redesign (ShortsScheduler)**: icon sidebar, uploader Dashboard (upcoming/recent/needs-you, YouTube view counts), Accounts cards with logos, 3-step video editor with shared details and first-frame preview, Schedule + Calendar agenda for all platforms, one Auto Upload for YouTube + TikTok, organized Settings | Automated + offscreen screenshots; not yet reviewed natively by the user; view counts not yet live-verified |
| **TikTok via Buffer**: connect (PKCE + relay page), Cloudinary hosting, Publish Now / Schedule, status, duplicates, reconciliation, watched-folder automation | **Live**: one automatic Public post to @mrcenterr via Buffer (video 7698923752935017741), plus controlled tests |

Tests: 812 automated, plus 28 native macOS harness commands and `native_tiktok.py`. See
`docs/stage-19-buffer-tiktok-verification.md`.

## Pending / not verified

- **Stage 17 optional live test:** a real folder-to-YouTube background upload. The source and
  proposal are prepared in `artifacts/stage17-prepared/`; it requires the user to authorize the
  live workspace use, review the source/destination, select declarations,
  approve the standing scope in the app and personally enable background + global publishing.
- The live workspace `artifacts/stage6-live/library.sqlite3` is now schema **11** (it migrated when the
  user launched it on 2026-10-17 17:41). Its schema-9 pre-launch backup is in `stage6-live/backups/`.
- No real background-service or recurring/automatic-folder upload has reached YouTube.
- **TikTok live use** needs: Buffer account with TikTok connected (possibly a Business account),
  a Cloudinary account and an authorized single live test. Done: Buffer app client saved, relay
  page live at `https://wildwonderwinsto.github.io/autouploaderhtml/buffer-oauth-relay.html`,
  live library at schema 12.

## Known limitations

- The Mac must be awake, logged in and online; the service doesn't wake the Mac or run before login.
- YouTube's upload API cannot set the 18+ age restriction; such content needs manual handling.
- Not certified: network filesystems, hostile filesystem mutation, every codec, very large libraries,
  Google account eligibility/quotas beyond the observed responses, logout/login and power loss.
- Views: YouTube counts via read-only `videos.list`/`channels.list`; TikTok counts only with an
  optional Buffer **personal API key** (Settings; Buffer serves metrics only to personal keys,
  refreshed daily, TikTok coverage not confirmed by Buffer's docs). Neither is live-verified.
  The Dashboard adds views up per video across platforms. Native macOS dialogs (file chooser)
  follow the macOS language.
- Posting times: YouTube uses the approved recurring queue; TikTok picks schedule each selected
  video individually in Buffer at the next posting times (one confirmation lists every post).
- The YouTube connection re-verifies itself every five minutes, but never while an upload is
  running or one is due within two minutes (the dispatcher needs the account lock).
- Instagram, Facebook and Snapchat appear as "Coming soon" cards only.
- Schedule/detail setup pages still scroll at the smallest window size.
- Not packaged, signed or notarized; runs from this checkout and `.venv`.
- No exactly-once guarantee across SQLite, Keychain and YouTube; uncertain outcomes are reconciled.
- TikTok: privacy/comments/Duet/Stitch/commercial disclosure aren't settable via Buffer; music or
  trending sounds can't post automatically; Cloudinary Free caps videos at 100 MB; Calendar doesn't
  mark TikTok posts; Pause All doesn't stop a post already scheduled in Buffer (cancel it).

## Next priorities (each needs explicit approval)

1. The user-authorized Stage 17 live folder-to-YouTube acceptance.
2. Stage 19 external setup, then one authorized live TikTok post via Buffer.
3. Usability polish found during live use.
3. Later: packaging/distribution, then additional platforms — each as its own stage.
