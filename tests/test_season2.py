"""Season 2 tests: real collected prices, real signals, the verified ledger.

These tests are deliberately written against the *committed* collected files
under ``data/real/``. If the data is not present (a checkout without the
collected artefacts), the data-dependent tests skip loudly rather than passing
quietly: a Season 2 that cannot read its prices must never report success.
"""

from __future__ import annotations

import json
import os
import unittest

from fixtures import REPO_ROOT

from sim import config, ledger, masterfeed, realdata, season2, strategies_mf
from sim.portfolio import Account
from sim.strategies import Context

MEMORY_ROOT = os.path.join(REPO_ROOT, "memory")
SEASON2_RUN = "season2-primary-seed20260918"
HAVE_PRICES = os.path.isdir(os.path.join(realdata.REAL_ROOT, "prices", "yahoo"))
HAVE_RUN = os.path.isdir(os.path.join(MEMORY_ROOT, "runs", SEASON2_RUN))


@unittest.skipUnless(HAVE_PRICES, "data/real/prices is not collected in this checkout")
class TestRealMarketData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = realdata.build_real_market_data()

    def test_window_and_calendar(self):
        self.assertEqual(self.md.dates[0], realdata.SEASON2_WARMUP_START)
        self.assertEqual(self.md.dates[-1], realdata.SEASON2_END)
        self.assertGreater(self.md.warmup_days, 200,
                           "a one-year warm-up is the point of collecting two years")
        self.assertEqual(self.md.dates[self.md.first_competition_index],
                         realdata.SEASON2_START)
        self.assertEqual(len(self.md.dates), len(self.md.spx))
        self.assertEqual(len(self.md.dates), len(self.md.vix))

    def test_every_bar_is_the_collected_bar(self):
        for symbol, series in self.md.bars.items():
            source = realdata.load_series(symbol)
            by_date = source.by_date()
            for bar in series:
                ref = by_date.get(bar.date)
                if ref is None:
                    self.assertIn(bar.date, self.md.gaps[symbol],
                                  f"{symbol} {bar.date} is not in the file and is not "
                                  f"declared as forward-filled")
                    continue
                self.assertEqual((bar.open, bar.high, bar.low, bar.close),
                                 (ref.open, ref.high, ref.low, ref.close),
                                 f"{symbol} {bar.date} differs from the collected file")
                self.assertEqual(bar.volume, ref.volume)

    def test_instrument_parameters_are_estimated_not_declared(self):
        market = self.md.instruments["SPY"]
        self.assertGreater(market.beta, 0.7)
        self.assertLess(market.beta, 1.4)
        # XBI's beta was once computed against the wrong slice of the index
        # series and came out negative; a biotech ETF on a broad market factor
        # cannot have a negative beta without something being broken.
        self.assertGreater(self.md.instruments["XBI"].beta, 0.2)
        for symbol, inst in self.md.instruments.items():
            self.assertIn(inst.provenance.get("price_start"), ("real",), symbol)
            self.assertGreater(inst.adv_shares, 100_000, symbol)
            self.assertLess(inst.sigma_idio_annual, 3.0, symbol)

    def test_crosscheck_against_fred_is_published_and_passes(self):
        result = realdata.crosscheck_against_fred("SPY")
        self.assertTrue(result["ok"], result)
        self.assertGreater(result["return_correlation"], 0.99, result)
        self.assertGreater(result["sessions"], 400, result)

    def test_inventory_covers_every_price_file(self):
        inventory = realdata.data_inventory(digest=False)
        paths = {row["path"] for row in inventory["files"]}
        for symbol in self.md.symbols:
            slug = symbol.replace("^", "_")
            self.assertIn(f"data/real/prices/yahoo/{slug}.json", paths)

    def test_source_register_lists_every_endpoint_the_code_calls(self):
        sources = realdata.collected_sources()
        ids = [row["id"] for row in sources]
        self.assertEqual(len(ids), len(set(ids)))
        for row in sources:
            self.assertTrue(row["url"].startswith("https://"), row)
            self.assertIn(row["source_class"],
                          ("OFFICIAL", "OFFICIAL-VENDOR", "SECONDARY", "ASSERTED"), row)


