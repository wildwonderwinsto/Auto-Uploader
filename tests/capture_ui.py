"""Render all pages at two sizes for manual visual review; no network or user data."""

import os
import tempfile
from pathlib import Path

from support import settle

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from shortspublisher.app import build
from shortspublisher.ui.window import PAGES

temporary = tempfile.TemporaryDirectory()
app, window = build(Path(temporary.name))
output = Path("artifacts")
output.mkdir(exist_ok=True)
window.show()
for size in ((1180, 780), (760, 560)):
    window.resize(*size)
    for page in PAGES:
        window.navigate(page.key)
        app.processEvents()
        if not window.grab().save(str(output / f"{page.key}-{size[0]}.png")):
            raise RuntimeError("Screenshot could not be saved")

settle(app, window.videos)
window.close()
temporary.cleanup()
