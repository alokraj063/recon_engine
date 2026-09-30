"""
Wabtec's 4-4-5 fiscal calendar: which fiscal month and week a date falls in.

Pure date arithmetic, no I/O. A fiscal year is 52 (sometimes 53) Monday-
to-Sunday weeks ending on the LAST SUNDAY OF DECEMBER, and its twelve
months take their weeks from `pattern` (4-4-5 per quarter). A 53-week
year gives its extra week to the last month. Derived from the team's own
Daily Collection files (2026-09-30): fiscal Jul 26 = 29 Jun-26 Jul,
Aug 26 = 27 Jul-23 Aug, Sep 26 = 24 Aug-27 Sep (5 weeks), i.e. fiscal
2026 starts Mon 29 Dec 2025.

`year_starts` overrides the rule for a year whose start Finance moved
({"2027": "2027-01-04"}); a year not listed follows the rule.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence

DEFAULT_PATTERN = (4, 4, 5, 4, 4, 5, 4, 4, 5, 4, 4, 5)
MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
ORDINALS = ("1st", "2nd", "3rd", "4th", "5th", "6th")


class FiscalCalendarError(ValueError):
    pass


@dataclass(frozen=True)
class FiscalPeriod:
    year: int            # fiscal year (2026)
    month: int           # 1..12
    week: int            # week within the fiscal month, 1-based
    month_start: date
    month_end: date      # inclusive (a Sunday)

    @property
    def month_label(self) -> str:
        """The team's sheet name: "Sep 26"."""
        return f"{MONTH_ABBR[self.month - 1]} {self.year % 100:02d}"

    @property
    def week_label(self) -> str:
        return f"{ORDINALS[self.week - 1]} Week"


def _last_sunday_of_december(year: int) -> date:
    d = date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - 6) % 7)


def _as_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


class FiscalCalendar:
    def __init__(self, pattern: Sequence[int] = DEFAULT_PATTERN,
                 year_starts: Optional[Dict[str, str]] = None):
        pattern = tuple(int(p) for p in pattern)
        if len(pattern) != 12 or any(p < 1 or p > 6 for p in pattern):
            raise FiscalCalendarError(
                "pattern needs 12 month lengths of 1-6 weeks each")
        if sum(pattern) != 52:
            raise FiscalCalendarError(
                f"pattern must add up to 52 weeks, got {sum(pattern)}")
        self.pattern = pattern
        self.year_starts: Dict[int, date] = {}
        for y, d in (year_starts or {}).items():
            start = _as_date(d)
            if start.weekday() != 0:
                raise FiscalCalendarError(f"{y}: {start} is not a Monday")
            self.year_starts[int(y)] = start

    @classmethod
    def from_config(cls, cfg: Optional[dict]) -> "FiscalCalendar":
        cfg = cfg or {}
        return cls(cfg.get("pattern") or DEFAULT_PATTERN,
                   cfg.get("year_starts") or {})

    def year_start(self, year: int) -> date:
        if year in self.year_starts:
            return self.year_starts[year]
        return _last_sunday_of_december(year - 1) + timedelta(days=1)

    def year_end(self, year: int) -> date:
        return self.year_start(year + 1) - timedelta(days=1)

    def month_bounds(self, year: int) -> List[tuple]:
        """[(start, end)] for the year's 12 months; a 53rd week (or more,
        after an override) goes to the last month."""
        start = self.year_start(year)
        weeks = (self.year_end(year) - start).days // 7 + 1
        lengths = list(self.pattern)
        lengths[-1] += weeks - sum(lengths)
        out = []
        for n in lengths:
            end = start + timedelta(days=7 * n - 1)
            out.append((start, end))
            start = end + timedelta(days=1)
        return out

    def period(self, d) -> FiscalPeriod:
        d = _as_date(d)
        year = d.year + 1 if d >= self.year_start(d.year + 1) else d.year
        if d < self.year_start(year):
            year -= 1
        for i, (start, end) in enumerate(self.month_bounds(year)):
            if start <= d <= end:
                return FiscalPeriod(year, i + 1, (d - start).days // 7 + 1,
                                    start, end)
        raise FiscalCalendarError(f"{d} falls in no fiscal month")  # unreachable


def to_config(cal: FiscalCalendar) -> dict:
    return {"pattern": list(cal.pattern),
            "year_starts": {str(y): d.isoformat()
                            for y, d in sorted(cal.year_starts.items())}}
