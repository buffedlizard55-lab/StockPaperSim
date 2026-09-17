"""The trading calendar is built from real FRED observations.

Every assertion here is checked against the published holiday schedule
(https://www.nasdaq.com/market-activity/stock-market-holiday-schedule) and
against the raw CSV files in data/real/fred/, read independently of
sim.calendar so that a parsing bug cannot cancel itself out.
"""

from __future__ import annotations

import datetime as dt
import os
import unittest

from fixtures import REPO_ROOT, cfg, fred_csv_rows

from sim import config
from sim.calendar import EARLY_CLOSES, TradingCalendar


# Official 2025-2026 US equity market closures that fall inside the Season 1
# window (2025-09-17 .. 2026-09-16).
#   SOURCE: https://www.nasdaq.com/market-activity/stock-market-holiday-schedule
OFFICIAL_CLOSURES_IN_WINDOW = [
    "2025-11-27",   # Thanksgiving Day
    "2025-12-25",   # Christmas
    "2026-01-01",   # New Year's Day
    "2026-01-19",   # Martin Luther King, Jr. Day
    "2026-02-16",   # Washington's Birthday (Presidents Day)
    "2026-04-03",   # Good Friday
    "2026-05-25",   # Memorial Day
    "2026-06-19",   # Juneteenth
    "2026-07-03",   # Independence Day (observed; 2026-07-04 is a Saturday)
    "2026-09-07",   # Labor Day
]

# Official early closes (1:00 p.m. ET) inside the window.
OFFICIAL_EARLY_CLOSES_IN_WINDOW = ["2025-11-28", "2025-12-24"]


