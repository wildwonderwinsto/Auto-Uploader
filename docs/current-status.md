# Current status — 2026-10-20 (after Stage 19)

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
| **TikTok via Buffer**: connect (PKCE + relay page), Cloudinary hosting, Publish Now / Schedule, status, duplicates, reconciliation, watched-folder automation | Controlled Buffer/Cloudinary + native (Stage 19); **no live request yet** |

Tests: 786 automated, plus 28 native macOS harness commands and `native_tiktok.py`. See
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
  a public Buffer app client, the relay page hosted at your HTTPS redirect URI, a Cloudinary
  account, and an authorized single live test. Opening the live library migrates it to schema 12.

## Known limitations

- The Mac must be awake, logged in and online; the service doesn't wake the Mac or run before login.
- YouTube's upload API cannot set the 18+ age restriction; such content needs manual handling.
- Not certified: network filesystems, hostile filesystem mutation, every codec, very large libraries,
  Google account eligibility/quotas beyond the observed responses, logout/login and power loss.
- Analytics is a placeholder page. Native macOS dialogs (file chooser) follow the macOS language.
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
