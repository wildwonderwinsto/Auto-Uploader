"""Workflow, disclosure and preview boundaries using real Qt and isolated providers."""

import time
import unittest
from unittest.mock import patch

import test_uploads_ui as fixtures
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QBoxLayout
from support import settle

from shortspublisher.ui.components import Section


class PolishUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UploadUiTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.UploadUiTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.UploadUiTests()
        self.f.setUp()
        self.w, self.app = self.f.window, self.f.app
        self.addCleanup(self.cleanup)

    def cleanup(self):
        editor = self.w.videos.editor
        if editor.preparation:
            editor.load(editor.preparation)
        self.f.tearDown()

    def test_preview_selection_shows_first_frame_without_playing_or_uploading(self):
        preview = self.w.videos.preview
        self.assertEqual(preview.path, str(self.f.fixture.path))
        deadline = time.monotonic() + 5
        while (preview.priming or preview.video.frame.isNull()) and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertFalse(preview.video.frame.isNull())  # visible straight away
        self.assertNotEqual(preview.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertFalse(preview.video.playing)
        self.assertTrue(preview.audio.isMuted())
        self.assertEqual(self.f.fixture.http.initiations, 0)

    def test_preview_navigation_releases_decoder(self):
        preview = self.w.videos.preview
        preview.video.frame = QPixmap(16, 16)
        preview.time.setText("0:20 / 0:30")
        preview.seek.setRange(0, 30000)
        self.w.navigate("dashboard")
        self.assertTrue(preview.player.source().isEmpty())
        self.assertTrue(preview.video.frame.isNull())
        self.assertEqual(preview.time.text(), "0:00 / 0:00")
        self.assertEqual(preview.seek.maximum(), 0)

    def test_preview_close_releases_decoder(self):
        with patch.object(self.w.videos.preview, "release") as release:
            self.w.videos.request_close()
            release.assert_called_once()

    def test_back_to_library_with_keyboard_shows_the_list(self):
        self.w.videos.back_button.setFocus()
        QTest.keyClick(self.w.videos.back_button, Qt.Key.Key_Space)
        settle(self.app, self.w.videos)
        self.assertTrue(self.w.videos.library_section.isVisible())
        self.assertTrue(self.w.videos.list.isVisible())

    def test_optional_tags_are_always_visible(self):
        editor = self.w.videos.editor
        section = next(d for d in editor.findChildren(Section) if d.title.startswith("Tags"))
        editor.tags = ("test tag",)
        self.assertFalse(section.content.isHidden())
        self.assertIn("test tag", editor.value().tags)

    def test_review_next_step_does_not_authorize_upload(self):
        self.w.videos.tabs.setCurrentIndex(2)
        settle(self.app, self.w.videos)
        self.assertTrue(self.w.videos.publish_next.isEnabled())
        self.w.videos.publish_next.click()
        self.assertEqual(self.w.videos.tabs.currentIndex(), 3)
        self.assertEqual(self.f.fixture.http.initiations, 0)

    def test_unsaved_edit_disables_review_forward_action(self):
        self.w.videos.editor.title.setText("Changed title")
        self.assertFalse(self.w.videos.publish_next.isEnabled())
        self.w.videos.editor.load(self.w.videos.editor.preparation)

    def test_preview_layout_three_sizes(self):
        for width, height in ((760, 560), (1180, 780), (1600, 1000)):
            self.w.resize(width, height)
            self.app.processEvents()
            self.assertEqual(self.w.videos.horizontalScrollBar().maximum(), 0)
            self.assertEqual(
                self.w.videos.workspace.direction(),
                QBoxLayout.Direction.TopToBottom
                if width == 760
                else QBoxLayout.Direction.LeftToRight,
            )

    def test_service_error_feedback_is_visible(self):
        self.w.background.receive({"error": "Still stopping. Refresh status."})
        self.assertIn("Still stopping", self.w.background.feedback.text())
        self.assertFalse(self.w.background.quit_after_stop)

    def test_connected_account_hides_developer_setup(self):
        # Developer setup lives in Settings, never on the Accounts page.
        self.assertFalse(self.w.accounts.isAncestorOf(self.w.accounts.setup_guide))
        self.assertTrue(self.w.background.isAncestorOf(self.w.accounts.setup_guide))
        self.assertFalse(self.w.accounts.setup_button.isVisible())
        self.assertIn(self.f.fixture.repo.connection().channel.title, self.w.channel_summary.text())

    def test_age_restriction_does_not_claim_api_support(self):
        from PySide6.QtWidgets import QLabel

        labels = " ".join(w.text() for w in self.w.videos.editor.findChildren(QLabel))
        self.assertIn("this app cannot apply it", labels)
        self.assertIn("YouTube Studio", labels)
