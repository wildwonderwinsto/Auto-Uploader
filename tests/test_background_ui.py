import unittest
from contextlib import closing
from unittest.mock import patch

import test_execution_ui as fixtures
from PySide6.QtWidgets import QDialog, QMessageBox
from support import settle

from shortspublisher.ui.execution import ActivationDialog


class BackgroundUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ExecutionUiTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ExecutionUiTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.ExecutionUiTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.page = self.f.window.background
        self.addCleanup(lambda: settle(self.f.app, self.page))

    def test_settings_open_never_install(self):
        with patch.object(
            self.page.manager, "perform", return_value={"error": "controlled unavailable"}
        ) as perform:
            self.f.window.navigate("settings")
            settle(self.f.app, self.page)
            self.assertEqual([c.args[0] for c in perform.call_args_list], ["status"])
            self.assertIn("unavailable", self.page.status.text())

    def test_cancel_service_consent_no_write(self):
        with (
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No),
            patch.object(self.page.manager, "perform") as perform,
        ):
            self.page.buttons["enable"].click()
            perform.assert_not_called()

    def test_service_enable_distinct_from_start(self):
        with (
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
            patch.object(
                self.page.manager, "perform", return_value={"error": "controlled"}
            ) as perform,
        ):
            self.page.buttons["enable"].click()
            settle(self.f.app, self.page)
            perform.assert_called_once_with("enable", consent=True, login=False)

    def test_login_separate_opt_in(self):
        self.assertFalse(self.page.login.isChecked())
        self.page.login.setChecked(True)
        with (
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
            patch.object(
                self.page.manager, "perform", return_value={"error": "controlled"}
            ) as perform,
        ):
            self.page.buttons["enable"].click()
            settle(self.f.app, self.page)
            self.assertTrue(perform.call_args.kwargs["login"])

    def test_job_background_checkbox_default_off(self):
        dialog = ActivationDialog(self.f.approve(), self.f.window)
        self.assertFalse(dialog.background.isChecked())
        dialog.deleteLater()

    def test_job_background_consent_persisted_only_if_selected(self):
        job = self.f.approve()

        def accept(dialog):
            dialog.background.setChecked(True)
            return QDialog.DialogCode.Accepted

        with patch.object(ActivationDialog, "exec", accept):
            self.f.c.authorize(job)
        self.f.wait()
        with closing(self.f.c.service.library.connect()) as db:
            self.assertIsNotNone(
                db.execute("select * from background_consent where job_id=?", (job.id,)).fetchone()
            )
        self.assertIn(
            "background permitted", self.f.c.description(self.f.c.service.schedules.get(job.id))
        )

    def test_close_interface_does_not_stop_service(self):
        with patch.object(self.page.manager, "perform") as perform:
            self.page.request_close()
            perform.assert_not_called()

    def test_failed_stop_does_not_quit(self):
        with (
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
            patch.object(self.page.manager, "perform", return_value={"error": "Still stopping"}),
            patch.object(self.f.window, "close") as close,
        ):
            self.page.stop_and_quit()
            settle(self.f.app, self.page)
            self.assertFalse(self.page.quit_after_stop)
            close.assert_not_called()

    def test_active_background_stop_routes_to_service(self):
        self.f.c.data["background"] = {"requested": 1}
        with patch.object(self.page, "act") as act:
            self.f.c.stop_active()
            act.assert_called_once_with("stop")
