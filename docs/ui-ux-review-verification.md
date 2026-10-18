# UI/UX review and fixes — 2026-10-17

The review was done by inspecting real native Cocoa screenshots of every page and dialog, with
isolated SQLite roots, generated media and controlled Google responses. They came from
`tests/native_ui_audit.py` and the 28 native harness commands, at three window sizes and in several
application states. Confirmed problems were fixed in the UI layer only. The uploader, scheduler,
consent model, database and LaunchAgent code are unchanged.

**What this is not:** no person used the app for this review, and the native folder chooser was
not operated (see the manual checklist below). Screenshots were driven by harnesses, so interaction
was scripted clicks and state changes, not free exploration. The live StonkYa workspace was not
opened, no YouTube request was made, and no production service was installed.

## Pages and states inspected

- **Pages:** Dashboard; Videos library (empty, one, many videos; search, filter and sort); Import;
  video editor tabs (File/readiness, Details, Review, Publish, Schedule); local preview; Upload
  History.
- **Folders and posting:** Automatically Import Videos (configured, imported, duplicates, missing,
  invalid, waiting, paused, resumed); Automatic Posting (no profile, enabled, paused, enrolled,
  published, reopened); the four setup steps; Publishing Queue (recurring plan empty, paused,
  approved, first published); Calendar.
- **Account and settings:** Accounts (disconnected, connecting, connected, offline, cancelled, scope
  granted or denied, reopened); Settings/Background Publishing (not installed, started, error).
- **Dialogs:** public activation, Private/Public upload confirmation, folder consent, automation
  consent, recurring batch consent, error presentation.
- **Upload states:** uploading, paused, needs reconciliation, visibility discrepancy, Public
  verified, cancelled.

## Problems found (before)

| # | Severity | Problem | Where |
|---|---|---|---|
| 1 | High | Almost every action button stretched the full content width and stacked vertically. Pages were very tall, buttons looked like input fields, and there was no primary/secondary hierarchy. The Queue alone stacked 10+ bars. | All pages, dialogs |
| 2 | High | `&` in button text became a macOS mnemonic underscore: "Choose _Authorize a Watched Folder…", the consent dialog's "Authorize _Enable Profile", and "Review _Approve Queue…". | Automation setup, automation consent, recurring |
| 3 | High | Automatic Posting showed **Pause and Resume at the same time**. "Enabled" appeared beside "Background service: not checked", so the page never said whether posting could actually happen. | Automatic Posting |
| 4 | Medium | The Automatic Posting status was a 10-line dump: full raw folder path, "Reconciled local eligibility" (internal state), and channel ID as a fallback. | Automatic Posting |
| 5 | Medium | The Automatic Posting title used an unstyled object name (`PageTitle`), so it rendered as body text. Queue and Calendar titles used the smaller Heading style. | Automatic Posting, Queue, Calendar |
| 6 | Medium | Setup steps never said "Step N of 4" or what came next. Back/Next/Cancel were equal-width with no forward emphasis, and a separate full-width primary "Review Exact Automation Scope…" button sat on every step. | New/Edit Automation |
| 7 | Medium | Publish tab: the same "An existing attempt blocks another upload…" sentence appeared twice, and it was misleading after a successful upload. | Publish |
| 8 | Medium | Analytics, a "Not implemented yet" placeholder, was a main navigation destination. | Sidebar |
| 9 | Medium | The sidebar's global switch had no state beside it ("Enable Scheduled Publishing…" or "Pause All Scheduled Publishing" only). | Sidebar |
| 10 | Low | Dashboard: "WORKSPACE / DASHBOARD" breadcrumb (Accounts used "MANAGE / ACCOUNTS"; no other page had one); "Stop Active Scheduled Upload" always visible when nothing was uploading; all-caps PAUSED; a policy sentence shown as "Latest activity". | Dashboard, Accounts |
| 11 | Low | With no recurring queue, the Queue showed an empty dropdown, an empty 180 px list and seven disabled actions. The individual-schedule list said "No plans for this day/filter" on the Queue page, which has no day. | Queue |
| 12 | Low | Raw ISO times in detail panes (background check-in, plan approved/cancelled, execution start). | Settings diagnostics, plan details |
| 13 | Low | The watched-folder panel never explained how it differs from Import Videos. | Videos |

## Fixes

1. **Compact action rows** (`ui/components.py`: `FlowLayout`, `tidy_buttons`). Adjacent buttons in
   a vertical layout become one left-aligned row that wraps on narrow windows. Single buttons keep
   their natural width. Order, visibility, enabled state and keyboard order are unchanged.
   `MainWindow` applies this once after building its pages, and every dialog gets it on first show.
   The Videos header row wraps the same way.
