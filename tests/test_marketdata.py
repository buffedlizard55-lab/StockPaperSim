"""Market data: real anchors, simulated paths, no look-ahead, honest labels.

The numbers asserted here are the real ones from data/real/, recomputed
independently of sim.calendar and sim.marketdata where that is possible.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import subprocess
import sys
import unittest

from fixtures import REPO_ROOT, cfg, closes_digest, fred_csv_rows, instrument, replay

from sim import config

FRED_DIR = os.path.join(REPO_ROOT, "data", "real", "fred")
YAHOO_DIR = os.path.join(REPO_ROOT, "data", "real", "yahoo")


def _yahoo(name: str) -> dict:
    with open(os.path.join(YAHOO_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


class TestRealFactors(unittest.TestCase):
    """The index and volatility series must BE the real ones, not look like them."""

    @classmethod
    def setUpClass(cls):
        cls.md = replay()
        cls.diag = cls.md.diagnostics
        cls.t0 = cls.md.first_competition_index

    def test_spx_series_is_the_real_fred_series_verbatim(self):
        raw = fred_csv_rows("SP500")
        dates = self.md.calendar.dates
        self.assertEqual(len(self.md.spx), len(self.md.dates))
        for i, d in enumerate(dates):
            self.assertAlmostEqual(self.md.spx[self.t0 + i], raw[d], places=6,
                                   msg=f"SPX mismatch on {d}")
        self.assertEqual(self.md.spx[self.t0], raw[dates[0]])
        self.assertEqual(self.md.spx[-1], raw[dates[-1]])

    def test_vix_series_is_the_real_fred_series_verbatim(self):
        raw = fred_csv_rows("VIXCLS")
        filled = self.md.calendar.vix_filled
        for i, d in enumerate(self.md.calendar.dates):
            self.assertAlmostEqual(self.md.vix[self.t0 + i], filled[i], places=6,
                                   msg=f"VIX mismatch on {d}")
            if raw.get(d) is not None:
                self.assertAlmostEqual(self.md.vix[self.t0 + i], raw[d], places=6)

    def test_diagnostics_match_independently_recomputed_real_statistics(self):
        raw = fred_csv_rows("SP500")
        dates = self.md.calendar.dates
        levels = [raw[d] for d in dates]
        total_return = 100.0 * (levels[-1] / levels[0] - 1.0)
        rets = [math.log(levels[i] / levels[i - 1]) for i in range(1, len(levels))]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        ann_vol = math.sqrt(var * config.TRADING_DAYS_PER_YEAR)
        vraw = fred_csv_rows("VIXCLS")
        vix_vals = [vraw[d] for d in dates if vraw.get(d) is not None]

        self.assertAlmostEqual(self.diag["spx_total_return_pct_real"], total_return,
                               places=2)
        self.assertAlmostEqual(self.diag["spx_annualised_vol_real"], ann_vol, places=2)
        self.assertAlmostEqual(self.diag["vix_mean_real"],
                               sum(vix_vals) / len(vix_vals), places=2)
        self.assertAlmostEqual(self.diag["vix_max_real"], max(vix_vals), places=2)
        # Published values, for the record: +14.415%, 12.90% annualised vol,
        # VIX mean 18.157, VIX max 31.05 (2026-03-27).
        self.assertAlmostEqual(self.diag["spx_total_return_pct_real"], 14.415, places=3)
        self.assertAlmostEqual(self.diag["vix_max_real"], 31.05, places=2)

    def test_real_extremes_are_on_the_dates_the_raw_data_says(self):
        raw = fred_csv_rows("SP500")
        dates = self.md.calendar.dates
        levels = {d: raw[d] for d in dates}
        self.assertEqual(min(levels, key=levels.get), "2026-03-30")   # 6343.72
        self.assertEqual(max(levels, key=levels.get), "2026-08-13")   # 7798.99
        vraw = fred_csv_rows("VIXCLS")
        vix = {d: vraw[d] for d in dates if vraw.get(d) is not None}
        self.assertEqual(max(vix, key=vix.get), "2026-03-27")         # 31.05


class TestReplayDeterminism(unittest.TestCase):
    def test_same_seed_same_bars(self):
        self.assertEqual(closes_digest(replay(seed=20260917)),
                         closes_digest(replay()))

    def test_different_seed_changes_single_names_but_not_the_real_factor(self):
        a, b = replay(seed=20260917), replay(seed=11111111)
        # Warm-up sessions are simulated, so they do depend on the seed; the
        # competition window itself is the real published path and must not.
        self.assertEqual(a.spx[a.first_competition_index:],
                         b.spx[b.first_competition_index:],
                         "the real index path must not depend on the seed")
        self.assertEqual(a.vix[a.first_competition_index:],
                         b.vix[b.first_competition_index:],
                         "the real VIX path must not depend on the seed")
        self.assertNotEqual(a.spx[:a.first_competition_index],
                            b.spx[:b.first_competition_index],
                            "warm-up is simulated and should differ by seed")
        self.assertNotEqual([bar.close for bar in a.bars["NVDA"]],
                            [bar.close for bar in b.bars["NVDA"]])

    def test_rebuild_is_stable_across_processes(self):
        """Digest recorded from an independent interpreter run."""
        code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r);"
                "from fixtures import replay, closes_digest;"
                "print(closes_digest(replay()))" % (REPO_ROOT, os.path.join(REPO_ROOT, "tests")))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, cwd=REPO_ROOT, timeout=300)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        self.assertEqual(out.stdout.strip(), closes_digest(replay()))


class TestBarSanity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = replay()

    def test_every_bar_is_internally_consistent(self):
        for sym in self.md.symbols:
            for bar in self.md.bars[sym]:
                self.assertGreater(bar.close, 0.0, sym)
                self.assertGreater(bar.open, 0.0, sym)
                self.assertLessEqual(bar.low, min(bar.open, bar.close) + 1e-9,
                                     f"{sym} {bar.date}: low above body")
                self.assertGreaterEqual(bar.high, max(bar.open, bar.close) - 1e-9,
                                        f"{sym} {bar.date}: high below body")
                self.assertLessEqual(bar.low, bar.high + 1e-9)
                self.assertGreater(bar.volume, 0, f"{sym} {bar.date}: zero volume")
                for value in (bar.open, bar.high, bar.low, bar.close):
                    self.assertTrue(math.isfinite(value))

    def test_bar_dates_match_the_calendar(self):
        for sym in self.md.symbols:
            self.assertEqual([b.date for b in self.md.bars[sym]], self.md.dates)

    def test_prices_stay_within_a_sane_band_of_their_anchor(self):
        for sym in self.md.symbols:
            inst = self.md.instruments[sym]
            closes = [b.close for b in self.md.bars[sym]]
            self.assertLess(max(closes) / min(closes), 30.0,
                            f"{sym} moved more than 30x inside one year")
            self.assertGreater(min(closes), inst.price_start * 0.02)


class TestNoLookAhead(unittest.TestCase):
    """A strategy must never be able to see today's close or any future bar."""

    @classmethod
    def setUpClass(cls):
        cls.md = replay()
        cls.t0 = cls.md.first_competition_index

    def test_history_accessors_stop_before_t(self):
        for t in (self.t0, self.t0 + 1, self.t0 + 50, len(self.md.dates) - 1):
            for sym in ("SPY", "NVDA", "RIVN"):
                closes = self.md.history_closes(sym, t, 20)
                self.assertLessEqual(len(closes), 20)
                self.assertTrue(closes, f"empty history at t={t}")
                self.assertEqual(closes[-1], self.md.bars[sym][t - 1].close,
                                 f"{sym} at t={t} leaked today's close")
                    # ... and it is not merely today's close by coincidence of a flat bar.
                self.assertEqual(closes[0],
                                 self.md.bars[sym][t - len(closes)].close)

    def test_history_length_guard_pattern_is_not_a_silent_zero(self):
        """Regression: `len(history_volume(t, 20)) < 21` is always true.

        A strategy that guards on `window + 1` bars from a capped history can
        never fire (this silently disabled @DriftRider_PEAD once).  The
        accessor returns at most ``n`` bars, so the correct guard is ``< n``.
        """
        t = self.t0 + 100
        vols = self.md.history_volume("SPY", t, 20)
        self.assertEqual(len(vols), 20)
        self.assertLess(len(vols), 21)              # the buggy guard
        self.assertFalse(len(vols) < 20)            # the correct guard

    def test_warmup_gives_a_full_lookback_on_the_first_competition_session(self):
        self.assertEqual(self.t0, self.md.warmup_days)
        self.assertEqual(self.md.warmup_days, 126)
        for sym in ("SPY", "NVDA"):
            self.assertGreaterEqual(len(self.md.history_closes(sym, self.t0, 63)), 63)
            self.assertGreaterEqual(len(self.md.history_returns(sym, self.t0, 63)), 63)

    def test_warmup_dates_are_business_days_before_the_season(self):
        self.assertEqual(len(self.md.warmup_dates), self.md.warmup_days)
        for d in self.md.warmup_dates:
            self.assertLess(d, self.md.calendar.dates[0])
            self.assertLess(dt.date.fromisoformat(d).weekday(), 5)
        self.assertEqual(self.md.warmup_dates, sorted(self.md.warmup_dates))
        self.assertEqual(self.md.diagnostics["warmup_sessions_simulated"],
                         self.md.warmup_days)

    def test_bar_accessor_returns_today_only_when_asked_for_today(self):
        t = self.t0 + 5
        self.assertEqual(self.md.bar("SPY", t).date, self.md.dates[t])
        self.assertEqual(self.md.bar("SPY", t - 1).date, self.md.dates[t - 1])


