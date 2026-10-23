# ShortsScheduler — detailed guide

The full reference that grew with each development stage. New here? Start with the
[README](../README.md).

A local-first macOS desktop application for preparing and publishing short vertical videos to
YouTube — manually, on an approved schedule, from recurring queues, or automatically from watched
folders under explicit standing approval. YouTube is implemented and live-verified. TikTok is
implemented through Buffer and live-verified once (see [Stage 19](stage-19-buffer-tiktok-verification.md)
for the one-time Buffer, relay-page and Cloudinary setup). Instagram Reels and Facebook Page Reels
use the same Buffer setup (see [Stage 20](stage-20-verification.md)) and have not yet been
used live.

- Current capabilities and limitations: [docs/current-status.md](current-status.md)
- Developer commands and native checks: [docs/development.md](development.md)
- Architecture and safety decisions: [docs/architecture.md](architecture.md)
- AI-assistant handoff map: [CLAUDE.md](../CLAUDE.md)
- Latest stage: [Stage 20 — Instagram and Facebook through Buffer](stage-20-verification.md);
  previous: [Stage 19 — TikTok through Buffer](stage-19-buffer-tiktok-verification.md).

**Live folder-to-YouTube publishing remains pending personal in-app approval and activation.**
Packaging is not included.

## Instagram and Facebook (through Buffer)