2. **Mnemonics:** labels now use "and" (e.g. "Authorize and Enable Automation"). A test scans all
   UI modules for single `&` in button, checkbox and `setText` strings.
3. **Honest automation status** (`ui/automation.headline`):
   - "Active" appears only when the profile is enabled, its folder is watched and global publishing
     is on.
   - Otherwise the page says *why* it is not posting: paused, needs approval again, paused
     app-wide, or folder unavailable.
   - A separate line says whether it posts only while the app is open, or also while closed,
     using the last known service status.
   - Opening the page triggers a read-only service status check; Settings publishes a
     `status_changed` signal.
   - Pause or Resume is shown, never both. Revoke is shown only while enabled.
4. **Plain-language overview:** folder name (full path in a tooltip), channel name, schedule, queue
   counts, next posting time (weekday, readable), last published title. Internal status moved to
   "Technical details". The long control explanation moved to "How pausing works".
5. **Consistent titles:** Automatic Posting, Queue and Calendar now use the Title style; the
   breadcrumbs were removed.
6. **Setup steps:**
   - A "Step 1 of 4: Folder and channel · Next: Video details" indicator.
   - Steps renamed: Folder and channel, Video details, Schedule, Limits.
   - A standard button bar: Cancel on the left; Back, Next and "Review and Enable…" on the right.
   - Next is the primary action until the last step, where Review and Enable becomes primary. It
     stays available on every step, as Stage 17 required.
   - "Choose and Authorize a New Folder…" sits under the folder picker.
   - The background checkbox reads "Also post while ShortsPublisher is closed".
   - The consent dialog (step 5 in effect) is unchanged: same unchecked confirmations and exact scope.
7. **Publish tab:** the block reason now says "This video has already been uploaded — see Upload
   History below. Duplicate uploads are blocked." or "An earlier upload needs attention…". It is
   shown once. The enable/disable gate is unchanged.
8. **Analytics** placeholder removed from navigation. Ctrl+1–7 map to the remaining pages.
9. **Sidebar** shows "Automatic publishing / Paused — nothing will post" (or "On") above a
   "Turn On…" / "Pause All" button. The existing confirmation is unchanged.
10. **Dashboard:**
    - It opens with a purpose line, then the channel badge.
    - A quick-action row follows: Prepare a video (primary), Automatic Posting, Publishing Queue.
    - "Publishing status" uses full sentences. Stop Active Scheduled Upload appears only while an
      upload is running.
    - "Latest activity" starts as "No automatic uploads have run yet."
11. **Queue empty states:** the recurring picker, list and actions are hidden until a queue exists.
    The empty message explains what a recurring queue is. The individual list hides when empty, and
    its message fits the page.
12. **Readable times** for background check-in, plan approved/cancelled and execution start. The
    exact UTC/local ISO lines in *approval* dialogs were kept on purpose, as safety evidence.
13. **Videos:** "Recurring Posting…" moved into the header row. The watched-folder panel explains
    "Import Videos… adds files you pick once. A watched folder adds new finished videos
    automatically. Watching only imports — it never publishes."

Kept on purpose:
- PUBLIC wording, exact scope, channel IDs and UTC times in final confirmations.
- Unchecked consent boxes, and all permission layers.
- The library/editor navigation from Stage 13, and the unsaved-changes guard.

## Before / after (native Cocoa, 1180×780 unless stated)

