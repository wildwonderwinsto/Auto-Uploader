"""Line icons and platform logos drawn as small inline SVGs; no image files or emoji."""

from functools import cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel

# 24 × 24 outline glyphs, stroked in the requested colour. "fill:" marks solid shapes.
GLYPHS = {
    "dashboard": '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7"'
    ' height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3"'
    ' y="16" width="7" height="5" rx="1.5"/>',
    "videos": '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M7.5 3v18M16.5 3v18'
    'M3 8h4.5M3 12h18M3 16h4.5M16.5 8H21M16.5 16H21"/>',
    "queue": '<path d="M9 6h12M9 12h12M9 18h12"/><path d="M4 5.5l1 1 1.8-2M4 11.5l1 1 1.8-2"/>'
    '<circle cx="5" cy="18" r="1.2"/>',
    "calendar": '<rect x="3" y="4.5" width="18" height="16.5" rx="2.5"/><path d="M3 9.5h18'
    'M8 2.5v4M16 2.5v4"/><path d="M7.5 13.5h2M11 13.5h2M14.5 13.5h2M7.5 17h2M11 17h2"/>',
    "auto": '<path d="M13 2.5 4.5 13.5H11l-1 8 8.5-11H12z"/>',
    "accounts": '<circle cx="9" cy="8" r="3.6"/><path d="M2.8 20.5a6.2 6.2 0 0 1 12.4 0"/>'
    '<path d="M15.5 4.6a3.6 3.6 0 0 1 0 6.8M17.5 14.6a6.2 6.2 0 0 1 3.7 5.9"/>',
    "settings": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6"'
    ' r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "upload": '<path d="M12 15.5V3.5M7 8.5l5-5 5 5"/><path d="M4 15v3.5A2.5 2.5 0 0 0 6.5 21h11'
    'a2.5 2.5 0 0 0 2.5-2.5V15"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "folder": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.7l2.3 2.5h7A2.5 2.5 0 0 1 21 10v7.5a2.5'
    ' 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
    "play": 'fill:<path d="M8 5.2v13.6a.8.8 0 0 0 1.2.7l10.6-6.8a.8.8 0 0 0 0-1.4L9.2 4.5A.8.8'
    ' 0 0 0 8 5.2z"/>',
    "pause": 'fill:<rect x="6" y="4.5" width="4" height="15" rx="1.2"/><rect x="14" y="4.5"'
    ' width="4" height="15" rx="1.2"/>',
    "volume": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4.5 4.5 0 0 1 0 6'
    'M18.5 6.5a8 8 0 0 1 0 11"/>',
    "mute": '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.8 2.8L16.2 9.5"/>',
    "alert": '<path d="M10.3 4.2 2.6 17.6A2 2 0 0 0 4.3 20.6h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2'
    ' 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 17h.01"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20.5 4v4.8h-4.8"/>',
    "trash": '<path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 11v5M14 11v5"/>',
    "edit": '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "chevron-right": '<path d="M9 5l7 7-7 7"/>',
    "chevron-left": '<path d="M15 5l-7 7 7 7"/>',
    "chevron-up": '<path d="M5 15l7-7 7 7"/>',
    "chevron-down": '<path d="M5 9l7 7 7-7"/>',
    "eye": '<path d="M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7-9.5-7-9.5-7z"/><circle cx="12"'
    ' cy="12" r="3"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11'
    'A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    "power": '<path d="M12 3v8.5"/><path d="M6.3 6.6a8 8 0 1 0 11.4 0"/>',
    "link": '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14'
    ' 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"/>',
    "logout": '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l-5-5 5-5M5 12h11"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5h.01"/>',
    "history": '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6L3.5 8.5"/><path d="M3.5 3.5v5h5"/><path'
    ' d="M12 7.5V12l3 2"/>',
    "chart": '<path d="M3 20.5h18M6.5 17v-6M11 17V6.5M15.5 17v-4M20 17V9"/>',
    "heart": '<path d="M12 20s-7.5-4.6-9-9.3A4.8 4.8 0 0 1 12 6a4.8 4.8 0 0 1 9 4.7C19.5 15.4'
    ' 12 20 12 20z"/>',
    "tag": '<path d="M3 11.5V4a1 1 0 0 1 1-1h7.5l9.5 9.5-8.5 8.5z"/><circle cx="7.5" cy="7.5"'
    ' r="1.5"/>',
    "send": '<path d="M21 3 10.5 13.5M21 3l-6.5 18-4-7.5L3 9.5z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14'
    ' 14 0 0 0 0 18"/>',
    "lock": '<rect x="4.5" y="10.5" width="15" height="10.5" rx="2.5"/><path d="M8 10.5V7.5a4'
    ' 4 0 0 1 8 0v3"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.8-3.4 8-8 9-4.6-1-8-4.2-8-9V6z"/><path d="M8.8 12.2l2.2'
    ' 2.2 4.2-4.6"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12.5" rx="2"/><path d="M8.5 20.5h7M12'
    ' 16.5v4"/>',
    "bell": '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15z"/><path d="M10 21h4"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="9" cy="9" r="2"/>'
    '<path d="M21 15.5l-5-5L5 21"/>',
    "hourglass": '<path d="M6.5 3h11M6.5 21h11M7.5 3c0 5 9 6 9 9s-9 4-9 9M16.5 3c0 5-9 6-9 9s9'
    ' 4 9 9"/>',
    "list": '<path d="M8.5 6H21M8.5 12H21M8.5 18H21"/><circle cx="4" cy="6" r=".8"/><circle'
    ' cx="4" cy="12" r=".8"/><circle cx="4" cy="18" r=".8"/>',
    "users": '<circle cx="9" cy="8" r="3.6"/><path d="M2.8 20.5a6.2 6.2 0 0 1 12.4 0"/>',
    "film": '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M7.5 3v18M16.5 3v18'
    'M3 12h18"/>',
    "dot": 'fill:<circle cx="12" cy="12" r="5"/>',
}

