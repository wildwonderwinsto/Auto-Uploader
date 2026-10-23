<p align="center">
  <img src="assets/icon.png" width="128" alt="ShortsScheduler icon">
</p>

<h1 align="center">ShortsScheduler</h1>

<p align="center">
  Write a short video's details once, then upload it to <b>YouTube, TikTok, Instagram and
  Facebook</b> — now, at a time you pick, or automatically from a folder.<br>
  A free, local-first Mac app. Your videos and sign-ins stay on your Mac.
</p>

---

## What it does

- **One set of details for every platform.** Title, description and tags are written once; TikTok,
  Instagram and Facebook get a caption made from them (tags become hashtags).
- **Upload everywhere at once.** Tick YouTube, TikTok, Instagram and Facebook, choose **Now** or
  **Schedule**, and press one button. Each platform says why if it isn't ready.
- **Schedule ahead.** Pick a day and time, or build a queue of videos that go out one at a time
  at times you choose (for example every day at 9:00 AM).
- **Watched folders.** Drop new videos into a folder and they upload by themselves, on your
  schedule, to the platforms you picked.
- **Safe by default.** Nothing goes public without your approval, the same video is never
  uploaded twice to the same account, and nothing uploads automatically until you switch
  **Auto uploading** on in the sidebar.
- **Calendar and dashboard** for everything coming up, posted, or needing attention.