class TestCalendarConstruction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c = cfg()
        cls.cal = TradingCalendar(c.start, c.end)
        cls.start, cls.end = c.start, c.end

    def test_sessions_are_weekdays_inside_the_window(self):
        self.assertGreater(len(self.cal), 200, "a one-year season needs ~251 sessions")
        for s in self.cal.sessions:
            self.assertTrue(self.start <= s.date <= self.end, s.date)
            weekday = dt.date.fromisoformat(s.date).weekday()
            self.assertLess(weekday, 5, f"{s.date} is a weekend")

    def test_dates_strictly_increasing_and_unique(self):
        self.assertEqual(self.cal.dates, sorted(set(self.cal.dates)))

    def test_position_lookup_round_trips(self):
        for i, s in enumerate(self.cal.sessions):
            self.assertEqual(self.cal.position(s.date), i)
            self.assertIs(self.cal.session(i), s)

    def test_official_closures_are_not_sessions(self):
        sessions = set(self.cal.dates)
        for date in OFFICIAL_CLOSURES_IN_WINDOW:
            self.assertNotIn(date, sessions, f"{date} is an official closure")
            self.assertIn(date, self.cal.closed_dates,
                          f"{date} should be recorded as a detected closure")

    def test_official_early_closes_are_short_sessions(self):
        by_date = {s.date: s for s in self.cal.sessions}
        for date in OFFICIAL_EARLY_CLOSES_IN_WINDOW:
            self.assertIn(date, by_date)
            s = by_date[date]
            self.assertTrue(s.is_early_close)
            self.assertEqual(s.minutes, config.EARLY_CLOSE_MINUTES - config.SESSION_OPEN_MINUTES)
        self.assertEqual(set(EARLY_CLOSES), set(OFFICIAL_EARLY_CLOSES_IN_WINDOW))

    def test_regular_session_length_is_9_30_to_16_00_et(self):
        for s in self.cal.sessions:
            if not s.is_early_close:
                self.assertEqual(s.open_minutes, config.SESSION_OPEN_MINUTES)   # 570 = 09:30
                self.assertEqual(s.close_minutes, config.SESSION_CLOSE_MINUTES)  # 960 = 16:00
                self.assertEqual(s.minutes, 390)

    def test_no_session_is_missing_an_spx_value(self):
        raw = fred_csv_rows("SP500")
        for date in self.cal.dates:
            self.assertIsNotNone(raw.get(date), f"{date} has no real SP500 print")
            self.assertAlmostEqual(self.cal.spx[date], raw[date], places=6)

    def test_vix_observations_on_closed_dates_are_dropped_and_counted(self):
        """IR-01: VIXCLS prints on some dates where SP500 is blank."""
        spx, vix = fred_csv_rows("SP500"), fred_csv_rows("VIXCLS")
        expected = sorted(d for d in vix
                          if self.start <= d <= self.end
                          and vix[d] is not None
                          and spx.get(d) is None)
        self.assertEqual(sorted(self.cal.vix_dropped_on_closed), expected)
        self.assertEqual(len(expected), 7,
                         "IR-01 documents exactly 7 such dates in Season 1")
        for d in expected:
            self.assertNotIn(d, set(self.cal.dates))

    def test_every_session_has_a_real_vix_value(self):
        raw = fred_csv_rows("VIXCLS")
        for date, value in self.cal.vix.items():
            self.assertIsNotNone(value)
            self.assertGreater(value, 0.0)
            if raw.get(date) is not None:
                self.assertAlmostEqual(value, raw[date], places=6)

    def test_series_helpers_align_with_sessions(self):
        self.assertEqual(len(self.cal.spx_series()), len(self.cal))
        self.assertEqual(len(self.cal.vix_series()), len(self.cal))
        self.assertEqual(self.cal.spx_series(), [self.cal.spx[d] for d in self.cal.dates])

    def test_provenance_records_the_real_files_and_urls(self):
        prov = self.cal.provenance()
        self.assertTrue(prov["spx_file"].endswith(".csv"))
        # Paths are recorded relative to the repository root.
        self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, prov["spx_file"])),
                        prov["spx_file"])
        self.assertTrue(os.path.exists(os.path.join(REPO_ROOT, prov["vix_file"])))
        self.assertIn("fred.stlouisfed.org", prov["spx_url"])
        self.assertIn("fred.stlouisfed.org", prov["vix_url"])
        self.assertIn("nasdaq.com", prov["holiday_cross_check_url"])
        self.assertEqual(prov["sessions"], len(self.cal))
        self.assertEqual(prov["first_session"], self.cal.dates[0])
        self.assertEqual(prov["last_session"], self.cal.dates[-1])
        self.assertEqual(prov["vix_observations_dropped_on_closed_dates"],
                         self.cal.vix_dropped_on_closed)
        self.assertEqual(prov["full_closures_detected"], self.cal.closed_dates)
        self.assertEqual(prov["early_closes"], OFFICIAL_EARLY_CLOSES_IN_WINDOW)
        # Every date on which VIX printed but SP500 did not must be an official
        # market closure - otherwise the two series disagree about the calendar.
        for date in self.cal.vix_dropped_on_closed:
            self.assertIn(date, OFFICIAL_CLOSURES_IN_WINDOW,
                          f"{date}: VIX printed on a date that is not an official closure")

    def test_detected_closures_match_the_official_schedule_exactly(self):
        """No extra closures invented, none of the official ones missed."""
        raw = fred_csv_rows("SP500")
        gaps_from_data = sorted(d for d in raw
                                if self.start <= d <= self.end and raw[d] is None)
        self.assertEqual(sorted(self.cal.closed_dates), gaps_from_data)
        for official in OFFICIAL_CLOSURES_IN_WINDOW:
            self.assertIn(official, gaps_from_data,
                          f"{official} is an official closure but FRED has a value")

    def test_missing_data_directory_is_a_loud_error(self):
        with self.assertRaises(FileNotFoundError):
            TradingCalendar(self.start, self.end,
                            fred_dir=os.path.join(REPO_ROOT, "data", "nope"))


if __name__ == "__main__":
    unittest.main()
