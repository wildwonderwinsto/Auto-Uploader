import os
import time
import unittest
from datetime import UTC, datetime

from shortspublisher import activity
from shortspublisher.scheduling import Job
from shortspublisher.upload_store import Attempt


def attempt(state, remote="", processing="processed", title="Clip", identity="a1"):
    return Attempt(
        identity,
        1,
        "UC",
        {"metadata": {"title": title}, "size": 200},
        state,
        50,
        remote,
        "public",
        processing,
        "",
        "2026-10-01T10:00:00+00:00",
        "2026-10-01T10:05:00+00:00",
        "2026-10-01T10:06:00+00:00",
        False,
    )


def job(state, when="2026-10-21T15:00:00+00:00", enabled=1, identity="j1", title="Planned"):
    return Job(
        identity,
        2,
        "youtube",
        "UC",
        "Channel",
        "hash",
        {"metadata": {"title": title}},
        when,
        "UTC",
        when,
        0,
        state,
        "reason",
        1,
        when,
        when,
        when,
        "",
        "",
        enabled,
    )


def post(state, **values):
    base = {
        "state": state,
        "caption": "Dance clip #fyp\nsecond line",
        "due_at": None,
        "sent_at": None,
        "external_link": "",
        "buffer_post_id": "b1",
        "video_id": 3,
        "error": "",
        "released": 0,
        "updated_at": "2026-10-02T00:00:00+00:00",
    }
    return {**base, **values}


class TimelineTests(unittest.TestCase):
    def test_youtube_uploads_are_described_in_plain_words(self):
        done, busy, failed = activity.youtube_uploads(
            [attempt("Public Verified", "vid1"), attempt("Uploading"), attempt("Interrupted")]
        )
        self.assertEqual(
            (done.kind, done.status, done.link), ("done", "Public", "https://youtu.be/vid1")
        )
        self.assertEqual((busy.kind, busy.status), ("uploading", "Uploading · 25%"))
        self.assertEqual((failed.kind, failed.status), ("attention", "Upload interrupted"))
        self.assertEqual(activity.youtube_uploads([attempt("Cancelled")]), [])
        processing = activity.youtube_uploads([attempt("Uploaded Private", processing="")])[0]
        self.assertEqual(processing.status, "Private · YouTube is processing")

    def test_started_jobs_are_left_to_their_upload_record(self):
        entries = activity.youtube_plans(
            [
                job("Scheduled"),
                job("Scheduled", identity="j2", enabled=0),
                job("Missed", identity="j3"),
            ],
            started={"j1"},
        )
        self.assertEqual(
            [(e.kind, e.status) for e in entries],
            [("attention", "Waiting for your approval"), ("attention", "Missed its time")],
        )

    def test_only_the_first_queued_item_has_a_time(self):
        recurring = {
            "plans": [{"id": "p", "enabled": 1, "next_slot": "2026-10-22T09:00:00+00:00"}],
            "items": [
                {
                    "plan_id": "p",
                    "state": "Queued",
                    "snapshot": {"filename": "a.mp4"},
                    "video_id": 1,
                },
                {
                    "plan_id": "p",
                    "state": "Queued",
                    "snapshot": {"filename": "b.mp4"},
                    "video_id": 2,
                },
            ],
        }
        first, second = activity.youtube_queue(recurring)
        self.assertEqual((first.title, first.when), ("a.mp4", "2026-10-22T09:00:00+00:00"))
        self.assertEqual((second.when, second.status), ("", "Waiting in queue"))

    def test_tiktok_posts(self):
        sent, scheduled, failed = activity.tiktok_posts(
            [
                post(
                    "Sent by Buffer", sent_at="2026-10-03T00:00:00+00:00", external_link="https://t"
                ),
                post("Scheduled in Buffer", due_at="2026-10-23T00:00:00+00:00"),
                post("Waiting for TikTok reconnection"),
            ]
        )
        self.assertEqual(
            (sent.kind, sent.title, sent.link), ("done", "Dance clip #fyp", "https://t")
        )
        self.assertEqual((scheduled.kind, scheduled.status), ("upcoming", "Scheduled"))
        self.assertEqual((failed.kind, failed.status), ("attention", "Reconnect TikTok"))
        self.assertEqual(activity.tiktok_posts([post("Failed", released=1)]), [])

    def test_ordering(self):
        entries = activity.timeline(
            [
                job("Scheduled", "2026-10-25T00:00:00+00:00", identity="late"),
                job("Scheduled", "2026-10-21T00:00:00+00:00", identity="soon"),
            ],
            [attempt("Public Verified", "old"), attempt("Public Verified", "new", identity="a2")],
            set(),
            {},
            [post("Sent by Buffer", sent_at="2026-10-09T00:00:00+00:00")],
        )
        self.assertEqual(
            [e.when[:10] for e in activity.upcoming(entries)], ["2026-10-21", "2026-10-25"]
        )
        recent = activity.recent(entries)
        self.assertEqual(recent[0].platform, "tiktok")
        self.assertEqual(activity.since(recent, datetime(2026, 10, 5, tzinfo=UTC)), 1)

    def test_other_buffer_platforms_and_reconnect_wording(self):
        entries = activity.timeline(
            [],
            [],
            set(),
            {},
            [],
            {
                "instagram": [post("Waiting for Instagram reconnection")],
                "facebook": [post("Manual completion required")],
            },
        )
        self.assertEqual(
            [(e.platform, e.status) for e in entries],
            [("instagram", "Reconnect Instagram"), ("facebook", "Finish in the Facebook app")],
        )

    def test_undated_entries_sort_in_every_time_zone(self):
        # datetime.min/max.astimezone() overflow on one side of UTC each; sorting must not.
        previous = os.environ.get("TZ")
        self.addCleanup(time.tzset)
        self.addCleanup(
            lambda: (
                os.environ.pop("TZ", None)
                if previous is None
                else os.environ.__setitem__("TZ", previous)
            )
        )
        undated = activity.Entry("tiktok", "Posting", "", activity.DONE, "Posted")
        dated = activity.Entry(
            "tiktok", "Old", "2026-10-01T00:00:00+00:00", activity.DONE, "Posted"
        )
        for zone in ("Asia/Tokyo", "America/New_York"):
            os.environ["TZ"] = zone
            time.tzset()
            self.assertEqual(activity.recent([undated, dated]), [dated, undated])