TIKTOK_NOTE = (
    "M13.5 4.5h2.6c.2 1.7 1.4 3 3.2 3.2v2.6c-1.2 0-2.3-.4-3.2-1v5.6a4.6 4.6 0 1 1-4.6-4.6"
    "c.2 0 .5 0 .7.1v2.7a2 2 0 1 0 1.3 1.8z"
)
SNAP_GHOST = (
    "M12 4.6c2.6 0 4.4 2 4.4 4.6v2.1l1.6-.5c.5-.1.8.5.4.8l-1.6 1c.6 1.6 1.8 2.8 3.4 3.2-.3.6"
    "-1.3 1-2.4 1.1l-.4 1.1-1.6-.2c-.8.4-1.7 1.6-3.8 1.6s-3-1.2-3.8-1.6l-1.6.2-.4-1.1c-1.1-.1"
    "-2.1-.5-2.4-1.1 1.6-.4 2.8-1.6 3.4-3.2l-1.6-1c-.4-.3-.1-.9.4-.8l1.6.5V9.2c0-2.6 1.8-4.6"
    " 4.4-4.6z"
)
LOGOS = {
    "youtube": '<rect x="1" y="4.5" width="22" height="15" rx="4.6" fill="#FF0033"/>'
    '<path d="M9.8 8.6v6.8l5.9-3.4z" fill="#ffffff"/>',
    "tiktok": '<rect width="24" height="24" rx="6" fill="#000000"/><g transform="translate(-1.1'
    ' 0)"><path d="'
    + TIKTOK_NOTE
    + '" fill="#25F4EE" transform="translate(-.7 -.6)"/><path d="'
    + TIKTOK_NOTE
    + '" fill="#FE2C55" transform="translate(.7 .6)"/><path d="'
    + TIKTOK_NOTE
    + '" fill="#ffffff"/></g>',
    "instagram": '<defs><linearGradient id="g" x1="0" y1="1" x2="1" y2="0"><stop offset="0"'
    ' stop-color="#FEDA75"/><stop offset=".3" stop-color="#FA7E1E"/><stop offset=".6"'
    ' stop-color="#D62976"/><stop offset=".85" stop-color="#962FBF"/><stop offset="1"'
    ' stop-color="#4F5BD5"/></linearGradient></defs><rect width="24" height="24" rx="6"'
    ' fill="url(#g)"/><rect x="5" y="5" width="14" height="14" rx="4.2" fill="none"'
    ' stroke="#fff" stroke-width="1.8"/><circle cx="12" cy="12" r="3.3" fill="none"'
    ' stroke="#fff" stroke-width="1.8"/><circle cx="16.3" cy="7.7" r="1.1" fill="#fff"/>',
    "facebook": '<circle cx="12" cy="12" r="12" fill="#0866FF"/><path d="M13.4 24v-9.3h3.1'
    "l.5-3.6h-3.6V8.8c0-1 .3-1.8 1.8-1.8h1.9V3.8c-.3 0-1.5-.1-2.8-.1-2.8 0-4.6 1.7-4.6 4.8v2.6"
    'H6.6v3.6h3.1V24z" fill="#ffffff"/>',
    "snapchat": '<rect width="24" height="24" rx="6" fill="#FFFC00"/><path d="'
    + SNAP_GHOST
    + '" fill="#ffffff" stroke="#000000" stroke-width="1.1" stroke-linejoin="round"/>',
}
PLATFORM_NAMES = {
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "snapchat": "Snapchat",
}
# The app mark: a play button inside a calendar page.
MARK = (
    '<defs><linearGradient id="m" x1="0" y1="0" x2="1" y2="1"><stop offset="0"'
    ' stop-color="#6ea2ff"/><stop offset="1" stop-color="#3b6fe8"/></linearGradient></defs>'
    '<rect width="24" height="24" rx="6.5" fill="url(#m)"/><path d="M6.5 7.5h11M9 4.8v3.4'
    'M15 4.8v3.4" stroke="#ffffff" stroke-width="1.6" stroke-linecap="round" opacity=".8"/>'
    '<path d="M10 11.2v6.6l5.6-3.3z" fill="#ffffff"/>'
)


