# Stage 14 implementation milestones

1. Add schema 9 transactionally: consented folders, durable observations, unique SHA-256 content,
   non-destructive library associations. Verify migration rollback and prior-record preservation.
2. Implement a Qt-independent scanner: nonrecursive approved roots, no symlinks, two observations
   and 15-second quiet period, retained-descriptor hashing, FFprobe, post-inspection identity checks,
   atomic deduplicated library commit, retry/backoff and per-channel history evidence. Verify with
   generated files, deterministic clocks, SQLite and concurrent processes before UI integration.
3. Add native folder-picker confirmation with existing-file count/choice, pause/resume/change/remove,
   activity and diagnostics. Debounce Qt notifications (1 second), reconcile every 10 seconds and
   reattach missing watchers. Never navigate or replace a metadata buffer on discovery. Reuse the
   scanner from a separately permitted local-discovery thread in the authorized existing worker.
4. Run new automated/native cross-process checks, all baseline and existing native regressions,
   visual review at three sizes, source/database/reference preservation, and publish actual results.

No new dependency, publishing authorization, remote request, metadata generation, recurring plan,
packaging or source-file mutation. Default nonrecursive local folders only; symlink entries rejected.
SHA-256 means identical bytes, not perceptual equivalence. Stability remains a heuristic, so recommend
writing a temporary extension then atomically renaming a completed file into the selected directory.
