import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

import test_execution as fixtures

from shortspublisher.background import LaunchAgent, ServiceError
from shortspublisher.background_state import Ownership, read
from shortspublisher.execution import AutomaticPublisher
from shortspublisher.library import Library
from shortspublisher.worker import run


class BackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ExecutionTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ExecutionTests.tearDownClass()

    def setUp(self):
        self.f = fixtures.ExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.a, self.s, self.u = self.f.a, self.f.s, self.f.u
        self.manager = LaunchAgent(self.u.library)
        self.manager.label = "com.shortspublisher.test.unit"
        self.manager.login_directory = self.u.root / "LoginAgents"

    def enable(self):
        self.manager.configure(enabled=True, requested=True, consent=True)
        self.a.ownership = Ownership(self.u.library, "background")

    def active(self, background=True):
        job = self.f.f.approve()
        self.a.authorize(job.id, job.revision, Event(), background=background)
        self.a.control(False)
        return self.s.get(job.id)

    def test_default_no_background_permission(self):
        state = read(self.u.library)
        self.assertEqual((state["enabled"], state["requested"], state["login"]), (0, 0, 0))
        self.assertFalse(self.manager.plist.exists())

    def test_migration_preserves_all_old_records(self):
        path = self.u.root / "v7.sqlite3"
        shutil.copy2("tests/fixtures/library-v7.sqlite3", path)
        with sqlite3.connect(path) as db:
            names = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
            before = {n: db.execute("select * from " + n).fetchall() for n in names}
        library = Library(path)
        library.initialize()
        with closing(library.connect()) as db:
            for name, values in before.items():
                self.assertEqual([tuple(r) for r in db.execute("select * from " + name)], values)
            self.assertFalse(db.execute("select * from background_consent").fetchall())
            self.assertFalse(db.execute("pragma foreign_key_check").fetchall())
        self.assertFalse(read(library)["enabled"])

    def test_migration_rollback(self):
        path = self.u.root / "v7.sqlite3"
        shutil.copy2("tests/fixtures/library-v7.sqlite3", path)
        with sqlite3.connect(path) as db:
            db.execute("create table background_consent (id text)")
        with self.assertRaises(sqlite3.Error):
            Library(path).initialize()
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("pragma user_version").fetchone()[0], 7)
            self.assertFalse(
                db.execute("select 1 from sqlite_master where name='background_config'").fetchone()
            )

    def test_background_executes_exact_bytes_once(self):
        job = self.active()
        self.enable()
        self.f.due(job)
        self.a.dispatch(Event(), lambda _: None)
        self.a.dispatch(Event(), lambda _: None)
        self.assertEqual(self.u.http.initiations, 1)
        self.assertEqual(bytes(self.u.http.received), self.u.path.read_bytes())
        self.assertEqual(self.u.store.all()[0].state, "Public Verified")
        with closing(self.u.library.connect()) as db:
            self.assertEqual(
                db.execute("select kind from execution_owner").fetchone()[0], "background"
            )

    def test_old_approval_not_background_authorization(self):
        job = self.active(False)
        self.enable()
        self.f.due(job)
        self.a.dispatch(Event(), lambda _: None)
        self.assertEqual(self.u.http.initiations, 0)
        self.assertIn("desktop-only", self.s.get(job.id).reason)

    def test_enabled_service_prevents_desktop_dispatch(self):
        job = self.active()
        self.manager.configure(enabled=True, requested=True, consent=True)
        self.f.due(job)
        self.a.dispatch(Event(), lambda _: None)
        self.assertEqual(self.u.http.initiations, 0)

    def test_safe_handoff_after_stop(self):
        job = self.active()
        self.enable()
        desktop = AutomaticPublisher(self.s, self.u.service)
        with self.a.ownership.acquire() as owned:
            self.assertTrue(owned)
            self.manager.configure(requested=False)
            self.f.due(job)
            desktop.dispatch(Event(), lambda _: None)
            self.assertEqual(self.u.http.initiations, 0)
        desktop.dispatch(Event(), lambda _: None)
        self.assertEqual(self.u.http.initiations, 1)

    def test_stale_heartbeat_never_steals_owner(self):
        self.enable()
        with self.a.ownership.acquire():
            with closing(self.u.library.connect()) as db, db:
                db.execute("update background_owner set heartbeat='2000-01-01T00:00:00+00:00'")
            other = Ownership(self.u.library, "background")
            with other.acquire() as owned:
                self.assertFalse(owned)

    def test_gui_startup_does_not_recover_active_worker(self):
        job = self.active()
        self.enable()
        with self.a.ownership.acquire():
            self.f.due(job)
            identity = self.a.claim()
            AutomaticPublisher(self.s, self.u.service).startup()
            with closing(self.u.library.connect()) as db:
                self.assertEqual(
                    db.execute(
                        "select phase from scheduled_executions where id=?", (identity,)
                    ).fetchone()[0],
                    "Claimed",
                )

    def test_stop_during_preflight_prevents_post(self):
        job = self.active()
        self.enable()
        self.f.due(job)
        original = self.u.service.credentials

        def credentials(*args, **kwargs):
            result = original(*args, **kwargs)
            self.manager.configure(requested=False)
            return result

        with patch.object(self.u.service, "credentials", side_effect=credentials):
            self.a.dispatch(Event(), lambda _: None)
        self.assertEqual(self.u.http.initiations, 0)

    def test_changed_owner_rejected_before_post(self):
        job = self.active()
        self.enable()
        self.f.due(job)
        original = self.u.service.credentials

        def credentials(*args, **kwargs):
            result = original(*args, **kwargs)
            with closing(self.u.library.connect()) as db, db:
                db.execute("update background_owner set token='stale'")
            return result

        with patch.object(self.u.service, "credentials", side_effect=credentials):
            self.a.dispatch(Event(), lambda _: None)
        self.assertEqual(self.u.http.initiations, 0)

    def test_worker_startup_does_not_catch_up(self):
        job = self.active()
        self.enable()
        self.f.due(job)
        self.f.f.now += timedelta(seconds=1)
        self.a.startup()
        self.a.dispatch(Event(), lambda _: None)
        self.assertEqual(self.s.get(job.id).state, "Missed")
        self.assertEqual(self.u.http.initiations, 0)

    def test_second_process_cannot_acquire_active_lock(self):
        self.enable()
        script = "from pathlib import Path; from shortspublisher.library import Library; from shortspublisher.background_state import Ownership; import sys; o=Ownership(Library(Path(sys.argv[1])),'background'); c=o.acquire(); print(c.__enter__()); c.__exit__(None,None,None)"
        with self.a.ownership.acquire():
            result = subprocess.run(
                [sys.executable, "-c", script, str(self.u.library.path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False")

    def test_headless_import_does_not_load_qt(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import shortspublisher.worker,sys; assert not any(n.startswith('PySide6') for n in sys.modules)",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_headless_cancel_exits_and_releases(self):
        self.enable()
        cancel = Event()
        cancel.set()
        self.assertEqual(run(self.a, cancel), 0)
        self.assertEqual(read(self.u.library)["owner"]["state"], "Stopped")

    def test_monitor_can_heartbeat_across_threads(self):
        self.enable()
        errors = []
        with self.a.ownership.acquire():

            def beat():
                try:
                    self.a.ownership.heartbeat()
                except Exception as exc:
                    errors.append(exc)

            thread = Thread(target=beat)
            thread.start()
            thread.join()
        self.assertEqual(errors, [])

    def test_plist_absolute_and_secret_free(self):
        self.manager.write(self.manager.plist)
        data = plistlib.loads(self.manager.plist.read_bytes())
        self.assertEqual(data["Label"], self.manager.label)
        self.assertTrue(Path(data["ProgramArguments"][0]).is_absolute())
        self.assertTrue(Path(data["ProgramArguments"][-1]).is_absolute())
        self.assertFalse(data["KeepAlive"])
        self.assertNotIn("EnvironmentVariables", data)
        self.assertEqual(self.manager.plist.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.manager.plist.stat().st_uid, os.getuid())

    def test_missing_runtime_rejected(self):
        self.manager.runtime = self.u.root / "missing"
        with self.assertRaises(ServiceError):
            self.manager.configuration()

    def test_other_workspace_plist_not_overwritten(self):
        self.manager.write(self.manager.plist)
        data = plistlib.loads(self.manager.plist.read_bytes())
        data["ProgramArguments"][-1] = "/other/workspace"
        self.manager.plist.write_bytes(plistlib.dumps(data))
        with self.assertRaises(ServiceError):
            self.manager.write(self.manager.plist)

    def test_symlink_plist_rejected(self):
        self.manager.directory.mkdir()
        self.manager.plist.symlink_to(self.u.path)
        with self.assertRaises(ServiceError):
            self.manager.write(self.manager.plist)

    def test_world_writable_root_rejected(self):
        mode = self.manager.root.stat().st_mode
        self.manager.root.chmod(0o777)
        try:
            with self.assertRaises(ServiceError):
                self.manager.configuration()
        finally:
            self.manager.root.chmod(mode)

    def test_invalid_identifier_rejected(self):
        self.manager.label = "com.apple.unrelated"
        with self.assertRaises(ServiceError):
            self.manager.configuration()

    def test_unknown_action_rejected(self):
        with self.assertRaises(ServiceError):
            self.manager.perform("arbitrary command")

    def test_missing_os_service_is_not_running(self):
        with patch.object(
            self.manager, "launchctl", return_value=subprocess.CompletedProcess([], 113, "", "")
        ):
            self.assertEqual(self.manager.status()["status"], "Not Installed")

    def test_os_query_failure_not_false_stopped(self):
        with patch.object(
            self.manager, "launchctl", return_value=subprocess.CompletedProcess([], 1, "", "")
        ):
            with self.assertRaises(ServiceError):
                self.manager.status()

    def test_loaded_other_installation_rejected(self):
        with patch.object(
            self.manager,
            "launchctl",
            return_value=subprocess.CompletedProcess(
                [], 0, "path = /unrelated.plist\npid = 123", ""
            ),
        ):
            with self.assertRaises(ServiceError):
                self.manager.status()

    def lifecycle(self):
        self.os_state = {"loaded": False, "pid": 0}

        def call(*args, **kwargs):
            if args[0] == "bootstrap":
                self.os_state["loaded"] = True
            elif args[0] == "bootout":
                self.os_state["loaded"] = False
            elif args[0] == "kill":
                self.os_state["pid"] = 0
            return subprocess.CompletedProcess([], 0, "", "")

        self.addCleanup(patch.stopall)
        patch.object(Path, "home", return_value=self.u.root).start()
        patch.object(self.manager, "os_status", side_effect=lambda: self.os_state.copy()).start()
        self.calls = patch.object(self.manager, "launchctl", side_effect=call).start()

    def test_install_needs_consent(self):
        self.lifecycle()
        with self.assertRaises(ServiceError):
            self.manager.perform("enable")
        self.assertFalse(self.manager.plist.exists())
        self.calls.assert_not_called()

    def test_start_needs_existing_enable(self):
        self.lifecycle()
        with self.assertRaises(ServiceError):
            self.manager.perform("start")
        self.calls.assert_not_called()

    def test_install_idempotent_does_not_start(self):
        self.lifecycle()
        self.manager.perform("enable", consent=True)
        content = self.manager.plist.read_bytes()
        self.manager.perform("enable", consent=True)
        self.assertEqual(self.manager.plist.read_bytes(), content)
        self.assertFalse(read(self.u.library)["requested"])
        self.calls.assert_not_called()

    def test_login_requires_separate_opt_in(self):
        self.lifecycle()
        self.manager.perform("enable", consent=True)
        self.assertFalse(self.manager.login_plist.exists())
        self.manager.perform("enable", consent=True, login=True)
        self.assertTrue(self.manager.login_plist.exists())
        self.assertTrue(read(self.u.library)["login"])
        self.manager.perform("enable", consent=True, login=False)
        self.assertFalse(self.manager.login_plist.exists())

    def test_lifecycle_start_stop_restart_remove(self):
        self.lifecycle()
        self.manager.perform("enable", consent=True)
        state = self.manager.perform("start")
        self.assertTrue(state["requested"])
        self.assertNotEqual(state["status"], "Running")
        self.manager.perform("restart")
        self.assertEqual(sum(c.args[0] == "bootstrap" for c in self.calls.call_args_list), 2)
        self.manager.perform("remove")
        self.manager.perform("remove")
        self.assertFalse(self.manager.plist.exists())
        self.assertFalse(read(self.u.library)["enabled"])
        self.assertTrue(self.u.path.exists())
        self.assertIsNotNone(self.u.accounts.repository.connection())

    def test_failed_start_retains_conservative_ownership_intent(self):
        self.lifecycle()
        self.manager.perform("enable", consent=True)
        self.calls.side_effect = ServiceError("controlled launch failure")
        with self.assertRaises(ServiceError):
            self.manager.perform("start")
        self.assertTrue(read(self.u.library)["requested"])
        with Ownership(self.u.library).acquire() as owned:
            self.assertFalse(owned)

    def test_stale_live_pid_is_not_reported_running(self):
        self.lifecycle()
        self.manager.perform("enable", consent=True)
        self.os_state.update(loaded=True, pid=123)
        with closing(self.u.library.connect()) as db, db:
            db.execute(
                "update background_owner set pid=123,kind='background',state='Running',heartbeat='2000-01-01T00:00:00+00:00'"
            )
        self.assertEqual(self.manager.status()["status"], "Stale / unresponsive")

    def test_stop_timeout_does_not_remove_configuration(self):
        self.lifecycle()
        self.manager.perform("enable", consent=True)
        self.os_state.update(loaded=True, pid=123)
        self.calls.side_effect = lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "", "")
        with patch("shortspublisher.background.time.monotonic", side_effect=[0, 41]):
            with self.assertRaisesRegex(ServiceError, "Still stopping"):
                self.manager.perform("remove")
        self.assertTrue(self.manager.plist.exists())
        self.assertFalse(any(c.args[0] == "bootout" for c in self.calls.call_args_list))

    def test_database_contention_prevents_claim_without_post(self):
        job = self.active()
        self.enable()
        self.f.due(job)
        with closing(self.u.library.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            with self.assertRaises(sqlite3.OperationalError):
                self.a.dispatch(Event(), lambda _: None)
        self.assertEqual(self.u.http.initiations, 0)
        self.a.dispatch(Event(), lambda _: None)
        self.assertEqual(self.u.http.initiations, 1)

    def test_background_waits_for_foreground_transfer_release(self):
        foreground = Ownership(self.u.library)
        entered = Event()
        cancel = Event()
        background = Ownership(self.u.library, "background")

        def acquire():
            with background.acquire(wait=cancel) as owned:
                if owned:
                    entered.set()

        with foreground.acquire() as owned:
            self.assertTrue(owned)
            self.enable()
            thread = Thread(target=acquire)
            thread.start()
            self.assertFalse(entered.wait(0.1))
        self.assertTrue(entered.wait(3))
        thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_stop_cancels_pending_handoff(self):
        foreground = Ownership(self.u.library)
        result = []
        cancel = Event()
        background = Ownership(self.u.library, "background")

        def acquire():
            with background.acquire(wait=cancel) as owned:
                result.append(owned)

        with foreground.acquire():
            self.enable()
            thread = Thread(target=acquire)
            thread.start()
            cancel.set()
            thread.join(3)
        self.assertEqual(result, [False])
