"""Native Instagram/Facebook-via-Buffer audit: real Cocoa widgets, isolated SQLite/media, controlled
Buffer and Cloudinary. No network, no real credentials, no real post."""

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from threading import Event

from buffer_support import CHANNELS
from PySide6.QtWidgets import QScrollArea
from test_social_ui import SocialUiTests

from shortspublisher.cadence import Rule
from shortspublisher.social import INSTAGRAM
from shortspublisher.ui.autoupload import AutoUploadEditor
from shortspublisher.ui.social import AutomationConsent, ChooseAccounts, ConnectDialog

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=True)

SocialUiTests.setUpClass()
t = SocialUiTests()
t.setUp()
try:
    w, app = t.w, t.app
    sizes = []

    def capture(name: str, focus: list | None = None) -> None:
        for width, height in ((760, 560), (1180, 780), (1600, 1000)):
            w.resize(width, height)
            app.processEvents()
            target = w.stack.currentWidget()
            for position in ("top", "bottom"):
                if isinstance(target, QScrollArea):
                    bar = target.verticalScrollBar()
                    bar.setValue(bar.maximum() if position == "bottom" else 0)
                    for widget in focus or []:
                        if position == "top" and widget.isVisible() and target.isAncestorOf(widget):
                            target.ensureWidgetVisible(widget, 0, 0)
                app.processEvents()
                w.grab().save(str(args.root / f"{name}-{width}-{position}.png"))
                sizes.append(
                    {
                        "name": name,
                        "requested": [width, height],
                        "actual": [w.width(), w.height()],
                        "horizontal_overflow": target.horizontalScrollBar().maximum()
                        if isinstance(target, QScrollArea)
                        else 0,
                    }
                )

    def dialog_shot(dialog, name: str) -> None:
        dialog.show()
        app.processEvents()
        dialog.grab().save(str(args.root / f"{name}.png"))
        dialog.close()

    ig, fb = w.instagram, w.facebook
    w.navigate("accounts")
    capture("accounts-not-connected", [ig.card])
    dialog_shot(ConnectDialog(w, INSTAGRAM), "connect-dialog")
    every = [{**c, "organization_id": "org-1"} for c in CHANNELS]
    dialog_shot(ChooseAccounts(w, w.socials, every, "instagram"), "choose-accounts")
    t.connect_all()
    capture("accounts-connected", [ig.card])
    shared_key = len({s.service.connection()["key"] for s in w.socials}) == 1
    w.navigate("settings")
    t.reload()
    capture("settings-buffer-shared", [w.tiktok.client_setup, w.tiktok.hosting])

    w.navigate("videos")
    reel = t.import_in_page(t.reel, "reel.mp4")
    source = Path(reel.path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    w.videos.choose_upload("when", "now")
    w.videos.choose_upload("platform", "instagram")
    ig.ai.setCurrentIndex(ig.ai.findData(False))
    capture("videos-instagram-ready", [ig.video_panel])
    ig_post = t.instagram.publish(reel.id, ig.caption.toPlainText(), False, None, Event())
    t.reload()
    capture("videos-instagram-publishing", [ig.video_panel])
    t.buffer.send("post-1", "https://www.instagram.com/reel/abc/")
    t.now += timedelta(minutes=5)
    t.instagram.refresh(Event())
    t.reload()
    capture("videos-instagram-sent", [ig.video_panel])

    w.videos.choose_upload("when", "later")
    w.videos.choose_upload("platform", "facebook")
    capture("videos-facebook-schedule", [fb.video_panel])
    fb_post = t.facebook.publish(
        reel.id, fb.caption.toPlainText(), None, t.now + timedelta(hours=2), Event()
    )
    t.reload()
    capture("videos-facebook-scheduled", [fb.video_panel])

    # Same bytes again on Instagram: blocked. Facebook is an independent platform.
    copy = t.import_in_page(t.reel, "copy.mp4")
    duplicate_blocked = False
    try:
        t.instagram.publish(copy.id, "again", False, None, Event())
    except Exception as exc:  # noqa: BLE001
        duplicate_blocked = "Duplicate" in str(exc)

    small = t.import_in_page(t.small, "small.mp4")
    facebook_refuses_small = w.videos.fits["facebook"].text() == "Won't post"
    capture("videos-fits-small", [w.videos.fits["facebook"]])

    folder = t.root / "watched"
    folder.mkdir()
    scanner = w.watching.service
    folder_id = scanner.add(
        scanner.preview(str(folder), Event()), consent=True, existing=False, quiet=1
    )
    rule = Rule("UTC", tuple(range(7)), "09:00", "21:00", 2).encode()
    gated = []
    for service, ai in ((t.instagram, False), (t.facebook, None)):
        proposal = service.preview(
            {
                "name": f"{service.spec.name} reels",
                "folder_id": folder_id,
                "settings": dict(
                    caption_template="{filename}",
                    ai_generated=ai,
                    rule=rule,
                    daily_cap=2,
                    pattern="*.mp4",
                    background=False,
                ),
            }
        )
        consent = AutomationConsent(proposal, service.spec, w)
        gated.append(not consent.approve.isEnabled())
        dialog_shot(consent, f"automation-consent-{service.spec.key}")
        service.approve(proposal, scanner, Event())

    w.automation.folders = scanner.folders()
    w.automation.account = {}
    editor = AutoUploadEditor(w.automation, w.tiktok)
    editor.go(3)
    where_ready = all(editor.targets[k].isEnabled() for k in ("tiktok", "instagram", "facebook"))
    editor.resize(720, 720)
    dialog_shot(editor, "auto-upload-where")
    editor.go(1)
    dialog_shot(editor, "auto-upload-details")
    editor.go(2)
    dialog_shot(editor, "auto-upload-when")
    editor.dirty = False
    editor.reject()

    w.automatic.data = {"paused": 1}
    w.navigate("automation")
    t.reload()
    w.automation.render()
    capture("automation-paused-app-wide")
    # One card per auto upload; each platform's own line says whether it will post.
    headlines = [words for view in w.automation.views for _, words in view.lines]

    w.navigate("queue")
    t.reload()
    capture("queue-buffer", [ig.queue, fb.queue])
    w.navigate("dashboard")
    w.dashboard.refresh()
    capture("dashboard", [w.dashboard.buffer_cards["instagram"]])

    creates = t.buffer.creates
    result = {
        "buffer_creates": [
            {
                "channel": p["channelId"],
                "mode": p["mode"],
                "scheduling": p["schedulingType"],
                "metadata": p["metadata"],
            }
            for p in creates
        ],
        "hosting_uploads": t.cloud.uploads,
        "one_shared_sign_in": shared_key,
        "duplicate_blocked": duplicate_blocked,
        "facebook_refuses_small": facebook_refuses_small,
        "consent_initially_disabled": gated,
        "auto_upload_offers_every_buffer_platform": where_ready,
        "paused_headlines": headlines,
        "instagram_state": t.instagram.get(ig_post)["state"],
        "facebook_state": t.facebook.get(fb_post)["state"],
        "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == before,
        "max_horizontal_overflow": max(s["horizontal_overflow"] for s in sizes),
        "small_video": small.id,
    }
    (args.root / "window-sizes.json").write_text(json.dumps(sizes, indent=2))
    (args.root / "result.json").write_text(json.dumps(result, indent=2))
    assert [p["channel"] for p in result["buffer_creates"]] == ["ch-ig", "ch-fb"], result
    assert all(p["scheduling"] == "automatic" for p in result["buffer_creates"]), result
    assert result["buffer_creates"][0]["metadata"]["instagram"]["type"] == "reel", result
    assert result["buffer_creates"][1]["metadata"] == {"facebook": {"type": "reel"}}, result
    assert result["one_shared_sign_in"] and result["duplicate_blocked"], result
    assert result["facebook_refuses_small"] and all(gated), result
    assert result["auto_upload_offers_every_buffer_platform"], result
    assert not any("Active" in h for h in headlines), headlines
    assert result["instagram_state"] == "Sent by Buffer", result
    assert result["facebook_state"] == "Scheduled in Buffer", result
    assert result["source_unchanged"] and result["max_horizontal_overflow"] == 0, result
    print(
        "Stage 20 native Instagram/Facebook audit: PASS. Controlled Buffer/Cloudinary; "
        "no network, no real post."
    )
finally:
    t.doCleanups()
    SocialUiTests.tearDownClass()
