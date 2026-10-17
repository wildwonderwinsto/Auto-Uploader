# Stage 16 implementation plan

1. Add schema 11 profiles, revision/consent events and durable per-source decisions. No migration
   enables a profile. Deterministic filename templates use existing metadata validation.
2. Reconcile existing watched-file records into proposed metadata and a bounded recurring queue.
   Future-only scope excludes existing files at authorization. Human review and standing approval
   remain separate. Recheck fingerprint, root, revision, metadata and duplicates at atomic enrollment.
3. Extend existing snapshot validation with explicit profile provenance only for profile-linked
   executions. Check revocation at claim/initiation; preserve original manual and finite-batch rules.
4. Add Automatic Posting setup/overview/edit/revoke controls, exact unchecked consent, actual-file
   title preview and integrated queue/history links. Test default review mode and automatic mode.
5. Run deterministic/full regression, native foreground/background and preservation comparisons.
   Report native picker limitations and live-unverified status honestly. Stop after Stage 16.

No live migration, OAuth, YouTube upload, production service or Stage 17 is authorized. A separate
live application may be running; do not stop it or restore its ownership row. Execution access was initially blocked by the account usage limit and later restored on direct retry.
All five implementation/controlled-verification steps are complete; see stage-16-verification.md
for 699 passing tests, native/background evidence, cleanup and the remaining OS picker limitation.
