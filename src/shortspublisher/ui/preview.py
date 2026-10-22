"""Local playback. Preview is never used as inspection or upload evidence.

Selecting a video shows its first frame (decoded muted, then paused); nothing plays until asked.
"""

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QHideEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPixmap,
    QShowEvent,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from . import theme
from .icons import icon, pixmap


class PreviewSurface(QLabel):
    """Qt-owned frame surface, including in native widget captures. Click to play or pause."""

    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.sink = QVideoSink(self)
        self.frame = QPixmap()
        self.playing = False
        self.message = "Select a video"
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sink.videoFrameChanged.connect(self.present)

    def videoSink(self) -> QVideoSink:
        return self.sink

    def present(self, frame: QVideoFrame) -> None:
        self.frame = QPixmap.fromImage(frame.toImage()) if frame.isValid() else QPixmap()
        self.update()

    def render_frame(self) -> None:
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(260, 462)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 14, 14)
        painter.setClipPath(clip)
        painter.fillRect(bounds, QColor("#05060a"))
        if not self.frame.isNull():
            scaled = self.frame.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x = (self.width() - scaled.width()) / 2
            y = (self.height() - scaled.height()) / 2
            painter.drawPixmap(
                QRectF(x, y, scaled.width(), scaled.height()), self.frame, QRectF(self.frame.rect())
            )
        if not self.playing:
            # A round play button in the middle while paused or empty.
            center = bounds.center()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 140))
            painter.drawEllipse(center, 30, 30)
            glyph = pixmap("play", 26, "#ffffff")
            painter.drawPixmap(int(center.x() - 11), int(center.y() - 13), glyph)
            if self.frame.isNull() and self.message:
                painter.setPen(QColor(theme.MUTED))
                text_box = QRectF(16, center.y() + 42, bounds.width() - 32, 60)
                painter.drawText(
                    text_box,
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap),
                    self.message,
                )


