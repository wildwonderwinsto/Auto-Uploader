"""Bounded local-wall-clock opportunities; gaps skipped, repeated times use first occurrence."""

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .scheduling import ScheduleError, local_choices


@dataclass(frozen=True)
class Rule:
    zone: str
    days: tuple[int, ...]
    start: str
    end: str
    hours: int = 0  # zero = daily; otherwise whole-hour interval anchored to start each day

    def validate(self) -> None:
        try:
            ZoneInfo(self.zone)
            first, last = time.fromisoformat(self.start), time.fromisoformat(self.end)
            if (
                first.tzinfo
                or last.tzinfo
                or first.second
                or last.second
                or first.microsecond
                or last.microsecond
            ):
                raise ValueError()
            if (
                first > last
                or not self.days
                or len(set(self.days)) != len(self.days)
                or any(type(d) is not int or not 0 <= d <= 6 for d in self.days)
            ):
                raise ValueError()
            if type(self.hours) is not int or not 0 <= self.hours <= 24:
                raise ValueError()
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ScheduleError(
                "Choose an IANA zone, posting days and a same-day inclusive window. Intervals are 1–24 whole hours; daily uses the start time."
            ) from exc

    def encode(self) -> dict:
        return asdict(self)

    @classmethod
    def decode(cls, value: dict) -> "Rule":
        return cls(
            value["zone"], tuple(value["days"]), value["start"], value["end"], value["hours"]
        )

    def on_day(self, day: date) -> list[datetime]:
        self.validate()
        if day.weekday() not in self.days:
            return []
        first = datetime.combine(day, time.fromisoformat(self.start))
        end = datetime.combine(day, time.fromisoformat(self.end))
        values = []
        wall = first
        while wall <= end:
            try:
                values.append(local_choices(wall.isoformat(), self.zone)[0])
            except ScheduleError:
                pass  # Nonexistent wall time; never shift it into another approved slot.
            if not self.hours:
                break
            wall += timedelta(hours=self.hours)
        return values

    def next(self, after: datetime, count: int = 8) -> list[datetime]:
        self.validate()
        if after.tzinfo is None or not 1 <= count <= 32:
            raise ScheduleError("Use an aware clock and at most 32 preview opportunities.")
        day = after.astimezone(ZoneInfo(self.zone)).date()
        result = []
        for offset in range(370):  # at most 32 weekly slots, plus rare missing local dates
            for instant in self.on_day(day + timedelta(days=offset)):
                if instant > after:
                    result.append(instant)
                    if len(result) == count:
                        return result
        raise ScheduleError("No supported future opportunities found.")
