"""Local, opt-in playback. Preview is never used as inspection or upload evidence."""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QHideEvent, QPixmap, QResizeEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget


class PreviewSurface(QLabel):
    """Qt-owned frame surface, including in native widget captures."""

    def __init__(self) -> None:
        super().__init__()
        self.sink = QVideoSink(self)
        self.frame = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sink.videoFrameChanged.connect(self.present)

    def videoSink(self) -> QVideoSink:
        return self.sink

    def present(self, frame: QVideoFrame) -> None:
        self.frame = QPixmap.fromImage(frame.toImage()) if frame.isValid() else QPixmap()
        self.render_frame()

    def render_frame(self) -> None:
        if self.frame.isNull():
            self.clear()
        else:
            self.setPixmap(
                self.frame.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.render_frame()


class VideoPreview(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.path = ""
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.video = PreviewSurface()
        self.video.setMinimumSize(160, 220)
        self.video.setMaximumHeight(360)
        self.player.setVideoSink(self.video.sink)
        self.play = QPushButton("Play preview")
        self.play.clicked.connect(self.toggle)
        self.mute = QPushButton("Unmute")
        self.mute.clicked.connect(self.toggle_mute)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("Preview position")
        self.seek.setEnabled(False)
        self.seek.setToolTip(
            "Play the preview to load it, then drag or use the arrow keys to seek."
        )
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.seek.actionTriggered.connect(
            lambda _: self.player.setPosition(self.seek.sliderPosition())
        )
        self.time = QLabel("0:00 / 0:00")
        self.status = QLabel("Local preview · muted")
        self.status.setWordWrap(True)
        self.status.setObjectName("Muted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.placeholder = QLabel("Video preview\nSelect Play preview to watch the local file")
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setMinimumHeight(220)
        layout.addWidget(self.placeholder)
        layout.addWidget(self.video)
        self.video.hide()
        layout.addWidget(self.seek)
        controls = QHBoxLayout()
        controls.addWidget(self.play)
        controls.addWidget(self.mute)
        layout.addLayout(controls)
        layout.addWidget(self.time)
        layout.addWidget(self.status)
        self.player.positionChanged.connect(self.position)
        self.player.durationChanged.connect(self.duration)
        self.player.playbackStateChanged.connect(self.state)
        self.player.errorOccurred.connect(self.error)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.seekableChanged.connect(self.seek.setEnabled)
        self.play.setEnabled(False)
        self.mute.setEnabled(False)
        self.status.setText("Select a video to preview")

    def set_source(self, path: str) -> None:
        if path == self.path:
            return
        self.release()
        self.path = path
        self.play.setEnabled(bool(path))
        self.mute.setEnabled(bool(path))
        self.status.setText("Local preview · muted" if path else "Select a video to preview")
        self.audio.setMuted(True)
        self.mute.setText("Unmute")

    def toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.path:
            if not Path(self.path).is_file():
                self.status.setText(
                    "File not found. Restore it to its saved location, then try Play preview again."
                )
                return
            if self.player.source().isEmpty():
                # The source is a library path, never a network URL.
                self.player.setSource(QUrl.fromLocalFile(str(Path(self.path).absolute())))
            self.video.show()
            self.placeholder.hide()
            self.player.play()

    def media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if self.player.source().isEmpty():
            return
        if status in (
            QMediaPlayer.MediaStatus.LoadingMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
        ):
            self.status.setText("Loading local preview…")
        elif status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            self.status.setText(
                "Local preview · sound off" if self.audio.isMuted() else "Local preview · sound on"
            )
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.status.setText("Preview finished. Select Play preview to watch again.")

    def toggle_mute(self) -> None:
        self.audio.setMuted(not self.audio.isMuted())
        self.mute.setText("Unmute" if self.audio.isMuted() else "Mute")
        self.status.setText(
            "Local preview · muted" if self.audio.isMuted() else "Local preview · sound on"
        )

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
        self.play.setText(
            "Pause preview" if state == QMediaPlayer.PlaybackState.PlayingState else "Play preview"
        )

    def error(self, error: QMediaPlayer.Error, message: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            self.seek.setEnabled(False)
            self.video.present(QVideoFrame())
            self.video.hide()
            self.placeholder.show()
            self.status.setText(
                "This file could not be played. It may be missing, damaged or use an unsupported format. File inspection is separate from preview playback."
            )

    def release(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.video.present(QVideoFrame())
        self.seek.setRange(0, 0)
        self.seek.setEnabled(False)
        self.time.setText("0:00 / 0:00")
        self.video.hide()
        self.placeholder.show()
        self.audio.setMuted(True)
        self.mute.setText("Unmute")
        self.status.setText(
            "Select Play preview to watch · sound starts off"
            if self.path
            else "Select a video to preview"
        )

    def hideEvent(self, event: QHideEvent) -> None:
        self.release()
        super().hideEvent(event)
