"""Native controlled-provider workflow; real SQLite and isolated macOS Keychain.

This does not open Google's login page or assert live Google authorization.
"""

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from account_support import CHANNEL, CONFIG, ControlledOAuth, ControlledYouTube
from PySide6.QtWidgets import QFileDialog, QMessageBox
from support import settle

from shortspublisher.accounts import AccountRepository, AccountService
from shortspublisher.app import build
from shortspublisher.auth import AccountError
from shortspublisher.credentials import KeychainStore
from shortspublisher.library import Library

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("prepare", "reopen"))
parser.add_argument("--root", required=True, type=Path)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
library = Library(root / "data" / "library.sqlite3")
credentials = KeychainStore("ShortsPublisher.Stage4.NativeVerification")
oauth = ControlledOAuth()
youtube = ControlledYouTube()
service = AccountService(AccountRepository(library), credentials, oauth, youtube)
app, window = build(root / "data", account_service=service)
window.show()
window.navigate("accounts")
page = window.accounts
settle(app, page)


def capture(name):
    for width, height in ((1180, 780), (760, 560)):
        window.resize(width, height)
        app.processEvents()
        assert page.horizontalScrollBar().maximum() == 0
        for position in ("top", "bottom"):
            page.verticalScrollBar().setValue(
                0 if position == "top" else page.verticalScrollBar().maximum()
            )
            app.processEvents()
            assert window.grab().save(str(root / f"{name}-{width}-{position}.png"))


if args.phase == "prepare":
    assert service.repository.connection() is None, "Use a fresh isolated root"
    capture("disconnected")
    page.connect_button.click()
    settle(app, page)
    assert "JSON" in page.feedback.text()
    config_path = root / "controlled-client.json"
    config_path.write_text(json.dumps(CONFIG.document()))
    with patch.object(QFileDialog, "getOpenFileName", return_value=(str(config_path), "JSON")):
        page.configure_button.click()
    settle(app, page)
    config_path.unlink()
    assert page.view.configured
    oauth.wait = True
    page.connect_button.click()
    assert oauth.entered.wait(2)
    app.processEvents()
    capture("connecting-controlled")
    page.cancel_button.click()
    settle(app, page)
    assert page.status.text() == "Not connected"
    capture("cancelled")
    oauth.wait = False
    page.connect_button.click()
    settle(app, page)
    assert page.view.choices
    capture("confirm-controlled-channel")
    page.confirm_button.click()
    settle(app, page)
    assert page.status.text().startswith("Connected"), page.feedback.text()
    assert CHANNEL.id in page.technical.text()
    capture("connected-controlled")
else:
    assert page.status.text().startswith("Connected"), page.feedback.text()
    assert page.view.connection.channel == CHANNEL
    assert credentials.get(page.view.connection.key) is not None
    capture("reopened-controlled")
    youtube.error = AccountError("Controlled offline test. Cached details retained.", "offline")
    page.refresh_button.click()
    settle(app, page)
    assert page.status.text() == "Offline"
    capture("offline-controlled")
    youtube.error = None
    page.refresh_button.click()
    settle(app, page)
    assert page.status.text().startswith("Connected")
    removed_key = page.view.connection.key
    with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes):
        page.disconnect_button.click()
    settle(app, page)
    assert page.status.text() == "Not connected"
    assert service.repository.connection() is None
    assert credentials.get(removed_key) is None
    capture("disconnected-after-removal")
    oauth.wait = True
    page.connect_button.click()
    assert oauth.entered.wait(2)
    assert not window.close()
    settle(app, page)
assert window.close()
settle(app, page)
assert not window.isVisible()
for path in root.rglob("*"):
    if path.is_file() and path.suffix != ".png":
        content = path.read_bytes()
        assert b"test-access-sensitive" not in content
        assert b"test-refresh-sensitive" not in content
print(
    f"Native Accounts {args.phase}: passed with controlled OAuth/API and real isolated Keychain; no live Google authorization."
)
