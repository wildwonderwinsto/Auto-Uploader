"""Build ShortsScheduler.app, with its icon, for this project folder.

The app is a small launcher: it opens ShortsScheduler from this folder's Python environment
(.venv), backing up the library first. Keep the folder where it is, or run this again after
moving it. install.sh runs this for you.

    .venv/bin/python scripts/make_app.py              # ./ShortsScheduler.app
    .venv/bin/python scripts/make_app.py --applications   # ~/Applications/ShortsScheduler.app
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "ShortsScheduler"
ICON = ROOT / "assets" / f"{NAME}.icns"
PNG = ROOT / "assets" / "icon.png"
# The bundle's executable must be a native program, or Apple silicon Macs ask to install Rosetta;
# this one (scripts/launcher.c, arm64 + x86_64) just runs Resources/launch.sh.
NATIVE = ROOT / "assets" / "launcher"
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
    "/Support/lsregister"
)
LAUNCHER = """#!/bin/zsh
# ShortsScheduler: opens the app from its project folder (made by scripts/make_app.py; run by the
# bundle's native launcher).
ROOT={root}
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"  # FFprobe from Homebrew
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  /usr/bin/osascript -e 'display alert "ShortsScheduler can’t start" message "Its project folder \
or Python environment is missing. Open Terminal in the project folder, run ./install.sh, then try \
again."'
  exit 1
fi
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m shortspublisher --backup
"""


def render_icon() -> None:
    """The scheduler mark as an .icns (every size macOS asks for) and a PNG for the README."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "src"))
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    from shortspublisher.ui.icons import app_svg

    _app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(QByteArray(app_svg()))

    def image(side: int) -> QImage:
        result = QImage(side, side, QImage.Format.Format_ARGB32)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, side, side))
        painter.end()
        return result

    ICON.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        iconset = Path(temp) / f"{NAME}.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            image(size).save(str(iconset / f"icon_{size}x{size}.png"))
            image(size * 2).save(str(iconset / f"icon_{size}x{size}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICON)], check=True)
    image(512).save(str(PNG))


def build(destination: Path) -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    app = destination / f"{NAME}.app"
    if app.exists():
        shutil.rmtree(app)  # only ever a bundle this script made
    macos, resources = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    shutil.copy2(NATIVE, macos / NAME)
    (resources / "launch.sh").write_text(LAUNCHER.format(root=shlex.quote(str(ROOT))))
    shutil.copy2(ICON, resources / f"{NAME}.icns")
    with open(app / "Contents" / "Info.plist", "wb") as plist:
        plistlib.dump(
            {
                "CFBundleName": NAME,
                "CFBundleDisplayName": NAME,
                "CFBundleIdentifier": "com.shortspublisher.scheduler",
                "CFBundleExecutable": NAME,
                "CFBundleIconFile": NAME,
                "CFBundlePackageType": "APPL",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": version,
                "LSMinimumSystemVersion": "12.0",
                "LSApplicationCategoryType": "public.app-category.productivity",
                "NSHighResolutionCapable": True,
            },
            plist,
        )
    # A local ad-hoc signature (no Apple account needed) keeps macOS's view of the app consistent.
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=False)
    # Finder and the Dock cache icons; tell them about this bundle.
    subprocess.run([LSREGISTER, "-f", str(app)], check=False)
    os.utime(app)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--applications", action="store_true", help="Put the app in ~/Applications")
    args = parser.parse_args()
    render_icon()
    destination = Path.home() / "Applications" if args.applications else ROOT
    destination.mkdir(exist_ok=True)
    print(f"Built {build(destination)}")


if __name__ == "__main__":
    main()
