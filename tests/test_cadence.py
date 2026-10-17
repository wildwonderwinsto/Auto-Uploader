import unittest
from datetime import UTC, date, datetime

from shortspublisher.cadence import Rule
from shortspublisher.scheduling import ScheduleError


class CadenceTests(unittest.TestCase):
    def rule(self, **kwargs):
        return Rule(
            **(
                {
                    "zone": "UTC",
                    "days": tuple(range(7)),
                    "start": "09:00",
                    "end": "21:00",
                    "hours": 2,
                }
                | kwargs
            )
        )

    def test_two_hours_inclusive(self):
        self.assertEqual(
            [t.hour for t in self.rule().on_day(date(2030, 1, 1))], [9, 11, 13, 15, 17, 19, 21]
        )

    def test_daily(self):
        self.assertEqual(len(self.rule(hours=0).on_day(date(2030, 1, 1))), 1)

    def test_three_hours(self):
        self.assertEqual(
            [t.hour for t in self.rule(hours=3).on_day(date(2030, 1, 1))], [9, 12, 15, 18, 21]
        )

    def test_four_hours(self):
        self.assertEqual(
            [t.hour for t in self.rule(hours=4).on_day(date(2030, 1, 1))], [9, 13, 17, 21]
        )

    def test_custom_hours(self):
        self.assertEqual([t.hour for t in self.rule(hours=5).on_day(date(2030, 1, 1))], [9, 14, 19])

    def test_single_hour(self):
        self.assertEqual(len(self.rule(hours=1).on_day(date(2030, 1, 1))), 13)

    def test_weekdays(self):
        self.assertFalse(self.rule(days=(0, 1, 2, 3, 4)).on_day(date(2030, 1, 5)))

    def test_weekends(self):
        self.assertFalse(self.rule(days=(5, 6)).on_day(date(2030, 1, 1)))

    def test_custom_days(self):
        self.assertEqual(len(self.rule(days=(1,)).on_day(date(2030, 1, 1))), 7)

    def test_zone_conversion(self):
        self.assertEqual(self.rule(zone="America/Chicago").on_day(date(2030, 1, 1))[0].hour, 15)

    def test_spring_gap(self):
        self.assertEqual(
            self.rule(zone="America/Chicago", start="02:30", end="02:30").on_day(date(2030, 3, 10)),
            [],
        )

    def test_fall_first_only(self):
        values = self.rule(zone="America/Chicago", start="01:30", end="01:30").on_day(
            date(2030, 11, 3)
        )
        self.assertEqual(values, [datetime(2030, 11, 3, 6, 30, tzinfo=UTC)])

    def test_half_hour_zone(self):
        self.assertEqual(
            self.rule(zone="Asia/Kolkata").on_day(date(2030, 1, 1))[0].isoformat(),
            "2030-01-01T03:30:00+00:00",
        )

    def test_boundary_strict_next(self):
        self.assertEqual(self.rule().next(datetime(2030, 1, 1, 9, tzinfo=UTC), 1)[0].hour, 11)

    def test_next_week(self):
        self.assertEqual(
            self.rule(days=(1,), hours=0).next(datetime(2030, 1, 1, 9, tzinfo=UTC), 1)[0].day, 8
        )

    def test_repeated_clock_calculation_stable(self):
        clock = datetime(2030, 1, 1, tzinfo=UTC)
        self.assertEqual(self.rule().next(clock), self.rule().next(clock))

    def test_invalid_windows_and_intervals(self):
        for options in (
            {"start": "22:00", "end": "09:00"},
            {"hours": 25},
            {"hours": -1},
            {"hours": 1.5},
            {"days": ()},
            {"days": (7,)},
            {"zone": "unknown"},
            {"start": "09:00:01"},
        ):
            with self.subTest(options=options), self.assertRaises(ScheduleError):
                self.rule(**options).validate()

    def test_naive_clock_rejected(self):
        with self.assertRaises(ScheduleError):
            self.rule().next(datetime(2030, 1, 1))

    def test_preview_bounded(self):
        with self.assertRaises(ScheduleError):
            self.rule().next(datetime(2030, 1, 1, tzinfo=UTC), 1000)

    def test_rule_roundtrip(self):
        self.assertEqual(Rule.decode(self.rule().encode()), self.rule())