class GroupTests(unittest.TestCase):
    def test_same_video_on_two_platforms_is_one_row(self):
        youtube = activity.Entry(
            "youtube",
            "Morning routine",
            "2026-10-02T10:00:00+00:00",
            activity.DONE,
            "Public",
            "v1",
            "https://youtu.be/v1",
            7,
        )
        tiktok = activity.Entry(
            "tiktok",
            "Morning routine #fyp",
            "2026-10-02T11:00:00+00:00",
            activity.DONE,
            "Posted",
            "b1",
            "https://t",
            7,
        )
        other = activity.Entry(
            "youtube", "Other", "2026-10-01T10:00:00+00:00", activity.DONE, "Public", video_id=8
        )
        groups = activity.grouped([tiktok, youtube, other])
        self.assertEqual(len(groups), 2)
        both = groups[0]
        self.assertEqual(both.platforms, ("youtube", "tiktok"))
        self.assertEqual(both.title, "Morning routine")  # not the hashtag caption
        self.assertEqual(both.when, "2026-10-02T11:00:00+00:00")  # latest upload
        self.assertEqual(both.status, "Uploaded")

    def test_problems_stay_separate_and_upcoming_uses_first_time(self):
        a = activity.Entry("youtube", "Clip", "", activity.ATTENTION, "Upload failed", video_id=3)
        b = activity.Entry("tiktok", "Clip", "", activity.ATTENTION, "Failed", video_id=3)
        self.assertEqual(len(activity.grouped([a, b])), 2)
        soon = activity.Entry(
            "youtube",
            "Clip",
            "2026-10-05T09:00:00+00:00",
            activity.UPCOMING,
            "Scheduled",
            video_id=3,
        )
        later = activity.Entry(
            "tiktok",
            "Clip",
            "2026-10-06T09:00:00+00:00",
            activity.UPCOMING,
            "Scheduled",
            video_id=3,
        )
        (group,) = activity.grouped([later, soon])
        self.assertEqual(group.when, soon.when)
        self.assertEqual(group.status, "Scheduled")
