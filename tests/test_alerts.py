"""Telling the person about uploads that didn't go out: plain reasons and one notification per
new problem. Real SQLite; no notification is ever shown in tests (alerts.ENABLED stays off)."""

import json
import unittest
from contextlib import closing
from unittest.mock import patch

import test_execution as fixtures

from shortspublisher import alerts
from shortspublisher.activity import plain_reason


class PlainReasonTests(unittest.TestCase):
    def test_common_reasons_in_plain_words(self):
        for reason, plain in (
            (
                "Didn't start, nothing was sent to YouTube: YouTube needs you to sign in again.",
                "YouTube needs you to sign in",
            ),
            ("Unable to refresh authorization. Check your network and retry.", "No connection"),
            ("The app closed before the upload started.", "The app closed before it started"),
            ("Approved content or review changed. Reopen and approve.", "The video or its"),
        ):
            self.assertTrue(plain_reason(reason).startswith(plain), reason)

    def test_unknown_reason_keeps_its_first_sentence_short(self):
        self.assertEqual(plain_reason("Something odd. More detail."), "Something odd")
        self.assertLessEqual(len(plain_reason("x" * 200)), 60)


class AnnounceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ExecutionTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.ExecutionTests.tearDownClass()

    def setUp(self):
        self.e = fixtures.ExecutionTests()
        self.e.setUp()
        self.addCleanup(self.e.doCleanups)
        self.s = self.e.s
        self.active, self.sign_out, self.due = self.e.active, self.e.sign_out, self.e.due
        self.dispatch = self.e.dispatch

    def test_each_new_problem_is_announced_once(self):
        library = self.s.library
        self.assertEqual(alerts.problems(library), {})
        job = self.active()
        self.sign_out()
        self.due(job)
        with patch.object(alerts, "send") as send:
            self.dispatch()  # announces after dispatching
            self.dispatch()
        send.assert_called_once()
        headline = send.call_args.args[0]
        self.assertIn("didn't upload", headline)
        self.assertIn("sign in", headline)
        saved = json.loads((library.path.parent / alerts.FILE).read_text())
        self.assertEqual(saved, [f"job:{job.id}:Needs Attention"])

    def test_cleared_problem_is_announced_again_if_it_returns(self):
        job = self.active()
        with closing(self.s.library.connect()) as db, db:
            db.execute(
                "UPDATE scheduled_jobs SET state='Needs Attention',reason='X' WHERE id=?", (job.id,)
            )
        with patch.object(alerts, "send") as send:
            self.assertEqual(len(alerts.announce(self.s.library)), 1)
            self.assertEqual(alerts.announce(self.s.library), [])
            with closing(self.s.library.connect()) as db, db:
                db.execute("UPDATE scheduled_jobs SET state='Scheduled' WHERE id=?", (job.id,))
            self.assertEqual(alerts.announce(self.s.library), [])
            with closing(self.s.library.connect()) as db, db:
                db.execute(
                    "UPDATE scheduled_jobs SET state='Needs Attention' WHERE id=?", (job.id,)
                )
            self.assertEqual(len(alerts.announce(self.s.library)), 1)
        self.assertEqual(send.call_count, 2)

    def test_notifications_are_off_unless_an_entry_point_turns_them_on(self):
        self.assertFalse(alerts.ENABLED)
        with patch("subprocess.Popen") as popen:
            alerts.send("Headline", "Body")
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
