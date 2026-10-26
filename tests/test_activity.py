import os
import time
import unittest
from dataclasses import replace
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

    def test_processing_upload_is_done_and_titled_from_its_details(self):
        # Upload records keep the title in the payload sent to YouTube, not as "metadata".
        record = replace(
            attempt("Processing", "vid1", processing="processing"),
            snapshot={
                "filename": "Welcome.mp4",
                "payload": {"snippet": {"title": "Welcome!"}},
                "size": 200,
            },
        )
        entry = activity.youtube_uploads([record])[0]
        self.assertEqual(
            (entry.kind, entry.status, entry.title),
            (activity.DONE, "Public · YouTube is processing", "Welcome!"),
        )
        posted = activity.buffer_posts(
            "tiktok", [post("Sent by Buffer", video_id=1, sent_at="2026-10-01T09:00:00+00:00")]
        )
        self.assertEqual(len(activity.grouped([entry, *posted])), 1)  # one row, two logos

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


class FolderWaitingTests(unittest.TestCase):
    def profile(self, **values):
        return {"id": "p", "enabled": 1, "revoked": 0, "removed": 0, "revision": 2, **values}

    def decision(self, name, video_id, state="Waiting for next opportunity", revision=2):
        return {
            "name": name,
            "video_id": video_id,
            "state": state,
            "profile_revision": revision,
        }

    def test_buffer_platforms_join_youtube_in_the_waiting_row(self):
        decisions = {
            "p": [  # newest first, as stored
                self.decision("b.mp4", 2),
                self.decision("a.mp4", 1),
                self.decision("sent.mp4", 9, state="Enrolled"),
                self.decision("old.mp4", 8, revision=1),
            ]
        }
        waiting = activity.folder_waiting("tiktok", [self.profile()], decisions)
        self.assertEqual([(e.title, e.video_id) for e in waiting], [("a", 1), ("b", 2)])
        youtube = activity.Entry(
            "youtube", "a", "", activity.UPCOMING, "Waiting in queue", video_id=1
        )
        groups = activity.grouped([youtube, *waiting])
        self.assertEqual(groups[0].platforms, ("youtube", "tiktok"))
        self.assertEqual(groups[0].status, "Waiting in queue")

    def test_paused_or_removed_auto_uploads_are_not_listed(self):
        decisions = {"p": [self.decision("a.mp4", 1)]}
        for profile in (self.profile(enabled=0), self.profile(removed=1), self.profile(revoked=1)):
            self.assertEqual(activity.folder_waiting("tiktok", [profile], decisions), [])