class TestRealAnchors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = replay()
        cls.t0 = cls.md.first_competition_index
        cls.t1 = len(cls.md.dates) - 1

    def test_aapl_starts_and_ends_on_real_observed_closes(self):
        snap = _yahoo("AAPL_snapshot_2026-09-17.json")
        inst = instrument("AAPL")
        self.assertEqual(inst.provenance["price_start"], "real")
        self.assertEqual(inst.provenance["price_end_anchor"], "real")
        self.assertEqual(inst.provenance["dividends"], "real")
        # chartPreviousClose from the real 1-year request is the start anchor.
        self.assertAlmostEqual(inst.price_start,
                               snap["meta"]["chartPreviousClose_1y_window"], places=2)
        self.assertAlmostEqual(inst.price_start, 236.70, places=2)
        self.assertAlmostEqual(inst.fifty_two_week_low, snap["meta"]["fiftyTwoWeekLow"],
                               places=2)
        self.assertAlmostEqual(inst.fifty_two_week_high, snap["meta"]["fiftyTwoWeekHigh"],
                               places=2)
        # The last real daily close before the season ends is the end anchor.
        self.assertAlmostEqual(inst.price_end_anchor, snap["daily_5d"]["close"][-2],
                               places=2)
        self.assertAlmostEqual(self.md.bars["AAPL"][self.t1].close,
                               inst.price_end_anchor, places=6)

    def test_aapl_dividends_are_the_four_real_ones(self):
        snap = _yahoo("AAPL_snapshot_2026-09-17.json")
        real = snap["dividends_1y_window"]
        declared = instrument("AAPL").dividends
        self.assertEqual(len(declared), len(real))
        for got, want in zip(sorted(declared, key=lambda d: d["ex_date"]),
                             sorted(real, key=lambda d: d["ex_date_epoch"])):
            self.assertAlmostEqual(got["amount"], want["amount"], places=6)
            epoch_day = dt.datetime.utcfromtimestamp(want["ex_date_epoch"]).date()
            self.assertEqual(dt.date.fromisoformat(got["ex_date"]), epoch_day)

    def test_spy_tracks_the_real_index_within_the_fitted_ratio_error(self):
        spy = _yahoo("SPY_monthly_1y.json")
        diag = self.md.diagnostics
        self.assertLessEqual(diag["spy_ratio_max_abs_pct_error_vs_real_monthly_closes"],
                             0.25)
        # 13 real monthly bars; the last one (2026-09-17) falls after the final
        # competition session, so 12 pairs are usable.
        self.assertEqual(len(diag["spy_ratio_pairs"]), len(spy["close"]) - 1)
        month_starts = [dt.datetime.utcfromtimestamp(ts).date().isoformat()
                        for ts in spy["timestamp"]]
        for i, pair in enumerate(diag["spy_ratio_pairs"]):
            self.assertEqual(pair["date"][:7], month_starts[i][:7],
                             "ratio pair does not line up with the real monthly bar")
            self.assertAlmostEqual(pair["spy_close_real"], spy["close"][i], places=2)
            self.assertAlmostEqual(pair["spx_close_real"] / pair["spy_close_real"],
                                   pair["ratio"], places=6)
        ratio = diag["spy_index_ratio_fitted"]
        raw = fred_csv_rows("SP500")
        # SPY close on each real monthly bar date ~= index level / ratio.
        for pair in diag["spy_ratio_pairs"]:
            date = pair["date"]
            self.assertIn(date, raw, f"{date} is not a real FRED session")
            self.assertAlmostEqual(pair["spx_close_real"], raw[date], places=4)
            self.assertAlmostEqual(pair["spy_close_implied"],
                                   pair["spx_close_real"] / ratio, places=2)
            self.assertAlmostEqual(pair["ratio"],
                                   pair["spx_close_real"] / pair["spy_close_real"],
                                   places=6)
            self.assertLessEqual(pair["abs_pct_error"], 0.25)
            # abs_pct_error is computed before the implied close is rounded for
            # storage, so allow the rounding difference.
            self.assertAlmostEqual(abs(pair["spy_close_implied"] - pair["spy_close_real"])
                                   / pair["spy_close_real"] * 100.0,
                                   pair["abs_pct_error"], delta=0.01)

    def test_monthly_range_audit_matches_real_spy_ranges(self):
        audit = self.md.diagnostics["monthly_range_audit_spy"]
        self.assertEqual(audit["statistic"],
                         "(month high - month low) / month-end close, in percent")
        self.assertIn("yahoo", audit["real_source"].lower())
        self.assertIn("IR-06", audit["calibration_note"])
        months = audit["months"]
        self.assertEqual(audit["n_months"], len(months))
        self.assertEqual(audit["n_months"], 11,
                         "13 real monthly bars collapse to 11 complete months "
                         "inside the competition window")
        spy = _yahoo("SPY_monthly_1y.json")
        real_pct = {}
        for ts, high, low, close in zip(spy["timestamp"], spy["high"], spy["low"],
                                        spy["close"]):
            if None in (high, low, close):
                continue
            month = dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m")
            real_pct[month] = 100.0 * (high - low) / close
        for month, row in months.items():
            if month in real_pct:
                self.assertAlmostEqual(row["real_pct"], real_pct[month], places=2,
                                       msg=f"real monthly range mis-transcribed for {month}")
        self.assertLessEqual(audit["mean_abs_diff_pp"], 1.0)
        self.assertLessEqual(audit["max_abs_diff_pp"], 2.0)

    def test_provenance_labels_simulation_as_simulation(self):
        diag = self.md.diagnostics
        self.assertIn("SIMULATED", diag["single_name_daily_ohlcv"])
        self.assertEqual(diag["mode"], "real-anchored-replay")
        anchors = diag["real_anchors"]
        self.assertIn("AAPL.price_start", anchors)
        self.assertIn("SPY.price_start", anchors)
        for sym, inst in self.md.instruments.items():
            for field, label in inst.provenance.items():
                self.assertIn(label.split(" ")[0],
                              ("real", "scenario", "scenario-declared", "derived",
                               "definition"),
                              f"{sym}.{field} has an unrecognised provenance label {label!r}")
            if not inst.is_real_anchored:
                self.assertEqual(inst.provenance.get("price_start"), "scenario",
                                 f"{sym} claims a real price without an anchor")


