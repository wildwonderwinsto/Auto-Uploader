"""Native integration harness; each phase runs in a separate OS process.

The native picker boundary is supplied a generated fixture path; no inspection,
SQLite operation, worker, widget, or result is mocked. OS picker interaction remains
an explicit manual check. Run only with a disposable --root directory.
"""

import argparse
import hashlib
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox
from support import make_video, settle

from shortspublisher.app import build

parser = argparse.ArgumentParser()
parser.add_argument("phase", choices=("import", "reopen"))
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
root = args.root.resolve()
root.mkdir(parents=True, exist_ok=True)
source = root / "Native verification 雪.mp4"
if args.phase == "import":
    make_video(source)
    (root / "original.sha256").write_text(hashlib.sha256(source.read_bytes()).hexdigest())
app, window = build(root / "data")
window.show()
window.navigate("videos")
page = window.videos
settle(app, page)


def capture(name: str) -> None:
    for width, height in ((1180, 780), (760, 560)):
        window.resize(width, height)
        app.processEvents()
        assert page.horizontalScrollBar().maximum() == 0
        page.verticalScrollBar().setValue(0)
        assert window.grab().save(str(root / f"{name}-{width}.png"))
        if page.card.isVisible():
            page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
            app.processEvents()
            assert window.grab().save(str(root / f"{name}-details-{width}.png"))


if args.phase == "import":
    assert not page.records
    capture("empty")
    with patch.object(QFileDialog, "getOpenFileNames", return_value=([str(source)], "Videos")):
        QTest.mouseClick(page.import_button, Qt.MouseButton.LeftButton)
    assert page.worker is not None
    # Capture synchronously while the real worker is active; no simulated progress.
    assert window.grab().save(str(root / "inspecting-760.png"))
    settle(app, page)
    assert len(page.records) == 1, page.feedback.text()
    assert "160 × 240" in page.details.toPlainText()
    assert page.readiness.text() == "Ready for YouTube Shorts · local checks passed"
    capture("populated")
    silent = root / "Silent landscape fixture.webm"
    make_video(silent, audio=False, size="240x160")
    page.start("import", str(silent))
    settle(app, page)
    assert len(page.records) == 2
    assert "Needs attention" in page.readiness.text()
    assert "No audio" in page.warnings.text()
    assert "Landscape" in page.warnings.text()
    capture("warnings")
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        QTest.mouseClick(page.remove_button, Qt.MouseButton.LeftButton)
    settle(app, page)
    assert silent.exists()
    page.list.setCurrentRow(0)
    settle(app, page)
    broken = root / "Corrupt fixture.mp4"
    broken.write_bytes(b"intentionally invalid test fixture")
    page.start("import", str(broken))
    settle(app, page)
    assert "Operation failed" in page.feedback.text()
    capture("error")
    assert len(page.records) == 1
else:
    assert len(page.records) == 1, "Library did not survive process restart"
    page.list.setCurrentRow(0)
    settle(app, page)
    assert page.selected is not None
    assert "aac" in page.details.toPlainText()
    assert page.readiness.text() == "Ready for YouTube Shorts · local checks passed"
    capture("reopened")
    # Exercise the actual refresh action and persist the refreshed result.
    QTest.mouseClick(page.refresh_button, Qt.MouseButton.LeftButton)
    settle(app, page)
    assert "refreshed" in page.feedback.text()
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        QTest.mouseClick(page.remove_button, Qt.MouseButton.LeftButton)
    settle(app, page)
    assert not page.records
    assert not page.service.library.all()
    capture("removed")
assert hashlib.sha256(source.read_bytes()).hexdigest() == (root / "original.sha256").read_text()
assert window.close()
settle(app, page)
assert not window.isVisible()
print(f"Native phase {args.phase}: passed; source hash unchanged; window closed cleanly.")