They use the Buffer app client and video hosting you set up for TikTok.
1. In Buffer (<https://account.buffer.com/channels>) connect an **Instagram Business or Creator**
   account (personal accounts only get phone reminders) and a **Facebook Page** (not a profile or
   Group). Buffer's Free plan allows three channels: TikTok, Instagram and Facebook.
2. In **Accounts**, choose **Connect** on any of the three cards. Your existing Buffer sign-in is
   reused (otherwise Buffer's sign-in opens in the browser), and one window lists the TikTok,
   Instagram and Facebook accounts in your Buffer: tick the ones to connect and choose **Connect**.
3. Open a video, write its details once, mark it ready in **Review**, then choose **Upload**: every
   ready platform (YouTube, TikTok, Instagram, Facebook) is ticked; untick any, pick **Now** or
   **Schedule**, and choose **Upload to N Platforms…**. You confirm once (YouTube also shows its own
   exact-details confirmation). **Options** on a platform's row opens its own page and history.
   Instagram asks for the AI-generated content label in Details; Facebook has none. Posts are
   public Reels; Facebook Reels must be 9:16, at least 540×960 and 3–90 seconds ("Fits on" shows
   this per video).
4. **Auto Upload** (**Watched folders**, or **Individual videos** you choose) offers every
   connected platform; each platform gets its own
   approval.

### When a Buffer or Cloudinary account reaches its limits

Each backup must be a **separate sign-up** (its own email) — another API key of the same account
shares that account's limits. Backups are used automatically, in list order.

- **Cloudinary:** Settings → Video hosting → **Add Cloudinary Account…**, then follow the steps:
  sign up, copy the new account's API environment variable (`cloudinary://…`), paste, **Add**. When
  an account is over its plan's limits, the next one takes the video; the full one is skipped for a
  day (**Check Usage** shows every account's credits). Removing the main account makes the first
  backup the main one.
- **Buffer:** first sign up for another Buffer account and connect the **same** TikTok, Instagram
  and Facebook accounts in it. Sign out of Buffer in your browser, then Settings → Buffer connection
  → **Add Buffer Account…** → name it → **Add and Sign In**. The app tells you which of your
  accounts it found there. When a Buffer account reaches a limit, posts go through the next signed-in
  account automatically — only to the same TikTok/Instagram/Facebook account, never a different one,
  and never the same video twice.

## Automatic folder posting

1. Open **Auto Upload → Watched folders → Watch a Folder…**. Select an authorized watched folder
   or choose and authorize a local folder. Local-import consent alone does not permit publishing.
   (For videos you pick yourself, use **Auto Upload → Individual videos**: **Upload One Video…**
   opens one video on its Upload step, and **Choose Videos…** ticks several to upload one at a
   time at your times.) Nothing uploads automatically while the sidebar's **Auto uploading** switch is Off;
   the sidebar and a warning bar on each page say what is being held back.
2. Choose the connected channel and mode. **Review Before Publishing** is the default and requires
   individual human review. **Approved Automatic Publishing** uses separate standing authorization
   for qualifying future files; it never fabricates human review completion.
3. Set the filename title template, literal description/tags, explicit audience/synthetic declarations,
   eligibility limits, cadence, days, window, IANA zone and daily cap. Preview a real filename.
4. Review the exact future-file scope and select all three unchecked confirmations. Background
   permission is separate. Global scheduled publishing must also be explicitly enabled; approval
   never installs or starts a service.
5. Add a completed new video after authorization. Existing files are excluded. Stability, inspection,
   source fingerprint, metadata and duplicate checks precede queue enrollment. The next permitted
   recurring opportunity uses the existing uploader and independent result verification.
6. Use profile Pause/Resume, Disable, Edit, Queue and History controls. Editing revokes the old
   approval and requires renewed consent. Global pause, folder pause, service stop and active-upload
   stop have distinct effects. Failed or uncertain uploads are never automatically re-enrolled.

The user-operated native picker cancel/select and separate-process restoration passed in Stage 17.
Controlled tests verified background operation. Existing manual publishing, individual
schedules and finite approved Stage 15 batches remain available.

## Recurring Public posting

1. Import videos, save their metadata with Public visibility, and complete each video's review.
2. Open **Videos → Recurring Posting…** or **Publishing Queue → New Recurring Posting Queue…**.
   Select a finite batch of reviewed videos and the connected YouTube channel.
3. Choose once per selected day or a whole-hour interval (1–24), weekdays/weekends/custom days,
   an IANA zone and a same-day posting window. Intervals restart at the local start time each day;
   the end is included. Nonexistent DST times are skipped; repeated times use the first occurrence.
4. Review the exact titles, descriptions, tags, declarations, channel, sources, rule and next
   opportunities. Confirm that no manual restriction/additional YouTube setting remains unresolved,
   then explicitly approve automatic Public uploads of that batch. New plans start paused.
5. Activate the plan and separately enable global scheduled publishing. At each opportunity the
   next valid approved video can start through the existing uploader, without another dialog.
   ShortsScheduler sets no daily limit of its own; only YouTube's own 100 uploads a day applies.
6. Queue shows ordered videos, actual acknowledged transfer progress and verified/uncertain outcomes.
   Calendar shows future opportunities separately from concrete individual jobs. Pause/resume,
   reorder or remove unstarted items; history remains. Approving additional videos requires a new
   batch confirmation. Changing rule/channel/background permission requires a new plan; remove
   unstarted items from the old queue before approving them again.

Empty, blocked and missed opportunities are skipped without catch-up. Startup and resume skip past
opportunities, including one already due; the normal running dispatcher has the existing 60-second
start window. An invalid item needs attention and the next valid item may use that opportunity.
Failed/uncertain executions are never automatically requeued: use existing history/reconciliation.
Copies or renames do not grant another publishing opportunity on the same channel.

Background publishing is a separate unchecked permission on the batch, and requires the existing
separately authorized service to be running, an awake Mac and accessible files. It installs nothing.
A paused recurring plan stops new session initiation; an already initiated upload retains its
existing history/recovery behavior. Global Stop retains the existing transfer cancellation behavior.
Age restrictions still require YouTube Studio; “not made for kids” is not an 18+ setting.
This stage never supplies metadata or declarations for future files.

## Automatically import local videos

Open **Videos → Automatically Import Videos → Watch a Folder…**. Choose a real local folder,
review the candidate count, explicitly choose whether to include existing videos or only new
arrivals, and enable local importing. Subfolders, dotfiles, temporary extensions and symlinks are
excluded. Multiple folders are supported. Use **Scan Now**, **Pause Watching / Resume Watching**,
and **View Import Results**. Folder Settings shows the full path and diagnostics. To change a
location, pause it and choose **Change Folder**; approve the new location separately, then remove
the old paused configuration if desired. Removing monitoring leaves source files and library records.
To change quiet-period/background permission, remove monitoring and explicitly add it again;
the duplicate registry is retained.

The default quiet period is 15 seconds, with at least two matching observations, nonzero size,
SHA-256 and source identity checks, and successful FFprobe inspection for new content. It is a
heuristic: finish rendering/copying to a temporary extension, close the file, then atomically rename
it to a supported video extension for a reliable handoff. Supported candidate extensions are MP4,
MOV, WebM, MKV, M4V and AVI; actual inspection determines validity. Import does not mean Shorts
readiness, review approval or publishing eligibility. Invalid files retry with backoff.

Notifications are debounced for one second; a ten-second reconciliation scan catches missed events.
Monitoring resumes when the app opens, and preserves an active editor and its unsaved text. Results
are snapshots of current per-file states. Changes to an existing library path never silently replace
its source or metadata. A renamed source is recognized as duplicate content, but its original saved
library path remains unchanged; restore it or use the existing manual removal/import workflow.

The optional **Also import locally while the existing authorized background service runs** checkbox
is separate permission for each folder. It neither installs nor starts a service nor authorizes a
Public upload. With that permission, the existing running worker imports even while publishing is
paused or the GUI is closed. Use folder Pause to pause discovery; stopping the service stops its
local scanner too. The Mac must be awake and the source accessible. The worker finds FFprobe on its
PATH or in standard Homebrew locations (`/opt/homebrew/bin`, `/usr/local/bin`). A custom GUI
`--ffprobe` override is not persisted for launchd. Missing FFprobe produces retryable attention.

Byte-identical copies, renames and moves are recognized across monitored folders, manual library
entries and stored publishing history. Historical library IDs remain separate. Unavailable legacy
sources without a known fingerprint block unknown intake until restored or explicitly removed;
this favors duplicate prevention. SHA-256 does not detect reencoded equivalents or uploads outside
this app. Existing-file exclusions survive restart; an excluded pathname remains excluded until
monitoring is removed and re-added with an explicit include-existing choice. Prior content identities
remain retained even after removal. No original is renamed, deleted or rewritten by discovery.

The first GUI launch transactionally migrates schema 8 to 9; no watched folders are enabled by the
migration. A headless worker refuses an older schema until the GUI has migrated it. Verification used
isolated databases; the genuine existing workspaces were left at schema 8.

Opening **Videos** always shows the library. Select a row or **Continue Editing** to open details.
Import selects the first successfully imported file and opens Details; other imported files remain
in the library. **Save and review** saves your draft and opens Review. Complete review, then choose
**Publish now** or **Schedule**. Upload History is accessible from the Videos header without choosing
a video. History alone never selects a publishing target. Back to Library honors Save/Discard/Cancel;
clicking Videos while editing can park the unchanged buffer behind Continue Editing.

Dates use named months and include the year. Plans display the approved IANA zone's local instant,
including its DST abbreviation. Other timestamps use the system zone; clock formatting follows the
system locale's 12/24-hour convention. Display formatting never changes approved schedule instants.

## Quick start on this Mac

Install with `./install.sh` and open **ShortsScheduler.app**; see the [README](../README.md). The
app backs up its library (`backups/` in the data folder) before every launch.

## Optional macOS background publishing

Background publishing is **disabled by default**, independently of global scheduled publishing.
Before upgrading an existing library, close the old interface, stop any existing service and back up
SQLite. Keep the same data directory and connection. Migration preserves the global preference,
all previous attempts, and job approvals; older approvals remain desktop-only.

1. Open **Settings → Background Publishing**. Choose **Enable Background Publishing…** and read
   its separate consent. This installs the managed configuration; it does not initiate an upload.
2. Optionally select **Also start at login** before enabling. This is a separate preference.
   Click **Start Background Service**. Wait for Running or Paused and a fresh check-in; Starting
   or a saved Enabled setting alone does not establish readiness.
3. Import/review a new Public video, approve its future schedule and open automatic activation.
   Select **Also allow this exact upload while the interface is closed** only if intended.
   Already activated desktop-only jobs need a future reschedule/reapproval to change this scope.
4. Enable global scheduled publishing. All permission layers are required. Keep the Mac logged in,
   awake and connected. Keychain may require permission; do not disable its security protections.
5. Close the interface normally. The separately managed worker continues. Reopen the same data
   directory to view actual acknowledged progress and history in Dashboard, Queue and Calendar.

**Pause All Scheduled Publishing** prevents new starts but does not cancel an active upload.
**Stop Background Service** asks the worker to stop after its current request. It remains stopped
until Start, including at a later login. Once ownership is released, an open desktop can schedule
again; use global Pause to prevent all new starts. **Stop Background Publishing and Quit** stops
service execution and closes the interface. Neither action deletes remote content.
**Disable** also removes login startup; **Remove** unregisters and removes only managed plist files.
Both preserve the library, history, media and Google connection. Refresh status if stopping takes
longer than the bounded control wait; do not infer that a stale heartbeat means the process is gone.

The per-user label is `com.shortspublisher.publisher`, with one managed workspace per user.
Its session-only plist is `<data-dir>/background/com.shortspublisher.publisher.plist`; explicit
login startup additionally writes `~/Library/LaunchAgents/com.shortspublisher.publisher.plist`.
The interpreter, worker script and data paths are absolute. Keep this checkout and its virtual
environment in place; stop/remove the service before moving them. An unrelated or other-workspace
configuration is never overwritten. No root access, network admin endpoint or shell commands are used.

The worker holds an OS dispatcher lock across its lifetime. An active foreground transfer finishes
before background ownership is acquired. The desktop becomes a viewer while the service is requested;
it cannot take over after a crash merely because health is stale. Explicit Stop releases that request,
and the OS lock must also be free. Crashed/claimed uploads require existing Upload History recovery;
no automatic session resume or late catch-up occurs. The service does not automatically relaunch on
crash. Start it explicitly after inspecting errors and history.

Logs rotate at 1 MB with three backups under `<data-dir>/background/logs/worker.log`.
Control requests use the current user's `launchctl` domain; status combines OS PID and worker check-in.
No upload tokens or session URLs are stored in service configuration or logs. A locked/unavailable
Keychain can block publishing. This source checkout is not a signed/notarized packaged application.
Actual logout/login, exhaustive OS Keychain prompts and power-loss behavior have not been verified.
The service does not wake the Mac, prevent sleep, run before login, or publish while powered off.

Controlled native background verification (new disposable root; finish both commands for cleanup):

```sh
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_background_smoke.py prepare --root /tmp/publisher-stage10-check
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_background_continue.py --root /tmp/publisher-stage10-check
```

These use isolated test service labels and real synthetic Keychain entries with controlled Google
responses. They install real per-user LaunchAgents, prove GUI exit and worker survival, reopen during
transfer, restart without duplicates, then remove test services/credentials. They never access the
the owner's channel credential or publish to YouTube. A real background Public test needs separate explicit consent.

## Preparing and previewing a video

The Videos workspace follows **Source → Details → Review → Publish / Schedule**.
After selection, expand **Library · choose a video** to switch videos. Search by filename, saved
title or location; choose a status filter and sort by import date, filename or duration. Search
is case-insensitive and recognizes equivalent Unicode text. Clearing search restores the list.
Changing filters never switches the video you are editing: a message explains when it is hidden.
Reloading the library keeps the selected video and editing step.

Library filters describe saved records: **Review complete (saved)** still requires current source
and account checks before upload. **Has a future plan** includes plans awaiting activation;
**Has a YouTube result** means an upload returned an ID, not necessarily completed processing
or verified visibility. Expand Library or use Reload Library to refresh these records.

File diagnostics,
optional tags, recovery controls and service diagnostics are expandable.

Use **Play preview** to watch the local original. Playback starts muted, supports seeking,
and stops when leaving Videos or closing the window. It does not change the source or replace
FFprobe inspection. At narrow widths the preview appears below the current panel.

Details asks explicitly whether the video is made for kids and includes altered/synthetic
content. **18+ age restriction cannot be applied by this app through the upload API.** If needed,
upload privately and set the restriction in YouTube Studio before publishing. No local switch
claims to apply it. Unlisted remains a saved preference only; uploads support Private and Public.

Settings separates service setup, Start/Stop and advanced options. The sidebar's global scheduled
publishing control is independent. Dashboard shows observed channel, publishing and service states,
plus saved upload results; previewing or navigating never supplies publishing consent.

## Launch

Python 3.11+ is required (macOS's system Python 3.9 is too old). From this directory:

```sh
uv sync --python 3.12 --extra dev
uv run --offline shortspublisher
```

After installation, launch without uv:

```sh
.venv/bin/python -m shortspublisher
```

On Windows the equivalent interpreter is `.venv\Scripts\python.exe`; Windows and Linux
have not been tested. Installation and YouTube connection/verification need internet; video preparation works offline.
This is a development checkout, not a packaged installer.

## Connect a YouTube channel (macOS)

1. Open [Google Cloud Console](https://console.cloud.google.com/) and select or create a project.
2. Enable **YouTube Data API v3** in APIs & Services. Configure Google Auth Platform branding,
   audience and data access. Initial connection requests `https://www.googleapis.com/auth/youtube.readonly`.
   Uploading additionally requests `https://www.googleapis.com/auth/youtube.upload`; no other scopes are used.
3. For an External app in **Testing**, add your Google account under test users. Create an
   OAuth client of type **Desktop app** and download its JSON. A Web client or service account
   is not suitable; no custom web redirect URL or source-code edits are needed.
4. Open **Accounts → Choose Desktop Client JSON…** and select the downloaded file. The app
   validates its type and endpoints, storing only the required public desktop-client fields.
   Keep the download outside this source checkout. A desktop client secret is not a securely
   embedded application secret; it is separate from your private authorization tokens.
5. Click **Connect YouTube**. Your system browser opens Google's consent screen. Choose the
   intended YouTube/Brand Account, approve read-only access, and return to ShortsPublisher.
   The app listens on `127.0.0.1` at an ephemeral port for up to three minutes. Cancel is available.
6. **Confirm Selected Channel** only after checking its title and stable channel ID. Multiple
   returned channels require selecting the intended ID. Unexpected channel? Cancel and reconnect
   with the correct account. Channel confirmation expires after five minutes; unconfirmed credentials are never saved to disk.
7. macOS may ask for Keychain permission. After confirmation, the app stores authorization in
   Keychain and displays the verified channel. A failed Keychain operation never falls back to a file.
8. Restart the app: stored authorization is checked live once. Use **Refresh Channel** for another
   check. Missing credentials require reconnecting; network failures keep authorization and label
   cached information. After five minutes, a verified status becomes **Verification pending** without
   polling. An earlier successful check never guarantees the account is still authorized.

**Disconnect…** asks for confirmation. By default it removes only local authorization and the
active channel connection, preserving videos, drafts and reviews. Optionally revoke Google access;
this may affect other permissions granted to the same Cloud project. Remote revocation is reported
as successful only after Google's HTTP 200 response. If it cannot be confirmed, remove project
access in your [Google Account connections](https://myaccount.google.com/connections) when online.
To replace a client or reconnect an expired grant, disconnect locally first, then connect again.

External **Testing** refresh tokens for YouTube access can expire after seven days. Production
publishing status removes that testing-specific lifetime but can require Google's verification;
tokens can still expire or be revoked. Future unattended publishing must handle reconnection and
cannot rely on indefinite authorization. See [Google's expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration).
Upload permission is a separate explicit Accounts action. Failed additional consent preserves the
existing connection. Google does not support incremental authorization for installed apps: the new
consent requests both scopes and verifies the same channel before saving the grant. A missing new
refresh token is accepted only if the existing renewable grant is verified to carry both scopes.

Network requests have finite connect/read timeouts. Only transient channel-request failures get
bounded retries; denied consent, invalid refresh grants and exhausted daily quota are not retried
blindly. Closing waits for an active request to finish/cancel safely. Keychain system prompts may
require a response in macOS before a storage operation can finish.

## Schedule an automatic Public upload

**Before opening an older library in this version, close the old application and back up its SQLite
file.** Schema 7 preserves old records and leaves every Stage 8 plan execution-disabled. The global
publishing switch starts **Paused**. Neither old approval nor connecting an account authorizes uploads.

1. Select a video, save **Public** metadata and complete **Review**. In Accounts, connect the intended
   channel and authorize uploads. Planning can use cached identity; execution checks real credentials,
   scopes and the live channel again.
2. Open **Schedule → Schedule Video…**. Choose a future local date, time and IANA zone. The editor
   shows UTC; nonexistent DST times are rejected and repeated times require an explicit occurrence.
3. Review and approve the exact plan. **Save Draft** never authorizes publishing. To activate in the
   same flow, select **After saving, review automatic Public activation**. Alternatively select the
   future plan in Queue/Calendar and click **Activate Automatic Public Publishing…**.
4. Read the separate authorization: exact source/hash, metadata/declarations, channel ID, PUBLIC
   privacy, local/UTC instant and automatic execution **without a second confirmation**. Accept
   **Authorize Automatic PUBLIC Upload** only for that exact job. Cancelling leaves it disabled.
5. Click **Enable Scheduled Publishing…** in the navigation rail and confirm. Dashboard shows the
   persistent global setting, next activated job, attention count and active transfer. The default
   budget is three automatic claims per UTC day, configurable from one to ten in Dashboard; blocked
   claims count conservatively. This is an application limit, not a promise of Google's available quota.
6. Leave the app open, Mac awake and source accessible. At the intended instant a worker claims one
   eligible job, checks permissions/channel/review and hashes the source, then starts the existing
   resumable Public upload. Scheduled time means **upload start**, not guaranteed public availability.
7. Queue and Calendar show acknowledged bytes, linked attempt, returned ID, privacy verification and
   processing separately. **Completed** requires Public Verified plus processed/succeeded. Uploading
   at 100% alone is not success. Use **Upload History — Resume / Verify / Resolve…** for recovery.

**Pause All Scheduled Publishing** persists immediately through a separate control worker and prevents
new starts; it does not stop an already-started transfer or undo remote content. Use **Stop Active
Scheduled Upload** in Dashboard/Queue/Calendar to stop after the current request returns. Closing
coordinates worker cancellation. No daemon or closed-app publishing exists.

The dispatcher uses the next upcoming deadline and a five-second safety check, independent of the
selected page and schedule editor. The separate 30-second planning refresh is not the execution timer.
A start must be durably authorized within the 60-second Due window, including preflight time. Expired
windows become Missed; reopening after an unstarted job's instant or enabling after that instant never
catches it up, even inside 60 seconds. Reschedule and reapprove deliberately. Clock rollback cannot
reset an execution or a Missed job. OS/network delays can still prevent on-time execution.

Every activation binds the exact approved snapshot and time. Rescheduling before claim invalidates
activation. Changed source/review, removed videos, wrong channel, unavailable credentials, conflicts,
or known quota restrictions block initiation and retain the reason. No blind automatic retry creates
a new session. A competing manual upload uses the same locks; a due job can wait only within its window.

After claim, the job cannot be rescheduled or cancelled as an unstarted plan. Its single linked attempt
is preserved across restart; transfers never resume automatically. Explicit Resume queries the same
secure session, Verify checks a returned ID, and uncertain outcomes require Studio inspection and the
existing duplicate-risk acknowledgement. After deliberately releasing the attempt in Upload History,
**Close Reconciled Schedule…** closes only that local plan. A new plan needs fresh review/authorization.
Nothing here deletes, updates or changes the privacy of a remote video.

Calendar dates use each plan's saved zone; Queue sorts by UTC. Active plans reserve video ID and source
hash per channel. A conflicting manual upload requires cancelling an unclaimed plan first. Separate
copies of the same database are not coordinated stores. Original videos are referenced, never modified.

## Publish one reviewed video publicly

1. In Details, explicitly select **Public**, save, and complete the current review.
2. In Accounts, verify the intended channel and **Authorize Uploads…** if needed. The same
   read-only/upload scopes support both actions; authorization alone never uploads anything.
3. Open the video's Upload tab and choose **Publish Publicly Now…**. The worker checks live
   channel identity, permission, source integrity, current review and conflicting attempts.
4. Review the exact source path, size, duration, dimensions, metadata, channel ID, audience,
   synthetic declaration and **PUBLIC** visibility. Only **Publish Publicly Now** in this separate
   confirmation starts the upload. Cancel creates no session and transfers no video.
5. After transfer, the app independently calls `videos.list`. **Public Verified** requires the
   returned ID, expected channel and actual Public privacy. Processing is shown separately;
   transfer success or verified privacy does not guarantee completed processing or playback.
6. If verification is unavailable, the ID is retained as unverified. Use **Verify Remote Result**.
   A Private result or wrong channel becomes **Needs Reconciliation**; the app never changes
   remote visibility or repeats the upload automatically.

The approved privacy stays fixed during recovery. History shows requested and returned privacy,
verification status/time and processing. Existing Private attempts stay Private. Schema 4→5 adds
verification fields without rewriting existing snapshots; old verification timestamps remain unknown.

**Stage 7 live verification passed:** one user-confirmed Public upload was independently verified
with successful processing and persistent history after restart. Each new upload still needs its own direct final confirmation. Do not reuse or publish Stage 6's Private
video `<youtube-video-1>`. See [Stage 7 verification](stage-7-verification.md).

## Upload one reviewed video privately

1. In **Accounts**, verify the intended channel, then choose **Authorize Uploads…**.
   Approve Google's official browser consent for the same channel. No upload starts during consent.
2. Select a video with saved **Review Complete** status. Open its **Upload** tab and click
   **Upload Privately…**. Source hashing and live destination checks run in a background worker.
3. Review the exact filename, metadata, channel ID, declarations and SHA-256. Category is
   People & Blogs (22). **Private is forced**, including when Planned privacy says Public or Unlisted.
4. Only **Upload Privately Now** starts a resumable session and transfer. Cancel sends no video.
   Progress counts bytes acknowledged by YouTube, not fabricated time-based percentages.
5. Inspect the returned video ID, actual privacy, channel and processing status in Upload History.
   **Uploaded Private** means the response verified the ID, Private status and intended channel;
   it does not mean publicly published, or necessarily fully processed. **Verify Remote Result**
   rechecks the saved ID. Failed verification labels the previous result as cached/unverified.

**Stop Upload** and closing stop further chunks after the active request returns. They never delete
remote content. Restarting never resumes automatically. **Check Session and Resume…** validates the
original source/review and checks the existing secure session before sending more bytes. A changed
source or metadata cannot be substituted. Returned IDs use remote verification instead of transfer.

An expired/missing session or uncertain response becomes **Needs Reconciliation**. Check YouTube
Studio before **Acknowledge Outcome / Allow Another Attempt…**; its warning requires deliberate
acceptance of possible duplication. The acknowledgement itself does not upload or delete anything.
Local records cannot guarantee discovery of every remote duplicate. History survives library removal.
Local schedules are separate saved plans; Unlisted initiation and remote video deletion/modification are not implemented.

Upload session URLs stay in macOS Keychain under `ShortsPublisher.UploadSessions`, keyed by random
attempt IDs. SQLite contains only nonsecret snapshots, SHA-256, progress, IDs, states and timestamps.
Session entries are retained for recovery, including after account disconnect; reconnecting to the
same channel is required to use them. A database backup alone cannot restore a resumable session.
Do not run copied databases concurrently against shared Keychain entries. SQLite, Keychain and
YouTube cannot commit atomically; interrupted operations may require manual reconciliation.

Current [insert documentation](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert)
and [quota documentation](https://developers.google.com/youtube/data/determine_quota_cost), checked
October 17, 2026, specify 100 insert calls/day by default, costing one unit each in a separate Video
Uploads bucket. Consult your project's actual limits. Quota/channel limits stop retries; no posting
rate guarantees promotion or prevents restrictions. Google pages conflict about unverified-project
privacy restrictions. Stage 7 independently verified Public access for one user-confirmed upload;
that observation does not guarantee future access or absence of restrictions.

## Import and inspect a video

1. Open **Videos → Import Video…** and select a local MP4, MOV, WebM, or another video.
   The All files filter allows other containers; validity comes from FFprobe, not the extension.
2. Wait for inspection. Navigation stays available; conflicting library actions are disabled.
   The moving activity indicator has no percentage because FFprobe does not report one.
3. A successful inspection saves the video automatically and selects its details. Invalid
   imports show an error and never create a partial library record.
4. Review **YouTube Shorts Readiness**, then close and reopen the application. Open Videos
   and select the saved entry. Use **Refresh Inspection** after changing or restoring a file.
5. **Remove from Library** requires confirmation and removes only the database entry.

Install FFprobe separately, as part of FFmpeg. On a Mac with Homebrew:

```sh
brew install ffmpeg
ffprobe -version
```

FFprobe must be on the application's PATH. If launching from a different environment:

```sh
.venv/bin/python -m shortspublisher --ffprobe /opt/homebrew/bin/ffprobe
```

There is no automatic software download. Missing FFprobe does not prevent startup or
browsing saved records; new imports and refreshes report clear setup errors. FFmpeg itself
is used only to generate playable test fixtures; the app invokes FFprobe alone.
Stage 4 adds Google OAuth, HTTP, Keychain and operation-locking dependencies; `uv.lock` records exact versions.

## Prepare a publishing draft and complete local review

Select a video to use its **Source**, **Details**, and **Review** tabs.

1. In Details, enter a title and optional multiline description. Live counters show
   the title's 100-character limit and the description's 5,000 UTF-8-byte limit. Emoji can use
   several bytes. `<` and `>` are prohibited in titles/descriptions by YouTube's API rules.
2. Add keyword tags individually. These are not hashtags; the app does not insert hashtags.
   The combined 500-character count includes separators and two extra quote characters for
   each tag containing a space. Duplicate normalized, case-insensitive keywords are not added
   (an app convenience, not a claim about YouTube). A pending tag is included when you save.
3. Explicitly choose Yes or No for **Made for Kids** and **Altered or synthetic content disclosure**.
   Both start as Not specified. Read the explanatory guidance: animation or AI assistance alone
   does not automatically require disclosure. The documentation link opens only if clicked.
4. Choose **Planned privacy**, defaulting to Private. This is a saved preference, not a publish action. Public uploads require Public plus a current completed review.
5. Click **Save Draft**. Incomplete or invalid drafts can be saved. The saved indicator changes
   only after the database write succeeds; failed saves leave your edits available for retry.
6. Click **Next: Review →**. Review the video identity, metadata, blocking issues and warnings.
   Save first, fix blocking issues, and click **Mark Review Complete** to explicitly record review.
   Silent audio can be a warning; landscape shape, excess duration, an unavailable/changed source,
   failed inspection, invalid metadata or unspecified declarations block completion.
7. Close and reopen the app, select the video, and open Review to see its saved local state.

The states **Draft**, **Needs Attention**, and **Review Complete** describe local preparation,
not publishing status or YouTube approval. Edits immediately show a draft state; saving changed
metadata invalidates its prior review. Refreshing inspection also invalidates review, even if the
file still looks the same. Recheck Source and Saved Details checks current source availability
and saved metadata. Source changes detected on selection/recheck invalidate review durably.
There is no filesystem watcher; status describes the last explicit check.

Save / Discard / Cancel prompts protect edits when selecting another video, navigating away,
reloading, refreshing, removing or closing (including application Quit). Save waits for a successful
write before continuing. While a save is active, navigation/close waits for that save. If another
instance changed the saved revision, your stale save is refused; copy your edits before choosing
to discard/reload and resolve the conflict. No existing title is automatically overwritten.

Rules were researched October 15, 2026 from the [video resource reference](https://developers.google.com/youtube/v3/docs/videos),
[insert reference](https://developers.google.com/youtube/v3/docs/videos/insert), and
[disclosure guidance](https://support.google.com/youtube/answer/14328491).
Local metadata validation makes no API requests. These checks cannot guarantee future API acceptance,
copyright clearance, policy compliance or successful publishing.

## What readiness means

The rules follow [YouTube's official Shorts guidance](https://support.google.com/youtube/answer/15424877?hl=en),
checked October 15, 2026: square or vertical display shape and at most three minutes.
Pixel aspect ratio and rotation are considered. Missing audio is a warning, not an invalid
video. Unknown optional metadata is reported. H.264, AAC, 1080×1920, or a specific frame rate
are not imposed as invented mandatory requirements.

FFprobe reads metadata and counts decoded frames across the file. Errors, missing required
metadata, empty streams, changing files, and inspection timeouts prevent a passing inspection.
The default timeout is 120 seconds; a very large or slow file can time out without being corrupt.
These checks cannot detect every defect and do not guarantee acceptance, rights clearance,
or successful publishing. The account connection separately uses read-only YouTube channel requests.

Library rows show the **last inspection**. Selecting a row checks whether that source is
missing or changed, without an expensive scan of every file at startup. Failed refreshes
preserve previous metadata and visibly mark it as unverified. Files are referenced, not copied:
keep originals in a stable location. Reimporting the same canonical path (including symlink
aliases) selects its existing entry. Identical content at a different path is not deduplicated.

## Storage and troubleshooting

SQLite stores paths, inspection metadata, timestamps and refresh errors. The default macOS
location comes from Qt's application-data directory:

`~/Library/Application Support/ShortsPublisher/ShortsPublisher/library.sqlite3`

Schema version 12 is recorded in `PRAGMA user_version`. Existing libraries migrate
transactionally during startup; the desktop dispatcher also initializes new libraries. Existing video IDs and inspection metadata are preserved. Publishing details
are separate platform-keyed records, and deleted IDs are never reused. Database failures
never trigger deletion or reset. Close another running copy if the database is locked, then use
**Reload Library**. Back up the database with the application closed; it does not contain videos.

Use `--data-dir /path/to/test-data` for an isolated library. Logs normally live in the data
location's `logs` subfolder, rotate at 1 MB with three backups, and also go to stderr.
`--log-dir PATH` overrides log storage. Log and error details can include local file paths.
Authorization tokens live only in macOS Keychain, separate from the database and logs.
Database backups contain cached channel identity and public desktop-client configuration, but no
access/refresh tokens. Restoring a backup without its Keychain entry requires reconnection.
Keyboard navigation remains available with Tab, Space,
and Ctrl+1 through Ctrl+7. Content scrolls at smaller sizes; minimum window size is 760×560.

## Checks

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m shortspublisher --smoke-test
```

Real-media tests require FFmpeg and FFprobe; otherwise those tests explicitly skip. Tests use
temporary libraries and generated fixtures, never real user videos or the default library.

Native macOS smoke workflow (use a new disposable root for each run):

```sh
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_library_smoke.py import --root /tmp/publisher-native-check
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_library_smoke.py reopen --root /tmp/publisher-native-check
```

The two separate processes exercise actual background inspection, UI metadata, persistence,
refresh, and removal, and capture screenshots at two sizes. The harness supplies the file-picker
selection and removal-confirmation response; manual OS picker interaction is not asserted.

## Organization

- `app.py`: startup, CLI configuration and composition.
- `media.py`: safe cancellable FFprobe inspection and metadata parsing.
- `readiness.py`: versioned YouTube technical rules, independent of UI and storage.
- `watching.py`, `watch_schema.py`, `ui/watching.py`: shared local scanner, persistent content registry, consent and import activity.
- `library.py`: SQLite records, transactional schema migrations through version 14 and local library operations.
- `buffer.py`, `hosting.py`, `social.py`, `social_schema.py`, `ui/social.py`: TikTok, Instagram and Facebook through Buffer with Cloudinary media hosting (`tiktok.py`, `tiktok_schema.py`, `ui/tiktok.py` keep the Stage 19 names).
- `recurring.py`, `cadence.py`, `automation.py` and their `*_schema.py` / `ui/*.py`: recurring queues, posting opportunities and automatic folder profiles.
- `background.py`, `background_state.py`, `worker.py`, `worker_entry.py`, `ui/background.py`: optional LaunchAgent service.
- `ui/formatting.py`: English, time-zone-aware display of dates, times, sizes and statuses.
- `preparation.py`: YouTube draft validation, revision-aware saves and local review storage.
- `auth.py`, `credentials.py`, `youtube.py`: Google desktop OAuth, explicit Keychain backend and read-only channel adapter.
- `accounts.py`, `ui/accounts.py`: account persistence, serialized operations and Accounts presentation.
- `uploads.py`, `upload_http.py`, `upload_store.py`: Private/Public resumable uploads, secure recovery and durable attempts.
- `ui/uploads.py`: confirmation, acknowledged-byte progress and history.
- `scheduling.py`, `ui/scheduling.py`: plans, time zones, review, Queue and Calendar.
- `execution.py`, `execution_schema.py`, `ui/execution.py`: activation, migration, durable claims and desktop dispatch.
- `ui/metadata.py`: editable fields, counters, tags and preparation tabs.
- `ui/videos.py`: library presentation and the single background operation boundary.
- `ui/window.py`, `ui/theme.py`: shared navigation and visual design.
- `diagnostics.py`: bounded logging.
- `tests/`: isolated service, storage, process, UI and native integration checks.

ShortsStudio is a read-only design reference, never a runtime dependency. Adapted ideas:
semantic dark colors, blue selected states, cards, stacked destinations, library list/details,
and workers that communicate with the GUI through queued signals. No production logic,
credentials, media, or entire components were copied.

Stage 3 native workflow (use a new test root):

```sh
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_metadata_smoke.py prepare --root /tmp/publisher-metadata-check
QT_QPA_PLATFORM=cocoa .venv/bin/python tests/native_metadata_smoke.py reopen --root /tmp/publisher-metadata-check
```

The harness imports a real generated Short, edits every field, saves, reviews, exits, reopens in
another process, checks exact metadata, changes a reviewed field, and reviews again. Only the
OS file-picker result is supplied automatically. Screenshots and source hash checks are real.

See [architecture](architecture.md), [Stage 2 verification](stage-2-verification.md),
[Stage 3 verification](stage-3-verification.md), and
[Stage 4 verification](stage-4-verification.md), and
[Stage 5 verification](stage-5-verification.md), [Stage 6 verification](stage-6-verification.md),
and [Stage 7 verification](stage-7-verification.md). Stage 7 is live-verified. See [Stage 8 verification](stage-8-verification.md) for local scheduling.
See [Stage 10 verification](stage-10-verification.md) for the optional macOS service.
Real background YouTube publishing remains unverified. See [Stage 11 UI verification](stage-11-verification.md).

Native Stage 4 controlled-provider workflow (fresh disposable root; uses an isolated real Keychain namespace):

```sh
.venv/bin/python tests/native_accounts_smoke.py prepare --root /tmp/publisher-accounts-check
.venv/bin/python tests/native_accounts_smoke.py reopen --root /tmp/publisher-accounts-check
```

The second process verifies persistence and removes the synthetic Keychain entry. These commands
never sign into Google. Native selection and disconnect confirmation are supplied by the harness.
Full tests include local loopback sockets, which need an environment allowing local listeners.

Native Stage 5 workflow (fresh disposable root; synthetic Google responses and isolated real Keychain):

```sh
.venv/bin/python tests/native_upload_smoke.py prepare --root /tmp/publisher-upload-check
.venv/bin/python tests/native_upload_smoke.py reopen --root /tmp/publisher-upload-check
.venv/bin/python tests/native_upload_smoke.py history --root /tmp/publisher-upload-check
```

Run all three phases; the final process verifies saved success and removes its synthetic Keychain
entries. These commands never call Google's upload endpoint. Use the normal application only for
an explicitly approved live test with your own client, intended channel and disposable video.

Native Stage 7 Public workflow (fresh root; controlled Google responses and isolated real Keychain):

```sh
.venv/bin/python tests/native_public_smoke.py prepare --root /tmp/publisher-public-check
.venv/bin/python tests/native_public_smoke.py reopen --root /tmp/publisher-public-check
.venv/bin/python tests/native_public_smoke.py history --root /tmp/publisher-public-check
```

Finish all phases to remove synthetic Keychain entries. This does not authorize or perform a real Public upload.

Native Stage 8 local workflow (new disposable root; all HTTP requests forbidden by the harness):

```sh
.venv/bin/python tests/native_schedule_smoke.py prepare --root /tmp/publisher-schedule-check
.venv/bin/python tests/native_schedule_smoke.py reopen --root /tmp/publisher-schedule-check
```

These exercise genuine import/review, approval, queue/calendar synchronization, rescheduling,
cancellation, restart, injected Due/Missed times and attention states. They never upload a video.


Native Stage 9 automatic workflow (fresh disposable root, controlled Google responses):

```sh
.venv/bin/python tests/native_execution_smoke.py prepare --root /tmp/publisher-stage9-check
.venv/bin/python tests/native_execution_smoke.py reopen --root /tmp/publisher-stage9-check
```

Complete both phases to remove their synthetic Keychain entries. These exercise real import/review,
activation, Qt timer dispatch, exact-byte transfer, independent result parsing, paused/missed and
wrong-channel cases, and restart without another upload. They do not establish live YouTube success.

Stage 12 adds focused library discovery, selection retention and preview feedback. See
[Stage 12 verification](stage-12-verification.md).