class TestLedger(unittest.TestCase):
    """The ledger must re-derive an account from its own fill tape."""

    def _fill(self, date, symbol, side, qty, price, commission=0.0, fee=0.0):
        return {"participant": "@T", "date": date, "symbol": symbol, "side": side,
                "filled_qty": qty, "requested_qty": qty, "avg_price": price,
                "commission": commission, "exchange_fee": fee, "regulatory_fee": 0.0,
                "rebate": 0.0, "interval": 0, "reason": "test"}

    def test_round_trip_pnl_and_fees(self):
        fills = [self._fill("2026-01-02", "XBI", "buy", 100, 100.0, fee=0.10),
                 self._fill("2026-01-05", "XBI", "sell", 100, 110.0, fee=0.10)]
        trips = ledger.build_round_trips(fills)
        closed = [t for t in trips if t["status"] == "closed"]
        self.assertEqual(len(closed), 1)
        self.assertAlmostEqual(closed[0]["gross_pnl_usd"], 1000.0, places=4)
        self.assertAlmostEqual(closed[0]["fees_usd"], 0.20, places=4)
        self.assertAlmostEqual(closed[0]["net_pnl_usd"], 999.80, places=4)

    def test_zero_crossing_opens_the_opposite_lot(self):
        fills = [self._fill("2026-01-02", "XBI", "buy", 100, 100.0),
                 self._fill("2026-01-05", "XBI", "sell", 150, 110.0),
                 self._fill("2026-01-06", "XBI", "buy", 50, 90.0)]
        trips = ledger.build_round_trips(fills)
        self.assertEqual([t["status"] for t in trips], ["closed", "closed"])
        self.assertAlmostEqual(trips[0]["gross_pnl_usd"], 1000.0, places=4)
        self.assertEqual(trips[1]["direction"], "short")
        self.assertAlmostEqual(trips[1]["gross_pnl_usd"], 1000.0, places=4)

    def test_verify_reproduces_equity_when_carry_is_given(self):
        fills = [self._fill("2026-01-02", "XBI", "buy", 100, 100.0, fee=0.10),
                 self._fill("2026-01-05", "XBI", "sell", 100, 110.0, fee=0.10)]
        check = ledger.verify_ledger(fills, engine_final_equity=100_999.80,
                                     engine_realized_pnl=999.80,
                                     carry_net_usd=0.0, starting_cash=100_000.0)
        self.assertEqual(check["open_positions"], {})
        self.assertLessEqual(abs(check["equity_residual_usd"]), check["rounding_bound_usd"])
        self.assertTrue(check["within_rounding_bound"])
        # Realised P&L is compared net of explicit fees on both sides, because
        # that is what the engine reports; a gross-to-net comparison is what
        # produced a spurious 118.75 USD "residual" in the first Season 2 run.
        self.assertLessEqual(abs(check["realized_residual_usd"]), 0.01)

    def test_verify_flags_a_wrong_equity(self):
        fills = [self._fill("2026-01-02", "XBI", "buy", 100, 100.0)]
        check = ledger.verify_ledger(fills, engine_final_equity=1.0,
                                     engine_realized_pnl=0.0, carry_net_usd=0.0,
                                     starting_cash=100_000.0)
        self.assertFalse(check["within_rounding_bound"])
        self.assertGreater(abs(check["equity_residual_usd"]), 100.0)

    def test_ledger_round_trips_through_disk(self):
        import tempfile
        fills = [self._fill("2026-01-02", "XBI", "buy", 100, 100.0),
                 self._fill("2026-01-05", "XBI", "sell", 100, 110.0)]
        doc = ledger.build_ledger(fills, None)
        with tempfile.TemporaryDirectory() as tmp:
            written = ledger.write_ledger(tmp, doc)
            self.assertIn(ledger.LEDGER_SUMMARY, written)
            again = ledger.read_ledger(tmp)
            self.assertEqual(len(again["fills"]), 2)
            self.assertEqual(len(again["round_trips"]), len(doc["round_trips"]))