def _svg(body: str) -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' + body + "</svg>"
    ).encode()


def _glyph(name: str, color: str) -> bytes:
    body = GLYPHS[name]
    if body.startswith("fill:"):
        return _svg(f'<g fill="{color}" stroke="none">{body[5:]}</g>')
    return _svg(
        f'<g fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round"'
        f' stroke-linejoin="round">{body}</g>'
    )


def _render(data: bytes, size: int, ratio: float = 2.0) -> QPixmap:
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(data)).render(painter, QRectF(0, 0, pixmap.width(), pixmap.height()))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


@cache
def pixmap(name: str, size: int = 18, color: str = "#eceef3") -> QPixmap:
    return _render(_glyph(name, color), size)


@cache
def icon(name: str, color: str = "#c9cdd6", active: str = "") -> QIcon:
    """An icon whose checked/selected state can use a second colour (sidebar)."""
    result = QIcon()
    for size in (16, 20, 24):
        result.addPixmap(pixmap(name, size, color), QIcon.Mode.Normal, QIcon.State.Off)
        result.addPixmap(pixmap(name, size, "#6b7280"), QIcon.Mode.Disabled, QIcon.State.Off)
        if active:
            result.addPixmap(pixmap(name, size, active), QIcon.Mode.Normal, QIcon.State.On)
            result.addPixmap(pixmap(name, size, active), QIcon.Mode.Active, QIcon.State.On)
            result.addPixmap(pixmap(name, size, active), QIcon.Mode.Selected, QIcon.State.On)
    return result


@cache
def logo(platform: str, size: int = 28) -> QPixmap:
    return _render(_svg(LOGOS[platform]), size)


@cache
def logo_icon(platform: str) -> QIcon:
    result = QIcon()
    for size in (16, 20, 24, 32):
        result.addPixmap(logo(platform, size))
    return result


@cache
def mark(size: int = 30) -> QPixmap:
    return _render(_svg(MARK), size)


# The app icon: the same idea as the mark (a play button on a calendar page), drawn for the
# Dock on Apple's 1024 grid (an 824 square, centred).
APP_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><defs>'
    '<linearGradient id="b" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6ea2ff"/>'
    '<stop offset="1" stop-color="#3159d6"/></linearGradient>'
    '<linearGradient id="p" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/>'
    '<stop offset="1" stop-color="#e9efff"/></linearGradient>'
    '<filter id="s" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="14"'
    ' stdDeviation="18" flood-color="#0b1f5c" flood-opacity=".35"/></filter></defs>'
    '<rect x="100" y="100" width="824" height="824" rx="186" fill="url(#b)"/>'
    '<g filter="url(#s)"><rect x="238" y="268" width="548" height="500" rx="70" fill="url(#p)"/>'
    '<path d="M238 338a70 70 0 0 1 70-70h408a70 70 0 0 1 70 70v62H238z" fill="#1d2f5e"/></g>'
    '<rect x="350" y="214" width="46" height="116" rx="23" fill="#ffffff"/>'
    '<rect x="628" y="214" width="46" height="116" rx="23" fill="#ffffff"/>'
    '<path d="M460 482v196l166-98z" fill="#3b6fe8" stroke="#3b6fe8" stroke-width="36"'
    ' stroke-linejoin="round"/></svg>'
)


def app_svg() -> bytes:
    return APP_ICON.encode()


@cache
def app_icon() -> QIcon:
    """Window and Dock icon (the .app bundle carries the same artwork as an .icns file)."""
    result = QIcon()
    for size in (16, 32, 64, 128, 256, 512):
        result.addPixmap(_render(app_svg(), size))
    return result


def image(source: QPixmap, size: int | None = None) -> QLabel:
    """A transparent, fixed-size label showing an icon or logo."""
    label = QLabel()
    label.setObjectName("Glyph")
    label.setPixmap(source)
    side = size or round(source.width() / source.devicePixelRatio())
    label.setFixedSize(QSize(side, side))
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label
