"""Reopen the actual user-operated picker fixture; no picker injection or live providers."""

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from automation_fixture import compose
from support import settle

from shortspublisher.app import build
from shortspublisher.background import LaunchAgent

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
root = parser.parse_args().root.resolve()
a = compose(root)
with (
    patch.object(LaunchAgent, "os_status", return_value={"loaded": False, "pid": 0}),
    patch("requests.sessions.Session.request", side_effect=AssertionError("No real HTTP")),
):
    app, w = build(
        root / "data",
        account_service=a.uploads.accounts,
        upload_service=a.uploads,
        schedule_service=a.schedules,
    )
    w.show()
    w.navigate("automation")
    for controller in (w.accounts, w.videos, w.uploads, w.schedules, w.watching, w.automation):
        settle(app, controller)
    folders = w.watching.service.folders()
    profiles = w.automation.service.state()["profiles"]
    expected = str(root / "incoming")
    assert len(folders) == len(profiles) == 1
    assert folders[0]["path"] == expected
    assert profiles[0]["folder_binding"]["path"] == expected
    assert profiles[0]["folder_binding"]["consent_at"] == folders[0]["consent_at"]
    assert [v.summary.startswith("Folder: incoming") for v in w.automation.views] == [True]
    assert not a.uploads.store.all()
    assert json.loads((root / "picker-cancel.json").read_text()) == {
        "cancel_created_folders": 0,
        "cancel_created_profiles": 0,
    }
    w.grab().save(str(root / "picker-reopened.png"))
    (root / "picker-result.json").write_text(
        json.dumps(
            {
                "canonical_path": expected,
                "same_consent_restored": True,
                "profile_restored": True,
                "upload_attempts": 0,
                "native_picker_selection": "user-operated; confirmed by user and persisted canonical path",
                "cancellation_created_nothing": True,
                "separate_process_reopen": True,
            },
            indent=2,
        )
    )
    for c in (w.automation, w.recurring, w.watching, w.automatic, w.background):
        c.stop_for_quit()
    for c in (w.accounts, w.videos, w.uploads, w.schedules):
        settle(app, c)
    w.close()
    app.processEvents()
print("Native human-picker persisted folder/profile reopen PASS; no real upload")
