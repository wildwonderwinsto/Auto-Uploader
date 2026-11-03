"""Post Now on the Schedule's platform cards, and queue posts that went nowhere trying again at
the next posting time instead of needing attention. Real SQLite and files; controlled Buffer."""

import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from threading import Event

from test_queues import RULE, QueueFixture

from shortspublisher.social import AUTO_TRIES, SocialError


class PostNowTests(QueueFixture):
    def scheduled(self, due):
        video = self.ready("a.mp4", "First")
        return self.service.publish(video, "First", False, due, Event())

    def test_scheduled_buffer_post_is_taken_off_buffers_schedule_and_posted_now(self):
        post_id = self.scheduled(self.now + timedelta(hours=3))
        self.assertEqual(self.service.get(post_id)["state"], "Scheduled in Buffer")
        hosted = self.service.get(post_id)["hosted_url"]
        self.service.post_now(post_id, Event())
        self.assertNotIn("post-1", self.buffer.posts)  # deleted from Buffer's schedule
        self.assertEqual(len(self.buffer.creates), 2)
        now = self.buffer.creates[-1]
        self.assertEqual(now["mode"], "shareNow")
        self.assertNotIn("dueAt", now)
        self.assertEqual(now["assets"][0]["video"]["url"], hosted)  # the same copy, no re-upload
        self.assertEqual(self.cloud.uploads, 1)
        post = self.service.get(post_id)
        self.assertEqual((post["state"], post["buffer_post_id"]), ("Publishing", "post-2"))
        self.assertIsNone(post["due_at"])

    def test_post_buffer_has_started_is_never_posted_again(self):
        post_id = self.scheduled(self.now + timedelta(hours=3))
        self.buffer.posts["post-1"]["status"] = "sending"
        with self.assertRaises(SocialError):
            self.service.post_now(post_id, Event())
        self.assertEqual(len(self.buffer.creates), 1)
        self.assertIn("post-1", self.buffer.posts)
        self.assertEqual(self.service.get(post_id)["state"], "Publishing")

    def test_post_waiting_for_buffer_goes_now_and_finished_posts_cannot(self):
        post_id = self.scheduled(self.now + timedelta(hours=3))
        with closing(self.library.connect()) as db, db:
            db.execute(
                "UPDATE tiktok_posts SET state='Waiting for Buffer limit',buffer_post_id='' "
                "WHERE id=?",
                (post_id,),
            )
        self.buffer.posts.clear()
        self.service.post_now(post_id, Event())
        self.assertEqual(self.buffer.creates[-1]["mode"], "shareNow")
        self.buffer.send("post-2")
        self.now += timedelta(minutes=3)
        self.service.refresh(Event())
        self.assertEqual(self.service.get(post_id)["state"], "Sent by Buffer")
        with self.assertRaises(SocialError):
            self.service.post_now(post_id, Event())
        self.assertEqual(len(self.buffer.creates), 2)


class TryAgainTests(QueueFixture):
    def test_failed_queue_post_goes_again_at_the_next_posting_time(self):
        first, second = self.ready("a.mp4", "First"), self.ready("b.mp4", "Second")
        queue = self.queues.save(name="Clips", rule=RULE, videos={"tiktok": [first, second]})
        self.run_queues()
        self.buffer.posts["post-1"].update(status="error", error={"message": "TikTok said no"})
        self.now = datetime(2030, 1, 1, 9, 5, tzinfo=UTC)
        self.service.refresh(Event())
        self.assertEqual(self.service.posts()[0]["state"], "Failed")
        self.run_queues()
        # Not left needing attention: the same video goes first, at the next free time.
        self.assertEqual(len(self.buffer.creates), 2)
        again = self.buffer.creates[1]
        self.assertTrue(again["text"].startswith("First"))
        self.assertEqual(again["dueAt"], "2030-01-01T11:00:00Z")
        self.assertEqual([i["state"] for i in self.items(queue)], ["Sent", "Waiting"])
        failed = next(p for p in self.service.posts() if p["state"] == "Failed")
        self.assertEqual(failed["released"], 1)  # no longer shown as needing attention

    def test_a_video_that_keeps_failing_stops_after_a_few_tries(self):
        first = self.ready("a.mp4", "First")
        queue = self.queues.save(name="Clips", rule=RULE, videos={"tiktok": [first]})
        for attempt in range(AUTO_TRIES):
            self.run_queues()
            identity = f"post-{attempt + 1}"
            self.buffer.posts[identity].update(status="error", error={"message": "No"})
            self.now += timedelta(hours=2, minutes=5)
            self.service.refresh(Event())
        self.run_queues()
        self.assertEqual(len(self.buffer.creates), AUTO_TRIES)
        item = self.items(queue)[0]
        self.assertEqual(item["state"], "Needs attention")
        self.assertIn(f"after {AUTO_TRIES} tries", item["reason"])

    def test_queue_post_whose_time_passed_waiting_for_buffer_moves_to_the_next_time(self):
        first = self.ready("a.mp4", "First")
        self.queues.save(name="Clips", rule=RULE, videos={"tiktok": [first]})
        self.buffer.create_fail = "rate"
        self.run_queues()
        post = self.service.posts()[0]
        self.assertEqual(post["state"], "Waiting for Buffer limit")
        self.buffer.create_fail = None
        self.now = datetime(2030, 1, 1, 9, 30, tzinfo=UTC)  # past its 09:00 time; limit reset
        self.run_queues()
        post = self.service.get(post["id"])
        self.assertNotEqual(post["state"], "Missed — not sent")
        self.assertEqual(post["state"], "Scheduled in Buffer")
        self.assertEqual(self.buffer.creates[-1]["dueAt"], "2030-01-01T11:00:00Z")


if __name__ == "__main__":
    unittest.main()
