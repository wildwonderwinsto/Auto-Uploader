# Current status — 2026-10-17 (after Stage 18)

## Works today (YouTube only)

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

Tests: 714 automated (Stage 18), plus 28 native macOS harness commands. See the latest
`docs/stage-18-verification.md`.

## Pending / not verified

- **Stage 17 optional live test:** a real folder-to-YouTube background upload. The source and
  proposal are prepared in `artifacts/stage17-prepared/`; it requires the user to authorize the
  live workspace migration (schema 9 → 11), review the source/destination, select declarations,
  approve the standing scope in the app and personally enable background + global publishing.
- The live workspace `artifacts/stage6-live/library.sqlite3` is schema **9**; current code is **11**.
  Launching it migrates it (the launcher takes a backup first). Coordinate with the user.
- No real background-service or recurring/automatic-folder upload has reached YouTube.

## Known limitations

- The Mac must be awake, logged in and online; the service doesn't wake the Mac or run before login.
- YouTube's upload API cannot set the 18+ age restriction; such content needs manual handling.
- Not certified: network filesystems, hostile filesystem mutation, every codec, very large libraries,
  Google account eligibility/quotas beyond the observed responses, logout/login and power loss.
- Analytics is a placeholder page. Native macOS dialogs (file chooser) follow the macOS language.
- Schedule/detail setup pages still scroll at the smallest window size.
- Not packaged, signed or notarized; runs from this checkout and `.venv`.
- No exactly-once guarantee across SQLite, Keychain and YouTube; uncertain outcomes are reconciled.

## Next priorities (each needs explicit approval)

1. The user-authorized Stage 17 live folder-to-YouTube acceptance (includes the live migration).
2. Approved cleanup of historical `artifacts/` (proposal in `docs/stage-18-verification.md`).
3. Usability polish found during live use (e.g. schedule day-control scrolling).
4. Later: packaging/distribution, then additional platforms — each as its own stage.