class VideoPreview(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.path = ""
        self.priming = False
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.video = PreviewSurface()
        self.video.setMinimumSize(160, 284)
        self.video.clicked.connect(self.toggle)
        self.player.setVideoSink(self.video.sink)
        self.play = QPushButton()
        self.play.setObjectName("Ghost")
        self.play.setIconSize(QSize(18, 18))
        self.play.setAccessibleName("Play preview")
        self.play.clicked.connect(self.toggle)
        self.mute = QPushButton()
        self.mute.setObjectName("Ghost")
        self.mute.setIconSize(QSize(18, 18))
        self.mute.clicked.connect(self.toggle_mute)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("Preview position")
        self.seek.setEnabled(False)
        self.seek.setToolTip("Drag or use the arrow keys to move through the video.")
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.seek.actionTriggered.connect(
            lambda _: self.player.setPosition(self.seek.sliderPosition())
        )
        self.time = QLabel("0:00 / 0:00")
        self.time.setObjectName("Subtle")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("Subtle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        # Kept for callers that show or hide it; the surface paints its own empty state.
        self.placeholder = QLabel()
        self.placeholder.hide()
        layout.addWidget(self.video, 1)
        controls = QHBoxLayout()
        controls.setSpacing(4)
        controls.addWidget(self.play)
        controls.addWidget(self.seek, 1)
        controls.addWidget(self.time)
        controls.addWidget(self.mute)
        layout.addLayout(controls)
        layout.addWidget(self.status)
        self.player.positionChanged.connect(self.position)
        self.player.durationChanged.connect(self.duration)
        self.player.playbackStateChanged.connect(self.state)
        self.player.errorOccurred.connect(self.error)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.seekableChanged.connect(self.seek.setEnabled)
        self.video.sink.videoFrameChanged.connect(self.primed)
        self.play.setEnabled(False)
        self.mute.setEnabled(False)
        self.state(QMediaPlayer.PlaybackState.StoppedState)
        self.show_mute()
        self.set_message("Select a video to preview")

    def set_message(self, message: str) -> None:
        self.status.setText(message)
        self.video.message = message
        self.video.update()

    def show_mute(self) -> None:
        muted = self.audio.isMuted()
        self.mute.setIcon(icon("mute" if muted else "volume", theme.MUTED))
        self.mute.setToolTip("Turn sound on" if muted else "Turn sound off")
        self.mute.setAccessibleName("Unmute" if muted else "Mute")

    def set_source(self, path: str) -> None:
        if path == self.path:
            if path and self.player.source().isEmpty() and self.isVisible():
                self.load_poster()
            return
        self.release()
        self.path = path
        self.play.setEnabled(bool(path))
        self.mute.setEnabled(bool(path))
        self.audio.setMuted(True)
        self.show_mute()
        self.set_message("Sound starts off" if path else "Select a video to preview")
        if path and self.isVisible():
            self.load_poster()

    def load_poster(self) -> None:
        """Decode just the first frame, muted, so the video is visible straight away."""
        if not self.path or not Path(self.path).is_file():
            return
        self.priming = True
        self.audio.setMuted(True)
        self.player.setSource(QUrl.fromLocalFile(str(Path(self.path).absolute())))

    def primed(self, frame: QVideoFrame) -> None:
        if self.priming and frame.isValid():
            self.priming = False
            self.player.pause()
            self.player.setPosition(0)

    def toggle(self) -> None:
        if (
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and not self.priming
        ):
            self.player.pause()
        elif self.path:
            if not Path(self.path).is_file():
                self.set_message(
                    "File not found. Put it back in its saved location, then press play again."
                )
                return
            self.priming = False
            if self.player.source().isEmpty():
                # The source is a library path, never a network URL.
                self.player.setSource(QUrl.fromLocalFile(str(Path(self.path).absolute())))
            self.player.play()

    def media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if self.player.source().isEmpty():
            return
        if status in (
            QMediaPlayer.MediaStatus.LoadingMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
        ):
            self.set_message("Loading preview…")
        elif status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            if self.priming and status == QMediaPlayer.MediaStatus.LoadedMedia:
                self.player.play()
            self.set_message("Sound off" if self.audio.isMuted() else "Sound on")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.set_message("Preview finished. Press play to watch again.")

    def toggle_mute(self) -> None:
        self.audio.setMuted(not self.audio.isMuted())
        self.show_mute()
        self.set_message("Sound off" if self.audio.isMuted() else "Sound on")

    def position(self, position: int) -> None:
        if self.player.source().isEmpty():
            self.time.setText("0:00 / 0:00")
            return
        if not self.seek.isSliderDown():
            self.seek.setValue(position)

        def stamp(ms: int) -> str:
            seconds = ms // 1000
            return f"{seconds // 60}:{seconds % 60:02d}"

        self.time.setText(f"{stamp(position)} / {stamp(self.player.duration())}")

    def duration(self, duration: int) -> None:
        self.seek.setRange(0, duration if not self.player.source().isEmpty() else 0)
        self.position(self.player.position())

    def state(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState and not self.priming
        self.video.playing = playing
        self.video.update()
        self.play.setIcon(icon("pause" if playing else "play", theme.TEXT))
        self.play.setToolTip("Pause" if playing else "Play")
        self.play.setAccessibleName("Pause preview" if playing else "Play preview")

    def error(self, error: QMediaPlayer.Error, message: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            self.priming = False
            self.seek.setEnabled(False)
            self.video.present(QVideoFrame())
            self.set_message(
                "This file could not be played. It may be missing, damaged or use an unsupported format. File inspection is separate from preview playback."
            )

    def release(self) -> None:
        self.priming = False
        # Clearing the source also stops playback. An explicit stop() straight after a source
        # change can deadlock Qt's FFmpeg backend (for example when switching videos quickly).
        self.player.setSource(QUrl())
        self.video.present(QVideoFrame())
        self.seek.setRange(0, 0)
        self.seek.setEnabled(False)
        self.time.setText("0:00 / 0:00")
        self.audio.setMuted(True)
        self.show_mute()
        self.state(QMediaPlayer.PlaybackState.StoppedState)
        self.set_message(
            "Press play to watch · sound starts off" if self.path else "Select a video to preview"
        )

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self.path and self.player.source().isEmpty():
            self.load_poster()

    def hideEvent(self, event: QHideEvent) -> None:
        self.release()
        super().hideEvent(event)