| Page | Before | After |
|---|---|---|
| Dashboard | [before](../artifacts/ui-ux-review-evidence/before-dashboard.png) | [after](../artifacts/ui-ux-review-evidence/after-dashboard.png) |
| Videos library | [before](../artifacts/ui-ux-review-evidence/before-library.png) | [after](../artifacts/ui-ux-review-evidence/after-library.png) |
| Review tab | [before](../artifacts/ui-ux-review-evidence/before-review.png) | [after](../artifacts/ui-ux-review-evidence/after-review.png) |
| Publish (already uploaded) | [before](../artifacts/ui-ux-review-evidence/before-publish.png) | [after](../artifacts/ui-ux-review-evidence/after-publish.png) |
| Watched folder (760×560) | [before](../artifacts/ui-ux-review-evidence/before-watching-760.png) | [after](../artifacts/ui-ux-review-evidence/after-watching-760.png) |
| Automatic Posting | [before](../artifacts/ui-ux-review-evidence/before-automation.png) | [after](../artifacts/ui-ux-review-evidence/after-automation.png) |
| Automatic Posting (760×560) | [before](../artifacts/ui-ux-review-evidence/before-automation-760.png) | [after](../artifacts/ui-ux-review-evidence/after-automation-760.png) |
| Setup step 1 | [before](../artifacts/ui-ux-review-evidence/before-setup-1.png) | [after](../artifacts/ui-ux-review-evidence/after-setup-1.png) |
| Setup step 3 (Schedule) | — | [after](../artifacts/ui-ux-review-evidence/after-setup-3-schedule.png) |
| Video editor header (File tab) | [before](../artifacts/ui-ux-review-evidence/before-review.png) | [after](../artifacts/ui-ux-review-evidence/after-editor-file-tab.png) |
| Real `main()` entry, sidebar state | — | [after](../artifacts/ui-ux-review-evidence/after-real-entry-dashboard.png) |
| Publishing Queue | [before](../artifacts/ui-ux-review-evidence/before-queue.png) | [after](../artifacts/ui-ux-review-evidence/after-queue.png) |
| Settings | [before](../artifacts/ui-ux-review-evidence/before-settings.png) | [after](../artifacts/ui-ux-review-evidence/after-settings.png) |
| Accounts | [before](../artifacts/ui-ux-review-evidence/before-accounts.png) | [after](../artifacts/ui-ux-review-evidence/after-accounts.png) |
| Calendar, China Region | — | [after](../artifacts/ui-ux-review-evidence/after-calendar-zh_CN.png) |

## Responsive layout

