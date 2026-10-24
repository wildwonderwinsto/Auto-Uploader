"""Video queues across platforms (schema 17): real SQLite, FFprobe and files; controlled Buffer."""

import unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event

from support import make_video
from test_preparation import VALID
from test_tiktok import Fixture

from shortspublisher.cadence import Rule
from shortspublisher.library import SCHEMA_VERSION
from shortspublisher.preparation import PreparationStore
from shortspublisher.queues import QueueService
from shortspublisher.scheduling import ScheduleError

RULE = Rule("UTC", tuple(range(7)), "09:00", "21:00", 2)


class QueueTests(Fixture):
    def setUp(self):
        super().setUp()
        self.connect()
        self.host()
        with closing(self.library.connect()) as db, db:  # the user's own "Turn On"
            db.execute("UPDATE publishing_control SET paused=0 WHERE id=1")
        self.queues = QueueService(self.library, lambda: self.now)
        self.preps = PreparationStore(self.library)

    def ready(self, name, title):
        # Distinct durations give distinct bytes; identical copies would be duplicates.
        self.made = getattr(self, "made", 0) + 1
        make_video(self.root / name, size="360x640", duration=2 + self.made)
        video = self.imports.execute("import", str(self.root / name), Event())["selected"]
        prep = self.preps.save(video.id, replace(VALID, title=title, tags=("cats",)), 0)
        self.preps.complete(video.id, prep.revision, video.inspection_revision)
        self.service.save_draft(video.id, "", False)  # the AI label, chosen in Details
        return video.id

    def run_queues(self):
        self.service.reconcile(self.scanner(), Event())

    def scanner(self):
        class NoFolders:  # reconcile reads folders only for auto uploads
            pass

        return NoFolders()

    def items(self, queue):
        return [i for i in self.queues.state()["items"] if i["queue_id"] == queue]

    def test_one_video_in_buffer_at_a_time_in_order(self):
        first, second = self.ready("a.mp4", "First"), self.ready("b.mp4", "Second")
        queue = self.queues.save(name="Clips", rule=RULE, videos={"tiktok": [first, second]})
        self.run_queues()
        self.run_queues()
        self.assertEqual(len(self.buffer.creates), 1)  # never the whole queue at once
        self.assertEqual(self.buffer.creates[0]["dueAt"], "2030-01-01T09:00:00Z")
        self.assertTrue(self.buffer.creates[0]["text"].startswith("First"))
        self.assertEqual([i["state"] for i in self.items(queue)], ["Sent", "Waiting"])
        self.buffer.send("post-1")
        self.now = datetime(2030, 1, 1, 9, 5, tzinfo=UTC)
        self.service.refresh(Event())
        self.run_queues()
        self.assertEqual(len(self.buffer.creates), 2)
        self.assertEqual(self.buffer.creates[1]["dueAt"], "2030-01-01T11:00:00Z")
        self.assertTrue(self.buffer.creates[1]["text"].startswith("Second"))

    def test_edits_apply_to_videos_not_sent_yet(self):
        first, second = self.ready("a.mp4", "First"), self.ready("b.mp4", "Second")
        third = self.ready("c.mp4", "Third")
        queue = self.queues.save(name="Clips", rule=RULE, videos={"tiktok": [first, second]})
        self.run_queues()
        # New details for a waiting video, a new order and a new video: all apply.
        prep = self.preps.get(second)
        prep = self.preps.save(second, replace(prep.metadata, title="Second, renamed"), 1)
        self.preps.complete(second, prep.revision, self.library.get(second).inspection_revision)
        old = self.queues.state()["queues"][0]
        with self.assertRaises(ScheduleError):  # an out-of-date edit is refused
            self.queues.save(
                name="Clips", rule=RULE, videos={"tiktok": [third]}, identity=queue, revision=9
            )
        self.queues.save(
            name="Renamed",
            rule=Rule("UTC", tuple(range(7)), "18:00", "18:00", 0),
            videos={"tiktok": [third, second, first]},  # the first is already in Buffer
            identity=queue,
            revision=old["revision"],
        )
        states = [(i["video_id"], i["state"]) for i in self.items(queue)]
        self.assertEqual(states, [(first, "Sent"), (third, "Waiting"), (second, "Waiting")])
        self.buffer.send("post-1")
        self.now = datetime(2030, 1, 1, 9, 5, tzinfo=UTC)
        self.service.refresh(Event())
        self.run_queues()
        self.assertTrue(self.buffer.creates[1]["text"].startswith("Third"))
        self.assertEqual(self.buffer.creates[1]["dueAt"], "2030-01-01T18:00:00Z")  # new times
        self.buffer.send("post-2")
        self.now = datetime(2030, 1, 1, 18, 5, tzinfo=UTC)
        self.service.refresh(Event())
        self.run_queues()
        self.assertTrue(self.buffer.creates[2]["text"].startswith("Second, renamed"))
        self.assertEqual(len(self.buffer.creates), 3)  # the first was never sent twice

    def test_details_being_edited_wait_and_the_next_ready_video_goes(self):
        first, second = self.ready("a.mp4", "First"), self.ready("b.mp4", "Second")
        prep = self.preps.get(first)
        self.preps.save(first, replace(prep.metadata, title="Half edited"), 1)  # not ready
        queue = self.queues.save(name="Clips", rule=RULE, videos={"tiktok": [first, second]})
        self.run_queues()
        self.assertTrue(self.buffer.creates[0]["text"].startswith("Second"))
        waiting = next(i for i in self.items(queue) if i["video_id"] == first)
        self.assertEqual(waiting["state"], "Waiting")
        self.assertIn("Mark this video ready", waiting["reason"])

    def test_pause_global_pause_and_delete(self):
        first = self.ready("a.mp4", "First")
        queue = self.queues.save(name="Clips", rule=RULE, videos={"tiktok": [first]})
        self.queues.control(queue, "pause")
        self.run_queues()
        with closing(self.library.connect()) as db, db:
            db.execute("UPDATE publishing_control SET paused=1 WHERE id=1")
        self.queues.control(queue, "resume")
        self.run_queues()
        self.assertEqual(self.buffer.creates, [])
        self.queues.control(queue, "remove")
        with closing(self.library.connect()) as db, db:
            db.execute("UPDATE publishing_control SET paused=0 WHERE id=1")
        self.run_queues()
        self.assertEqual(self.buffer.creates, [])
        self.assertEqual(self.queues.state(), {"queues": [], "items": []})

    def test_other_platforms_never_send_a_queue_for_tiktok_only(self):
        first = self.ready("a.mp4", "First")
        self.queues.save(name="Clips", rule=RULE, videos={"tiktok": [first]})
        self.assertEqual(self.queues.state()["queues"][0]["platforms"], ["tiktok"])
        with self.assertRaises(ScheduleError):
            self.queues.save(name="Clips", rule=RULE, videos={"youtube": [first]})
        with self.assertRaises(ScheduleError):
            self.queues.save(name="", rule=RULE, videos={"tiktok": [first]})

    def test_v16_library_gains_empty_queue_tables(self):
        with closing(self.library.connect()) as db, db:
            db.execute("DROP TABLE queue_items")
            db.execute("DROP TABLE queues")
            db.execute("PRAGMA user_version=16")
        self.library.initialize()
        with closing(self.library.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(SCHEMA_VERSION, 17)
            self.assertEqual(db.execute("SELECT count(*) FROM queues").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM queue_items").fetchone()[0], 0)
            # Existing records are untouched: the connection made in setUp is still there.
            self.assertIsNotNone(self.service.connection())


if __name__ == "__main__":
    unittest.main()