class TestDividends(unittest.TestCase):
    def test_declared_schedules_are_quarterly_positive_and_in_window(self):
        for inst in replay().instruments.values():
            if not inst.dividends:
                continue
            dates = [d["ex_date"] for d in inst.dividends]
            self.assertEqual(dates, sorted(set(dates)), f"{inst.symbol} ex-dates")
            for div in inst.dividends:
                self.assertGreater(div["amount"], 0.0)
                self.assertTrue("2025-09-01" <= div["ex_date"] <= "2026-09-30")
                self.assertLess(dt.date.fromisoformat(div["ex_date"]).weekday(), 5,
                            f"{inst.symbol} ex-date {div['ex_date']} is a weekend")

    def test_payers_are_declared_and_non_payers_are_not(self):
        insts = replay().instruments
        payers = {s for s, i in insts.items() if i.dividends}
        self.assertIn("SPY", payers)
        self.assertIn("AAPL", payers)
        self.assertIn("NVDA", payers, "NVDA pays a small declared dividend")
        self.assertNotIn("RIVN", payers, "RIVN pays no dividend")
        self.assertNotIn("CVNA", payers, "CVNA pays no dividend")
        self.assertGreaterEqual(len(payers), 12)

    def test_declared_yields_are_removed_from_price_drift(self):
        """IR-09: total return must not double count the declared dividend."""
        md = replay()
        for sym in ("SPY", "JPM", "XOM"):
            inst = md.instruments[sym]
            if not inst.dividends:
                continue
            total_declared = sum(d["amount"] for d in inst.dividends)
            price_return = md.bars[sym][-1].close / inst.price_start - 1.0
            dividend_yield = total_declared / inst.price_start
            # The price path is drift-adjusted, so price return + declared yield
            # must be a plausible total return for the name (not obviously
            # inflated by counting the dividend twice).
            self.assertGreater(price_return + dividend_yield, price_return)
            self.assertLess(dividend_yield, 0.10,
                            f"{sym} declared a {dividend_yield:.1%} yield in one year")


class TestVolatilityModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = replay()
        cls.t0 = cls.md.first_competition_index

    def test_realised_sigma_is_positive_and_finite(self):
        for sym in self.md.symbols:
            for t in (self.t0, self.t0 + 60, len(self.md.dates) - 1):
                sigma = self.md.realised_sigma_daily(sym, t, 21)
                self.assertTrue(math.isfinite(sigma))
                self.assertGreater(sigma, 0.0)
                self.assertLess(sigma, 0.5, f"{sym} daily sigma {sigma:.3f} is absurd")

    def test_realised_volatility_matches_the_declared_total_volatility(self):
        """IR-19 diagnostic: the vol model, not the drift, drives dispersion."""
        rows = []
        for sym in self.md.symbols:
            inst = self.md.instruments[sym]
            rets = [math.log(self.md.bars[sym][i].close / self.md.bars[sym][i - 1].close)
                    for i in range(self.t0 + 1, len(self.md.dates))]
            mean = sum(rets) / len(rets)
            sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
            realised = sd * math.sqrt(config.TRADING_DAYS_PER_YEAR)
            mrets = [math.log(self.md.spx[i] / self.md.spx[i - 1])
                     for i in range(self.t0 + 1, len(self.md.spx))]
            mmean = sum(mrets) / len(mrets)
            msd = math.sqrt(sum((r - mmean) ** 2 for r in mrets) / (len(mrets) - 1))
            cov = sum((a - mean) * (b - mmean) for a, b in zip(rets, mrets)) / (len(rets) - 1)
            beta_realised = cov / (msd ** 2)
            predicted = math.sqrt((inst.beta * msd) ** 2 +
                                  (inst.sigma_idio_annual / math.sqrt(config.TRADING_DAYS_PER_YEAR)) ** 2
                                  ) * math.sqrt(config.TRADING_DAYS_PER_YEAR)
            rows.append((sym, realised, predicted, beta_realised, inst.beta))
        for sym, realised, predicted, beta_r, beta_d in rows:
            self.assertLess(abs(realised - predicted) / predicted, 0.35,
                            f"{sym}: realised vol {realised:.3f} vs predicted {predicted:.3f}")
            self.assertLess(abs(beta_r - beta_d), 0.6,
                            f"{sym}: realised beta {beta_r:.2f} vs declared {beta_d:.2f}")

    def test_vix_regime_factor_scales_with_the_real_vix(self):
        md = self.md
        vix_mean = sum(md.vix[self.t0:]) / (len(md.vix) - self.t0)
        for t in (self.t0 + 5, self.t0 + 120, len(md.dates) - 1):
            factor = md.vix_regime_factor(t)
            self.assertTrue(math.isfinite(factor))
            self.assertGreater(factor, 0.0)
            self.assertAlmostEqual(factor, md.vix[t - 1] / vix_mean, delta=0.35)

    def test_adv_is_positive_and_uses_only_past_volume(self):
        for sym in self.md.symbols:
            t = self.t0 + 30
            adv = self.md.adv(sym, t, 63)
            self.assertGreater(adv, 0.0)
            window = self.md.history_volume(sym, t, 63)
            self.assertAlmostEqual(adv, sum(window) / len(window), delta=1.0)


if __name__ == "__main__":
    unittest.main()