Every route was captured at requested 760×560, 1180×780 and 1600×1000; the actual sizes were
760×560, 1180×780 and **1600×921** (this display's limit). Each was captured top and bottom.
The automation, watching, library, recurring and upload harnesses add their own states at the
same sizes.

`test_shell.test_resize_render_and_close` asserts no horizontal scrolling on any page at all three
sizes. `test_automation_ui` asserts the same for all four setup steps at 760×560. Wrapping rows
were checked visually at 760 px. One regression was found and fixed during this work: the
four-button Videos header overflowed by 79 px before it was made to wrap.

## English and Regions

The Stage 18 English-interface fix is preserved. `native_ui_audit` ran under the en_US, zh_CN and
en_GB Regions:
- Calendar months and weekdays and all timestamps are English.
- zh_CN and en_GB keep 24-hour times; en_US keeps 12-hour times.
- 雪 fixture data is unchanged.
- The Stage 18 locale tests still pass.

## Test results

| Check | Result |
| --- | --- |
| Full suite | **727 tests OK** in 246.9 s, 0 failures, 0 skips (714 baseline + 13 new) — `artifacts/ui-ux-review-evidence/suite.log` |
| Ruff / format / mypy | pass / 134 files formatted / no issues in 47 source files |
| Native macOS (Cocoa) | **28/28 commands exit 0** on the final code — `native-regressions.log` |
| Native page/dialog audit | en_US, zh_CN, en_GB runs, 76 screenshots each, exit 0 |
| Service cleanup | no ShortsPublisher LaunchAgent loaded or installed; no ShortsPublisher process left |

**A pre-existing intermittent test was found and fixed.** Two full-suite runs during this work
each failed one test in `test_videos_ui.VideosUiTests` (`test_close_during_inspection…`, then
`test_database_failure…`).
- Running the unchanged committed code in a temporary worktree reproduced the close/inspection
  failure in 2 of 10 runs, so it predates this review. The heavier window build made it more frequent.
- Cause: the test's first `window.close()` can be refused while another startup loader is still
  busy. When that loader finishes, the deferred close runs again, after the test has started a new
  Videos load.
- Fix: `setUp` now waits for every startup worker, not only the Videos page. No assertion was
  weakened. The class then passed 6 of 6 runs, and the final full suite passed.

The new `tests/test_ui_review.py` adds 13
tests:
- `headline` never says Active while blocked, and says when posting can happen.
- The service status is always reported.
- No mnemonic ampersands.
- No full-width buttons on any page.
- Analytics is removed.
- The sidebar shows its state; Stop is hidden when idle.
- `tidy_buttons` keeps order and hidden state.
- The setup step indicator and primary emphasis work.

Existing assertions changed only where wording changed on purpose. Each still checks the same
behaviour: "global pause visible" now asserts the sentence plus the sidebar state, and "paused"
asserts the Paused headline, Resume visible and Pause hidden. The upload completion test now also
asserts the clearer "already been uploaded" reason.

## Manual acceptance checklist (not performed by a person this time)

1. Videos → Automatically Import Videos → Watch a Folder…: the macOS chooser opens. Cancel leaves
   no folder; selecting one shows the consent dialog with the exact path.
2. Automatic Posting → New Automation…: "Choose and Authorize a New Folder…" under Watched folder
   opens the chooser; after consent the new folder is selected in the picker.
3. Walk steps 1–4 with Next. Check that the step line updates, Next is blue until step 4, and
   Review and Enable… is blue on step 4.
4. In the consent dialog, confirm the button reads "Authorize and Enable Automation" with no
   underscore.
5. With global publishing paused, the automation headline must read "Not posting · automatic
   publishing is paused for the whole app".
6. At 760×560, check that no page needs horizontal scrolling and every action row wraps.

## Follow-up fixes (second pass)

All five fixable items from the first pass's remaining-issues list were addressed:

| Was | Priority | Fix | Verified by |
|---|---|---|---|
| Library header row (Import / Reload / History / Recurring) repeated above the video editor | Medium | Back to Library moved into the header row. While editing or viewing history, that row shows only **← Back to Library** and **Import Videos…** (Import stays available, per Stage 13). Reload, Upload History and Recurring Posting appear only on the library landing. | `test_editor_header_keeps_only_back_and_import`; Stage 13 workflow tests unchanged |
| "Passes implemented checks" and the local-check disclaimer repeated on the File and Review tabs | Medium | Badge now reads **"Ready for YouTube Shorts · local checks passed"**; the disclaimer is a tooltip on the badge and stays visible once on the Review tab. The stored readiness status is unchanged. | `test_readiness_uses_plain_wording…`; native library smoke assertion updated |
| Schedule and Details setup steps scrolled (Stage 17 note) | Medium | Weekday checkboxes are now one wrapping row (Mon … Sun, full names as accessible names) in both the automation and recurring editors. The static "existing files" note moved to step 1; the bottom feedback line is shown only for errors. The sample-path box is hidden until a sample is chosen. The description box is 84 px tall (was 100). | Measured scroll range per step, below; `test_step_indicator_and_primary_action` asserts the day row |
| Sidebar could stay on "Checking…" | Medium | Through the real `main()` entry point (isolated data dir) the sidebar shows "Automatic publishing · Paused — nothing will post" with **Turn On…** within the first load. If that first load fails, it now shows "Status unavailable — see Dashboard" instead of staying on Checking… | `entry_grab` native run; `test_sidebar_never_stuck_checking_after_failed_load` |
| App-wide Pause All mixed into per-automation actions | Low | Separated by spacing and a "Stop everything at once:" label | Native screenshot |
| All-caps PUBLIC/PRIVATE in Upload History rows and the progress line | Low | Title case ("Public", "Private") in history and progress. Confirmation dialogs keep the capitals. "Last verified observation" uses a readable time. | Upload completion test asserts " · Private · " |

Setup-step vertical scroll range in pixels (0 = fits). Native Cocoa, the same automation fixture,
the committed code (before) vs this pass (after):

| Dialog height | Step 1 | Step 2 Details | Step 3 Schedule | Step 4 |
|---|---:|---:|---:|---:|
| 700 (default), before | 0 | 0 | 74 | 0 |
| 700 (default), after | 0 | 0 | **0** | 0 |
| 560, before | 0 | 129 | 214 | 0 |
| 560, after | 0 | **21** | **0** | 0 |

## Remaining issues

| Priority | Issue |
|---|---|
| Low | The Details step still scrolls 21 px when the setup dialog is squeezed to 560 px tall. |
| Low | Schedule step: the day preset dropdown ("Every day") has no label of its own (the "Posting days" heading sits below it), and "Custom interval (hours)" stays visible, disabled, unless Custom is chosen. Found in the final screenshots; not changed in this pass. |
| Low | Analytics is hidden, not implemented (a feature, out of scope). |
| Low | No person has used the app during this review (see the manual checklist). |

## Suggestions

1. Run a short hands-on session with a first-time user on the four core tasks (import → publish,
   schedule, watch a folder, create an automation) and note where they hesitate.
2. Give the video editor its own header (title, status, Back) without the library actions.
3. Give the Dashboard a single "Needs attention" list that links to each item.
4. Group automation actions into everyday actions (Pause/Edit/Open) and a "More" menu (Revoke,
   Remove, Pause All).

Stop after this review. No packaging, distribution, new platform or other feature work was done.
