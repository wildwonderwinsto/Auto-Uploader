# Stage 15 milestones

1. Add transactional schema 10 and a bounded deterministic cadence calculator. Verify daily/hourly,
   selected weekdays, inclusive same-day windows, IANA zones, skipped DST gaps and first fold only.
2. Build immutable batch approval and persistent ordered queue with content/channel conflicts,
   explicit append approval, pause/resume/remove/reorder, bounded missed-slot evidence and unique slots.
   Verify source/metadata revalidation, migration rollback, concurrency and no automatic enrollment.
3. Materialize one approved queue item as an ordinary scheduled job inside the existing claim
   transaction. Reuse existing execution consent, background consent, ownership, budget, locks,
   uploader, fresh hash/account checks and independent Public verification. Check plan binding again
   at initiation. Preserve uncertain attempts and skip empty, paused, missed or blocked opportunities.
4. Add a recurring editor, batch confirmation, Queue/Calendar/Dashboard projections and controls;
   verify cancellation, keyboard/layouts, actual native foreground and isolated LaunchAgent execution.
5. Run the expanded full suite, lint/format/types and prior native regressions; compare read-only
   real-data/reference/source snapshots and remove test services. Publish actual results and limits.

Rule changes/channel changes use a new explicitly approved plan; existing rules are immutable.
Additional videos require a separate explicit batch approval. No unknown future files are approved.
No real YouTube test, real OAuth, production service installation, Stage 16 or packaging is authorized.

All five milestones completed. Final suite: 641 tests, no failures/skips; all 16 prior native
commands and new recurring foreground/background checks passed. See stage-15-verification.md
for exact evidence, live-unverified status and the separate active-app ownership-row exception.
