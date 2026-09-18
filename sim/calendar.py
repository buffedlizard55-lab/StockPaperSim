"""Trading calendar for the competition window.

The calendar is *derived from data*, not hand-written: a date is a session if
the real FRED S&P 500 daily series carries an observation for it.  FRED
publishes the index on every day the U.S. cash market is open and leaves the
value blank on market holidays, which makes the blank rows an authoritative
closed-day list for the window.

    SOURCE: https://fred.stlouisfed.org/series/SP500
    SOURCE (cross-check): https://www.nasdaq.com/market-activity/stock-market-holiday-schedule

Cross-check performed for Season 1 (2025-09-17 .. 2026-09-16): the ten blank
rows in the bundled FRED file are exactly the ten full closures published in
Nasdaq's official trading schedule (Thanksgiving 2025-11-27, Christmas
2025-12-25, New Year 2026-01-01, MLK 2026-01-19, Presidents Day 2026-02-16,
Good Friday 2026-04-03, Memorial Day 2026-05-25, Juneteenth 2026-06-19,
Independence Day observed 2026-07-03, Labor Day 2026-09-07).  See
research/VERIFICATION_LOG.md for the line-by-line record.

IRREGULARITY (IR-01): the bundled FRED *VIXCLS* file carries values on seven
of those ten closed dates while SP500 does not.  This module treats SP500 as
the authoritative session list and drops VIX observations on non-sessions.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import config

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRED_DIR = os.path.join(REPO_ROOT, "data", "real", "fred")

# Early-close sessions (13:00 ET) inside the Season 1 window.
#   SOURCE: https://www.nasdaq.com/market-activity/stock-market-holiday-schedule
#           ("Early Close ... 1:00 p.m." rows)
# 2025-11-28 = day after Thanksgiving 2025; 2025-12-24 = Christmas Eve 2025.
EARLY_CLOSES = {"2025-11-28", "2025-12-24"}


@dataclass
class Session:
    date: str
    index: int
    open_minutes: int
    close_minutes: int
    is_early_close: bool

    @property
    def minutes(self) -> int:
        return self.close_minutes - self.open_minutes


def _read_fred_csv(path: str) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        value_col = header[1]
        for row in reader:
            if not row or not row[0]:
                continue
            raw = row[1].strip() if len(row) > 1 else ""
            out[row[0]] = float(raw) if raw else None
    return out


class TradingCalendar:
    """Session list for the competition window plus convenience lookups."""

    def __init__(self, start: str, end: str, fred_dir: str = FRED_DIR) -> None:
        self.start = start
        self.end = end
        spx_file = self._find(fred_dir, "SP500")
        vix_file = self._find(fred_dir, "VIXCLS")
        spx = _read_fred_csv(spx_file)
        vix = _read_fred_csv(vix_file)
        self.spx_source = spx_file
        self.vix_source = vix_file

        dates = sorted(d for d in spx if start <= d <= end)
        self.sessions: List[Session] = []
        self.spx: Dict[str, float] = {}
        self.vix: Dict[str, float] = {}
        self.closed_dates: List[str] = []
        self.vix_dropped_on_closed: List[str] = []

        for i, d in enumerate(dates):
            value = spx[d]
            if value is None:
                self.closed_dates.append(d)
                if vix.get(d) is not None:
                    self.vix_dropped_on_closed.append(d)
                continue
            idx = len(self.sessions)
            early = d in EARLY_CLOSES
            self.sessions.append(Session(
                date=d,
                index=idx,
                open_minutes=config.SESSION_OPEN_MINUTES,
                close_minutes=config.EARLY_CLOSE_MINUTES if early else config.SESSION_CLOSE_MINUTES,
                is_early_close=early,
            ))
            self.spx[d] = value
            if vix.get(d) is not None:
                self.vix[d] = vix[d]

        self.dates: List[str] = [s.date for s in self.sessions]
        self._pos = {d: i for i, d in enumerate(self.dates)}
        # Forward-fill VIX if a session is missing an observation (should not
        # happen in Season 1; kept defensive and reported).
        self.vix_filled = self._forward_fill(self.vix, self.dates)

    @staticmethod
    def _find(fred_dir: str, series: str) -> str:
        """The collected file with the widest coverage for ``series``.

        More than one collection window can be on disk for the same series (the
        first run fetched the competition window; Season 2's run fetched a year
        of warm-up as well), and the file names sort in an order that has
        nothing to do with coverage. Picking on the name silently truncated
        Season 2 to zero warm-up sessions, so the choice is made on the number
        of observations instead and the same file is used by both seasons.
        """
        if not os.path.isdir(fred_dir):
            raise FileNotFoundError(f"missing real data directory: {fred_dir}")
        hits = sorted(f for f in os.listdir(fred_dir) if f.startswith(series + "_"))
        if not hits:
            raise FileNotFoundError(f"no bundled FRED file for series {series} in {fred_dir}")
        best = None
        for name in hits:
            path = os.path.join(fred_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    rows = sum(1 for line in handle if line.strip()) - 1
            except OSError:
                continue
            if best is None or rows > best[0]:
                best = (rows, path)
        return best[1] if best else os.path.join(fred_dir, hits[-1])

    @staticmethod
    def _forward_fill(series: Dict[str, float], dates: List[str]) -> List[float]:
        out: List[float] = []
        last: Optional[float] = None
        for d in dates:
            if d in series:
                last = series[d]
            if last is None:
                raise ValueError(f"no VIX observation available at or before {d}")
            out.append(last)
        return out

    def __len__(self) -> int:
        return len(self.sessions)

    def session(self, i: int) -> Session:
        return self.sessions[i]

    def position(self, date: str) -> int:
        return self._pos[date]

    def spx_series(self) -> List[float]:
        return [self.spx[d] for d in self.dates]

    def vix_series(self) -> List[float]:
        return list(self.vix_filled)

    def provenance(self) -> dict:
        return {
            "spx_file": os.path.relpath(self.spx_source, REPO_ROOT),
            "vix_file": os.path.relpath(self.vix_source, REPO_ROOT),
            "sessions": len(self.sessions),
            "first_session": self.dates[0],
            "last_session": self.dates[-1],
            "full_closures_detected": self.closed_dates,
            "early_closes": sorted(d for d in self.dates if d in EARLY_CLOSES),
            "vix_observations_dropped_on_closed_dates": self.vix_dropped_on_closed,
            "spx_url": "https://fred.stlouisfed.org/series/SP500",
            "vix_url": "https://fred.stlouisfed.org/series/VIXCLS",
            "holiday_cross_check_url": "https://www.nasdaq.com/market-activity/stock-market-holiday-schedule",
        }