@unittest.skipUnless(HAVE_PRICES, "data/real is not collected in this checkout")
class TestSignalBook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = realdata.build_real_market_data()
        cls.book = masterfeed.build_signal_book(cls.md, realdata.REAL_ROOT)

    def test_availability_is_consistent_with_the_arrays(self):
        for name, meta in self.book.availability.items():
            array = self.book.arrays.get(name)
            self.assertIsNotNone(array, f"{name} is registered but has no array")
            self.assertEqual(len(array), len(self.md.dates), name)
            if meta["state"] == "AVAILABLE":
                self.assertTrue(any(v != 0.0 for v in array),
                                f"{name} is AVAILABLE but every value is zero - a "
                                f"strategy would read it as a real observation")

    def test_every_registered_file_exists(self):
        for name, meta in self.book.availability.items():
            for rel in meta.get("files", []):
                path = os.path.join(REPO_ROOT, rel)
                self.assertTrue(os.path.exists(path) or os.path.isdir(path),
                                f"{name}: {rel} does not exist")

    def test_registered_urls_are_official_and_hyperlinked(self):
        for name, meta in self.book.availability.items():
            if meta["state"] != "AVAILABLE":
                continue
            self.assertTrue(meta["url"].startswith("https://"),
                            f"{name}: no official source recorded")

    def test_no_look_ahead_in_the_fda_count(self):
        """The 30-day count on a session can only use decisions before it."""
        array = self.book.arrays.get("fda_all_30d")
        self.assertIsNotNone(array)
        decisions = []
        with open(os.path.join(realdata.REAL_ROOT, "fda", "openfda_decisions.jsonl"),
                  "r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("status") != "AP":
                    continue
                raw = str(row.get("status_date") or "")
                if len(raw) == 8:
                    decisions.append(f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}")
        decisions.sort()
        for t in (self.md.first_competition_index,
                  self.md.first_competition_index + 40, len(self.md.dates) - 1):
            import datetime as dt
            session = self.md.dates[t]
            lo = (dt.date.fromisoformat(session) - dt.timedelta(days=30)).isoformat()
            expected = sum(1 for d in decisions if lo <= d < session)
            self.assertEqual(array[t], float(expected),
                             f"session {session}: {array[t]} vs recomputed {expected}")

    def test_mlb_signal_is_zero_outside_the_season(self):
        array = self.book.arrays["mlb_games_7d"]
        for t, date in enumerate(self.md.dates):
            if date < "2026-03-25":
                self.assertEqual(array[t], 0.0,
                                 "an MLB count before opening day would be a fabrication")


class TestSeason2Roster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster = strategies_mf.build_roster_mf()

    def test_unique_usernames_and_ids(self):
        usernames = [s.username for s in self.roster]
        self.assertEqual(len(usernames), len(set(usernames)))
        for username in usernames:
            self.assertTrue(username.startswith("@"), username)
            self.assertGreater(len(username), 6, username)
            self.assertNotIn(" ", username)

    def test_every_strategy_declares_why_it_is_return_seeking(self):
        for strategy in self.roster:
            spec = strategy.spec
            self.assertTrue(spec.thesis, spec.username)
            self.assertTrue(spec.entry_rules, spec.username)
            self.assertTrue(spec.exit_rules, spec.username)
            self.assertGreaterEqual(len(spec.known_failure_modes), 2, spec.username)
            self.assertTrue(spec.why_return_seeking, spec.username)
            self.assertGreaterEqual(spec.aggression, 1, spec.username)

    def test_masterfeed_register_is_complete_for_the_brief(self):
        register = masterfeed.signal_register()
        requested = {row["requested_as"] for row in register}
        for item in ("CEO", "SFWeather", "Insider-trades", "TradingViewTheLeap",
                     "NFLInjuryReport", "NBAInjuryReport", "DrugAnalysis",
                     "Ncaa-football-alerts", "NFL-scoreboard", "MLB-Live-PBP",
                     "SportsPred", "GOLD", "Tradingview-pinescript-editor",
                     "KalshiPaperSim"):
            self.assertIn(item, requested, f"{item} is missing from the register")
        for row in register:
            self.assertIn(row["source_class"], ("OFFICIAL", "OFFICIAL-VENDOR",
                                                "SECONDARY", "ASSERTED"),
                          row)
            self.assertIn(row["status"], ("FETCHED", "PARTIAL", "FORWARD-ONLY",
                                          "NOT-RETRIEVABLE"), row)
            self.assertIn(row["mapping"], ("STRONG-MAPPING", "WEAK-MAPPING",
                                           "UNPROVEN-MAPPING"), row)
            self.assertTrue(row["official_url"].startswith("https://"), row)

    def test_forward_only_strategies_place_no_backdated_trades(self):
        md = None
        forward = [s for s in self.roster
                   if s.spec.username == "@InjuryFeed_Forward"]
        self.assertEqual(len(forward), 1)
        if not HAVE_PRICES:
            self.skipTest("no collected data")
        md = realdata.build_real_market_data()
        md.signals = masterfeed.build_signal_book(md, realdata.REAL_ROOT)
        account = Account(participant="@InjuryFeed_Forward",
                          starting_cash=config.STARTING_CASH, margin=config.MarginConfig())
        for t in (md.first_competition_index, md.first_competition_index + 120,
                  len(md.dates) - 1):
            ctx = Context(md, t, account, config.CompetitionConfig(), quotes={}, seed=1)
            self.assertEqual(forward[0].on_day(ctx), [],
                             "a forward-only strategy placed a backdated order")


@unittest.skipUnless(HAVE_RUN, "the Season 2 run is not in this memory store")
class TestSeason2Artefacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = os.path.join(MEMORY_ROOT, "runs", SEASON2_RUN)

    def _load(self, name):
        with open(os.path.join(self.dir, name), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_verification_residuals_are_within_their_bound(self):
        doc = self._load("verification.json")
        self.assertTrue(doc["all_residuals_within_rounding_bound"], doc)
        for row in doc["per_participant"]:
            self.assertLessEqual(abs(row["equity_residual_usd"]),
                                 row["rounding_bound_usd"] + 1e-6,
                                 f"{row['username']}: residual {row['equity_residual_usd']} "
                                 f"exceeds the bound {row['rounding_bound_usd']}")
            self.assertEqual(row["open_positions"], {},
                             f"{row['username']} still holds inventory after the "
                             f"forced liquidation")

    def test_manifest_records_the_season_and_the_ledger(self):
        manifest = self._load("manifest.json")
        self.assertEqual(manifest["season"], "season2")
        self.assertEqual(manifest["competition"]["start"], realdata.SEASON2_START)
        self.assertEqual(manifest["competition"]["rank_metric"], "total_return_pct")
        self.assertIn("ledger", manifest)
        self.assertGreater(manifest["ledger"]["fills"], 0)
        self.assertIn("data_sources", manifest)

    def test_signal_status_matches_the_signal_book(self):
        book = self._load("signal_book.json")
        status = self._load("signal_status.json")
        self.assertEqual(len(status), len(strategies_mf.build_roster_mf()))
        for row in status:
            for signal in row["signals"]:
                state = book["availability"].get(signal["signal"], {}).get("state")
                self.assertEqual(signal["state"], state,
                                 f"{row['username']}: {signal['signal']}")


if __name__ == "__main__":
    unittest.main()
