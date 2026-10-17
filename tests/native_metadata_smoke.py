"""Real native Stage 3 workflow in separate processes; only the OS picker result is supplied."""

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog
from support import make_video, settle

from shortspublisher.app import build

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("prepare", "reopen"))
parser.add_argument("--root", required=True, type=Path)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
source = root / "Publishing example 雪.mp4"
if args.phase == "prepare":
    make_video(source)
    (root / "original.sha256").write_text(hashlib.sha256(source.read_bytes()).hexdigest())
app, window = build(root / "data")
window.show()
window.navigate("videos")
page = window.videos
settle(app, page)
editor = page.editor


def capture(state: str) -> None:
    for width, height in ((1180, 780), (760, 560)):
        window.resize(width, height)
        app.processEvents()
        assert page.horizontalScrollBar().maximum() == 0
        page.verticalScrollBar().setValue(page.tabs.mapTo(page.widget(), QPoint(0, 0)).y())
        app.processEvents()
        assert window.grab().save(str(root / f"{state}-{width}-top.png"))
        page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
        app.processEvents()
        assert window.grab().save(str(root / f"{state}-{width}-bottom.png"))


if args.phase == "prepare":
    assert not page.records, "Use a new disposable root for this workflow"
    with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(source)], "Videos")):
        QTest.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    settle(app, page)
    assert page.selected is not None
    page.tabs.setCurrentIndex(1)
    editor.title.setText("A quiet moment 🎬")
    editor.description.setPlainText(
        "A small scene, made with care.\n\n雪 and light.\nhttps://example.com/story"
    )
    for tag in ("short film", "animation", "雪"):
        editor.tag_input.setText(tag)
        QTest.keyClick(editor.tag_input, Qt.Key.Key_Return)
    editor.kids.setCurrentIndex(2)
    editor.synthetic.setCurrentIndex(2)
    editor.privacy.setCurrentIndex(0)
    assert editor.dirty
    capture("editing")
    expected = asdict(editor.value())
    (root / "expected.json").write_text(json.dumps(expected, ensure_ascii=False))
    editor.save_button.click()
    settle(app, page)
    assert not editor.dirty
    assert "saved locally" in page.feedback.text()
    capture("saved")
    page.tabs.setCurrentIndex(2)
    settle(app, page)
    assert page.review_button.isEnabled(), page.review_errors.text()
    page.review_button.click()
    settle(app, page)
    assert "Review Complete" in page.review_state.text(), page.feedback.text()
    capture("review-complete")
else:
    assert len(page.records) == 1
    page.list.setCurrentRow(0)
    settle(app, page)
    expected = json.loads((root / "expected.json").read_text())
    assert json.loads(json.dumps(asdict(editor.value()))) == expected
    assert editor.preparation.reviewed_at
    page.tabs.setCurrentIndex(2)
    settle(app, page)
    assert "Review Complete" in page.review_state.text()
    capture("reopened-review")
    page.tabs.setCurrentIndex(1)
    editor.title.setText("A quiet moment — revised 🎬")
    assert "Draft" in page.review_state.text()
    editor.save_button.click()
    settle(app, page)
    assert not editor.preparation.reviewed_at
    page.tabs.setCurrentIndex(2)
    settle(app, page)
    assert "Draft" in page.review_state.text()
    capture("review-invalidated")
    page.review_button.click()
    settle(app, page)
    assert "Review Complete" in page.review_state.text()
    capture("review-again")
    # Show real validation errors, then restore and save the valid draft.
    page.tabs.setCurrentIndex(1)
    valid_title = editor.title.text()
    editor.title.setText("x" * 101)
    page.tabs.setCurrentIndex(2)
    settle(app, page)
    assert not page.review_button.isEnabled()
    assert "100 characters" in page.review_errors.text()
    capture("blocking-errors")
    editor.title.setText(valid_title)
    assert not editor.dirty
assert hashlib.sha256(source.read_bytes()).hexdigest() == (root / "original.sha256").read_text()
assert window.close()
settle(app, page)
assert not window.isVisible()
print(
    f"Native metadata {args.phase}: passed; exact metadata and review assertions passed; source unchanged."
)
