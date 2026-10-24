"""Native TikTok-via-Buffer audit: real Cocoa widgets, isolated SQLite/media, controlled Buffer and
Cloudinary. No network, no real credentials, no real TikTok post."""

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from threading import Event

from PySide6.QtWidgets import QScrollArea
from support import settle
from test_tiktok_ui import TikTokUiTests

from shortspublisher.cadence import Rule
from shortspublisher.ui.tiktok import ConnectDialog, TikTokAutomationEditor, TikTokConsent

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=True)

TikTokUiTests.setUpClass()
t = TikTokUiTests()
t.setUp()
try:
    w, app, c = t.w, t.app, t.c
    sizes = []

    def capture(route: str, name: str) -> None:
        for width, height in ((760, 560), (1180, 780), (1600, 1000)):
            w.resize(width, height)
            app.processEvents()
            target = w.stack.currentWidget()
            for position in ("top", "bottom"):
                if isinstance(target, QScrollArea):
                    bar = target.verticalScrollBar()
                    if position == "bottom":
                        bar.setValue(bar.maximum())
                    else:
                        bar.setValue(0)
                    # Scroll the TikTok section into view for pages where it sits low.
                    for widget in (c.card, c.hosting, c.queue):
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

    w.navigate("accounts")
    settle(app, c)
    capture("accounts", "accounts-not-connected")
    dialog = ConnectDialog(w)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(args.root / "connect-dialog.png"))
    dialog.close()
    t.connect()
    t.reload()
    capture("accounts", "accounts-connected")
    w.navigate("settings")
    settle(app, c)
    capture("settings", "settings-hosting-empty")
    t.host()
    t.reload()
    capture("settings", "settings-hosting-ready")

    video = t.video()
    source = Path(video.path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    w.navigate("videos")
    settle(app, w.videos)
    w.videos.start("select", video.id)
    settle(app, w.videos)
    w.videos.tabs.setCurrentIndex(5)
    t.reload()
    c.context(video.id)
    settle(app, c)
    c.caption.setPlainText("A quiet morning #shorts #fyp")
    c.ai.setCurrentIndex(2)
    capture("videos", "videos-tiktok-ready")
    post_now = t.service.publish(video.id, "A quiet morning #shorts #fyp", False, None, Event())
    t.reload()
    capture("videos", "videos-tiktok-publishing")
    t.buffer.send("post-1")
    t.now += timedelta(minutes=5)
    t.service.refresh(Event())
    t.reload()
    capture("videos", "videos-tiktok-sent")

    second = t.video("second.mp4")
    # Same bytes as the first video: must be blocked as a duplicate for this TikTok account.
    duplicate_blocked = False
    try:
        t.service.publish(second.id, "again", False, None, Event())
    except Exception as exc:  # noqa: BLE001
        duplicate_blocked = "Duplicate" in str(exc)

    folder = t.root / "watched"
    folder.mkdir()
    scanner = w.watching.service
    folder_id = scanner.add(
        scanner.preview(str(folder), Event()), consent=True, existing=False, quiet=1
    )
    editor = TikTokAutomationEditor(c)
    for index in range(editor.steps.count()):
        editor.steps.setCurrentIndex(index)
        editor.show()
        app.processEvents()
        editor.grab().save(str(args.root / f"automation-editor-{index + 1}.png"))
    editor.close()
    settings = dict(
        caption_template="{filename} #shorts",
        ai_generated=False,
        rule=Rule("UTC", tuple(range(7)), "09:00", "21:00", 2).encode(),
        daily_cap=3,
        pattern="*.mp4",
        background=False,
    )
    proposal = t.service.preview(
        {"name": "Morning clips", "folder_id": folder_id, "settings": settings}
    )
    consent = TikTokConsent(proposal, w)
    consent.show()
    app.processEvents()
    consent.grab().save(str(args.root / "automation-consent.png"))
    consent_gated = not consent.approve.isEnabled()
    consent.close()
    t.service.approve(proposal, scanner, Event())
    w.automatic.data = {"paused": 1}
    w.navigate("automation")
    t.reload()
    capture("automation", "automation-paused-app-wide")
    w.automation.render()
    paused_headline = " ".join(words for v in w.automation.views for _, words in v.lines)

    w.navigate("queue")
    t.reload()
    capture("queue", "queue-tiktok")

    result = {
        "buffer_creates": len(t.buffer.creates),
        "modes": [p["mode"] for p in t.buffer.creates],
        "scheduling_types": [p["schedulingType"] for p in t.buffer.creates],
        "hosting_uploads": t.cloud.uploads,
        "duplicate_blocked": duplicate_blocked,
        "consent_initially_disabled": consent_gated,
        "paused_headline": paused_headline,
        "final_state": t.service.get(post_now)["state"],
        "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest() == before,
        "max_horizontal_overflow": max(s["horizontal_overflow"] for s in sizes),
    }
    (args.root / "window-sizes.json").write_text(json.dumps(sizes, indent=2))
    (args.root / "result.json").write_text(json.dumps(result, indent=2))
    assert result["buffer_creates"] == 1 and result["duplicate_blocked"], result
    assert result["consent_initially_disabled"] and result["source_unchanged"], result
    assert "Active" not in paused_headline, paused_headline
    assert result["max_horizontal_overflow"] == 0, result
    assert result["final_state"] == "Sent by Buffer", result
    print(
        "Stage 19 native TikTok audit: PASS. Controlled Buffer/Cloudinary; no network, no real post."
    )
finally:
    t.doCleanups()
    TikTokUiTests.tearDownClass()
