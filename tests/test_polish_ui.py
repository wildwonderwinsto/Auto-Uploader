"""Workflow, disclosure and preview boundaries using real Qt and isolated providers."""

import unittest
from unittest.mock import patch

import test_uploads_ui as fixtures
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QBoxLayout
from support import settle

from shortspublisher.ui.components import Disclosure


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

    def test_preview_selection_does_not_autoplay_or_upload(self):
        preview = self.w.videos.preview
        self.assertEqual(preview.path, str(self.f.fixture.path))
        self.assertTrue(preview.player.source().isEmpty())
        self.assertEqual(preview.player.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
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

    def test_selected_library_collapses_but_can_reopen_with_keyboard(self):
        section = self.w.videos.library_section
        self.assertFalse(section.toggle.isChecked())
        self.w.videos.back_button.setFocus()
        QTest.keyClick(self.w.videos.back_button, Qt.Key.Key_Space)
        settle(self.app, self.w.videos)
        self.assertTrue(section.toggle.isChecked())
        self.assertTrue(self.w.videos.list.isVisible())

    def test_optional_tags_retain_values_while_collapsed(self):
        editor = self.w.videos.editor
        section = next(d for d in editor.findChildren(Disclosure) if d.title.startswith("Tags"))
        editor.tags = ("test tag",)
        section.set_expanded(False)
        self.assertIn("test tag", editor.value().tags)
        section.set_expanded(True)
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
        self.assertFalse(self.w.accounts.setup_guide.toggle.isChecked())
        self.assertIn(self.f.fixture.repo.connection().channel.title, self.w.channel_summary.text())

    def test_age_restriction_does_not_claim_api_support(self):
        from PySide6.QtWidgets import QLabel

        labels = " ".join(w.text() for w in self.w.videos.editor.findChildren(QLabel))
        self.assertIn("this app cannot apply it", labels)
        self.assertIn("YouTube Studio", labels)