YouTube uploads use Google's official API. TikTok, Instagram and Facebook posts go through your
own [Buffer](https://buffer.com) account, with videos briefly hosted on your own
[Cloudinary](https://cloudinary.com) account (both have free plans).

## Requirements

- A Mac with **macOS 12 or newer** (Apple silicon or Intel)
- [Homebrew](https://brew.sh) (the installer uses it to add FFmpeg, which reads video files)
- **Python 3.11+**, or [uv](https://docs.astral.sh/uv/) (recommended — it fetches Python for you)

## Install

```sh
git clone https://github.com/<you>/<this-repo>.git ShortsScheduler
cd ShortsScheduler
./install.sh
```

The installer sets up a private Python environment in the folder, installs FFmpeg if needed, and
builds **ShortsScheduler.app** right there. Open it by double-clicking. (Use
`./install.sh --applications` to put the app in your Applications folder instead.)

> **First launch:** the app isn't signed by Apple, so macOS may say it can't be opened. Right-click
> **ShortsScheduler.app → Open → Open** once; after that it opens normally.
>
> The app runs this project folder's code, so **keep the folder where it is**. If you move it, run
> `./install.sh` again.

## Set up your accounts

You only do this once. Every step is also shown inside the app (**Accounts** and **Settings**),
with buttons that open the right web pages.

### YouTube

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and turn on the
   **YouTube Data API v3**.
2. Set up the **Google Auth Platform** (consent screen). While it is in *Testing*, add your Google
   account as a **test user** (sign-ins then expire after seven days; reconnect when asked).
3. Create an **OAuth client** of type **Desktop app** and download its JSON file.
4. In ShortsScheduler: **Settings → Connections → Choose Google Client File…** and pick that file.
5. **Accounts → YouTube → Connect**, sign in in your browser, confirm your channel, then
   **Allow Uploads**.

### TikTok, Instagram and Facebook (through Buffer)

1. In Buffer, connect the accounts you want to post to: TikTok, an Instagram **Business or
   Creator** account, and a Facebook **Page** (Buffer's free plan allows three).
2. In Buffer's **API settings → App Clients**, create a **public** client with the permissions
   `account:read`, `posts:read` and `posts:write`.
3. Buffer requires an HTTPS sign-in address, so host the small relay page
   [`docs/buffer-oauth-relay.html`](docs/buffer-oauth-relay.html) on HTTPS. The easiest way is
   GitHub Pages on your copy of this repository: **Settings → Pages → Deploy from a branch →
   `main` / `docs`**. Its address is then
   `https://<your-github-name>.github.io/<this-repo>/buffer-oauth-relay.html` — register that
   exact address as the client's redirect URI.
4. In ShortsScheduler: **Settings → Buffer connection**, enter the **Client ID** and that
   **redirect address**, and choose **Save Buffer App**.
5. **Settings → Video hosting**: create a free [Cloudinary](https://cloudinary.com/users/register_free)
   account, copy its **API environment variable** (`cloudinary://…`) from the API Keys page, paste
   it into any field, tick **Hosted copies are public by link**, and **Save**.
6. **Accounts → Connect** on TikTok, Instagram or Facebook. One sign-in connects all three.

> Buffer only accepts a video from a public web link, so each video is copied to *your* Cloudinary
> account first. Anyone who has that link can watch the copy until it is deleted (by default a day
> after Buffer reports the post sent).

If a Buffer or Cloudinary account reaches its plan's limits, add more accounts in Settings
(**Add Buffer Account…**, **Add Cloudinary Account…**); posts automatically move to the next one.

## Using it

1. **Videos → Add Videos** and pick your video files. Originals are never moved or changed.
2. **1 Details:** title, description, tags, and each platform's own settings (YouTube visibility,
   made for kids, AI-generated content label…).
3. **2 Review:** check everything and choose **Mark as Ready**.
4. **3 Upload:** every ready platform is ticked. Untick any you don't want, choose **Now** or
   **Schedule**, and press **Upload to N Platforms…**. You confirm before anything is sent.

**Auto Upload** (sidebar):

- **Watched folders → Watch a Folder…** — pick a folder, write the details once (`{filename}` is
  replaced by each video's name), choose the times and platforms. Only videos added *after* you
  turn it on are uploaded.
- **Individual videos → Upload One Video…** opens a video on its Upload step;
  **Choose Videos…** ticks several videos that then go out one at a time at your times.

Scheduled and automatic uploads only go out while **Auto uploading** (bottom of the sidebar) is
**On** and ShortsScheduler is open — or turn on **Settings → Background uploading** to keep going
while the app is closed (your Mac must stay awake, logged in and online). If something is waiting
while Auto uploading is off, the sidebar and each page tell you.

## Your data

- Library, history and settings: `~/Library/Application Support/ShortsPublisher/ShortsPublisher/`
  (a backup of the library is made each time the app opens, in `backups/` there).
- Sign-ins and secrets: the **macOS Keychain** (entries starting with `ShortsPublisher.`).
- Nothing is sent anywhere except to the platforms you connect, when you upload.

**Update:** `git pull && ./install.sh`.
**Uninstall:** delete the project folder and ShortsScheduler.app, the data folder above, and the
`ShortsPublisher.*` entries in **Keychain Access**. If you turned on Background uploading, turn it
off (or **Remove Service…**) in Settings first.

## Good to know

- YouTube's API can't set the 18+ age restriction; TikTok privacy, Duet/Stitch and music can't be
  set through Buffer; Instagram needs a Business or Creator account; Facebook posts go to Pages.
- Cloudinary's free plan accepts videos up to 100 MB.
- Snapchat is not supported yet.
- More detail: [current status and limits](docs/current-status.md) and the
  [detailed guide](docs/detailed-guide.md).

## For developers

```sh
uv sync --extra dev                       # or: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m shortspublisher --data-dir /tmp/sp-dev    # a separate, throwaway library
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
```

The code is a PySide6 app in `src/shortspublisher/` (pages in `ui/`). Design decisions are in
[docs/architecture.md](docs/architecture.md), development notes in
[docs/development.md](docs/development.md), and the map for AI coding assistants in
[CLAUDE.md](CLAUDE.md). Tests never touch real accounts: they use controlled stand-ins for Google,
Buffer and Cloudinary.

## License

No license has been chosen yet, so all rights are reserved by the author for now.
