"""Tests for the 2026-09-19 slate/insider/injury additions to the signal book.

Three behaviours are covered here, each with fixture files rather than the
committed collections so the tests run in any checkout:

* the ESPN slate clocks (NFL/NCAAF/NBA scoreboards) and their MISSING states;
* the SEC insider normalisation that merges the quarterly bulk data sets with
  the per-filing walk, de-duplicated by accession + transaction facts;
* the dated injury-snapshot archive and the rule that turns it AVAILABLE only
  when a capture is dated inside the window being built;
* the collector function that writes the dated archive (idempotent per date);
* the new Season 2 participants, including the two forward-only probes that
  must place no backdated trades.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest

from fixtures import REPO_ROOT

from sim import config, masterfeed, realdata, strategies_mf
from sim.portfolio import Account
from sim.strategies import Context


def _load_collector():
    spec = importlib.util.spec_from_file_location(
        "collect_real_data", os.path.join(REPO_ROOT, "scripts", "collect_real_data.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


COLLECTOR = _load_collector()


class _Instrument:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class FakeMD:
    """The two attributes the signal builders read: dates and instruments."""

    def __init__(self, dates, symbols=("AAA", "BBB")) -> None:
        self.dates = list(dates)
        self.instruments = {s: _Instrument(s) for s in symbols}


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


class TestInsiderNormalisation(unittest.TestCase):
    def test_bulk_rows_are_normalised_and_ceo_titles_detected(self):
        with tempfile.TemporaryDirectory(prefix="insider-") as root:
            _write_jsonl(
                os.path.join(root, "insider_bulk", "insider_transactions.jsonl"),
                [
                    {  # a plain open-market purchase by a director
                        "symbol": "AAA", "transaction_code": "P",
                        "transaction_date": "2026-01-05",
                        "accession_number": "0000000001-26-000001",
                        "shares": 1000.0, "price_per_share": 10.0,
                        "owners": [{"title": "Director", "relationship": "Director"}],
                    },
                    {  # a CEO purchase: the title match must fire
                        "symbol": "AAA", "transaction_code": "P",
                        "transaction_date": "2026-01-06",
                        "accession_number": "0000000001-26-000002",
                        "shares": 500.0, "price_per_share": 10.5,
                        "owners": [{"title": "Chief Executive Officer",
                                    "relationship": "Officer"}],
                    },
                    {  # an open-market sale
                        "symbol": "BBB", "transaction_code": "S",
                        "transaction_date": "2026-01-07",
                        "accession_number": "0000000001-26-000003",
                        "shares": 200.0, "price_per_share": 20.0,
                        "owners": [{"title": "CFO", "relationship": "Officer"}],
                    },
                ])
            rows, files, note = masterfeed._load_insider_rows(root)
            self.assertEqual(len(rows), 3)
            self.assertIn("data/real/insider_bulk/insider_transactions.jsonl", files)
            self.assertIn("data sets", note)
            titles = {r["ticker"]: r["title"] for r in rows if r["code"] == "P"}
            self.assertEqual(titles["AAA"].count("Chief Executive Officer"), 1)

            md = FakeMD(["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-09",
                         "2026-02-10"], symbols=("AAA", "BBB"))
            book = masterfeed.SignalBook(md.dates)
            masterfeed._insider_signals(book, md, root)
            self.assertEqual(book.availability["insider_buys_30d"]["state"],
                             "AVAILABLE")
            # a purchase dated on the session itself is not yet visible (the
            # window is exclusive of the session date: no same-day lookahead)
            self.assertEqual(book.value("insider_buys_30d::AAA", 1), 0.0)
            # 2026-01-06: the 01-05 purchase is inside the trailing 30d window
            self.assertEqual(book.value("insider_buys_30d::AAA", 2), 1.0)
            # 2026-01-09: both AAA purchases are inside 30 days
            self.assertEqual(book.value("insider_buys_30d::AAA", 3), 2.0)
            # the CEO purchase is counted on the CEO array
            self.assertEqual(book.value("insider_ceo_buys_30d::AAA", 3), 1.0)
            # the trailing-30d buy/sell ratio at 2026-01-09 is 2 buys / 1 sale
            self.assertAlmostEqual(book.value("insider_buy_ratio_30d", 3), 2.0)
            # after 30 days the window is empty again
            self.assertEqual(book.value("insider_buys_30d::AAA", 4), 0.0)
            self.assertEqual(book.value("insider_buy_ratio_30d", 4), 0.0)

    def test_bulk_and_walk_merge_deduplicates_on_accession_and_facts(self):
        with tempfile.TemporaryDirectory(prefix="insider-") as root:
            bulk_row = {
                "symbol": "AAA", "transaction_code": "P",
                "transaction_date": "2026-01-05",
                "accession_number": "ACC-1",
                "shares": 1000.0, "price_per_share": 10.0,
                "owners": [{"title": "Director", "relationship": "Director"}],
            }
            _write_jsonl(
                os.path.join(root, "insider_bulk", "insider_transactions.jsonl"),
                [bulk_row])
            _write_jsonl(
                os.path.join(root, "sec", "form4_transactions.jsonl"),
                [
                    {  # the same filing re-read by the walk: must not double count
                        "ticker": "AAA", "code": "P",
                        "transaction_date": "2026-01-05",
                        "accession": "ACC-1", "shares": 1000.0, "price": 10.0,
                        "title": "Director", "roles": ["director"],
                    },
                    {  # a genuinely newer filing the bulk quarter does not cover
                        "ticker": "AAA", "code": "P",
                        "transaction_date": "2026-08-01",
                        "accession": "ACC-2", "shares": 100.0, "price": 12.0,
                        "title": "Chief Financial Officer", "roles": ["officer"],
                    },
                ])
            rows, files, _note = masterfeed._load_insider_rows(root)
            self.assertEqual(len(rows), 2, "the re-read filing must deduplicate")
            self.assertEqual(len(files), 2, "both collections are recorded")
            dates = sorted(r["transaction_date"] for r in rows)
            self.assertEqual(dates, ["2026-01-05", "2026-08-01"])

    def test_missing_collections_register_missing(self):
        with tempfile.TemporaryDirectory(prefix="insider-") as root:
            rows, files, _note = masterfeed._load_insider_rows(root)
            self.assertEqual((rows, files), ([], []))
            md = FakeMD(["2026-01-02"], symbols=("AAA",))
            book = masterfeed.SignalBook(md.dates)
            masterfeed._insider_signals(book, md, root)
            self.assertEqual(
                book.availability["insider_buys_30d"]["state"], "MISSING")
            present = masterfeed.insider_collection_present(root)
            self.assertFalse(present["present"])


class TestSlateSignals(unittest.TestCase):
    @staticmethod
    def _game(date, home_score, away_score, league="nfl"):
        return {"league": league, "date": date, "status": "STATUS_FINAL",
                "home": "Home FC", "home_score": home_score,
                "away": "Away FC", "away_score": away_score,
                "event_id": date + str(home_score), "name": "A at H"}

    def test_counts_close_games_and_away_wins_in_a_7d_window(self):
        with tempfile.TemporaryDirectory(prefix="slate-") as root:
            _write_jsonl(
                os.path.join(root, "sports", "nfl_scoreboard.jsonl"),
                [self._game("2026-01-04", 20, 21),   # close, away win
                 self._game("2026-01-04", 30, 3),    # blowout, home win
                 self._game("2026-01-11", 10, 7),    # close, home win
                 {"league": "nfl", "date": "2026-01-11", "status": "STATUS_SCHEDULED",
                  "home": "Home FC", "home_score": None, "away": "Away FC",
                  "away_score": None, "event_id": "x", "name": "future"},
                 ])
            _write_jsonl(
                os.path.join(root, "sports", "ncaaf_scoreboard.jsonl"),
                [self._game("2026-01-04", 7, 21, league="ncaaf")])
            dates = ["2026-01-05", "2026-01-12", "2026-01-20"]
            md = FakeMD(dates, symbols=())
            book = masterfeed.SignalBook(md.dates)
            masterfeed._slate_signals(book, md, root)
            # 2026-01-05 sees the two 01-04 finals: 2 games, 1 close, 1 away win
            self.assertEqual(book.value("nfl_games_7d", 0), 2.0)
            self.assertEqual(book.value("nfl_close_games_7d", 0), 1.0)
            self.assertEqual(book.value("nfl_away_wins_7d", 0), 1.0)
            # 2026-01-12 sees only the 01-11 final (01-04 fell out of the window)
            self.assertEqual(book.value("nfl_games_7d", 1), 1.0)
            self.assertEqual(book.value("nfl_close_games_7d", 1), 1.0)
            self.assertEqual(book.value("nfl_away_wins_7d", 1), 0.0)
            # 2026-01-20 is past both slates
            self.assertEqual(book.value("nfl_games_7d", 2), 0.0)
            # the ncaaf file holds one away blowout
            self.assertEqual(book.value("ncaaf_away_wins_7d", 0), 1.0)
            # the nba collection is absent: MISSING, never borrowed from nfl
            self.assertEqual(book.availability["nba_games_7d"]["state"], "MISSING")
            self.assertEqual(book.value("nba_games_7d", 0), 0.0)
            self.assertEqual(book.provenance["nfl"]["source_class"], "SECONDARY")

    def test_empty_file_registers_missing(self):
        with tempfile.TemporaryDirectory(prefix="slate-") as root:
            _write_jsonl(os.path.join(root, "sports", "nfl_scoreboard.jsonl"), [])
            md = FakeMD(["2026-01-05"], symbols=())
            book = masterfeed.SignalBook(md.dates)
            masterfeed._slate_signals(book, md, root)
            for name in ("nfl_games_7d", "nfl_close_games_7d", "nfl_away_wins_7d"):
                self.assertEqual(book.availability[name]["state"], "MISSING", name)
                self.assertEqual(book.value(name, 0), 0.0)


class TestInjuryArchiveSignals(unittest.TestCase):
    @staticmethod
    def _snapshot(root, date, out_count, league="nfl"):
        path = os.path.join(root, "sports", "official", "archive",
                            f"espn_{league}_injuries_{date}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        items = []
        for i in range(out_count):
            items.append({"injuries": [{"athlete": {"id": i}, "status": "Out"}]})
        items.append({"injuries": [{"athlete": {"id": "q"}, "status": "Questionable"}]})
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"items": items}, fh)

    def test_snapshot_inside_the_window_makes_the_signal_real(self):
        with tempfile.TemporaryDirectory(prefix="injury-") as root:
            self._snapshot(root, "2026-01-05", 3)
            dates = ["2026-01-02", "2026-01-06", "2026-01-13"]
            md = FakeMD(dates, symbols=())
            book = masterfeed.SignalBook(md.dates)
            masterfeed._injury_signals(book, md, root)
            # before the capture: zero
            self.assertEqual(book.value("nfl_injury_report", 0), 0.0)
            # the session after the capture sees the 3 Out designations
            self.assertEqual(book.value("nfl_injury_report", 1), 3.0)
            # ...but not forever: 7-day staleness drops it back to zero
            self.assertEqual(book.value("nfl_injury_report", 2), 0.0)
            self.assertEqual(book.availability["nfl_injury_report"]["state"],
                             "AVAILABLE")
            # the NBA archive holds nothing: stays MISSING
            self.assertEqual(book.availability["nba_injury_report"]["state"],
                             "MISSING")

    def test_snapshots_after_the_window_stay_missing_with_an_archive_note(self):
        with tempfile.TemporaryDirectory(prefix="injury-") as root:
            self._snapshot(root, "2026-09-19", 5)
            md = FakeMD(["2026-09-14", "2026-09-15", "2026-09-16"], symbols=())
            book = masterfeed.SignalBook(md.dates)
            masterfeed._injury_signals(book, md, root)
            meta = book.availability["nfl_injury_report"]
            self.assertEqual(meta["state"], "MISSING")
            self.assertIn("1 capture", meta["note"])
            for t in range(3):
                self.assertEqual(book.value("nfl_injury_report", t), 0.0)


class TestInjuryArchiveCollector(unittest.TestCase):
    def test_dated_snapshots_written_once_per_date(self):
        with tempfile.TemporaryDirectory(prefix="collector-") as root:
            class Fetcher:
                def __init__(self):
                    self.manifest = []
                    self.budget_exhausted = False
                    self.calls = []

                def get(self, url, kind, headers=None, note="", **kwargs):
                    self.calls.append(url)
                    if "/injuries" in url and "site.api.espn.com" in url:
                        body = {"items": [
                            {"injuries": [{"status": "Out"}] * 2},
                            {"injuries": [{"status": "Doubtful"}]},
                            {"injuries": [{"status": "Questionable"}]},
                        ]}
                    else:
                        body = "<html>official league document</html>"
                    raw = json.dumps(body).encode("utf-8") \
                        if isinstance(body, dict) else body.encode("utf-8")
                    import hashlib
                    self.manifest.append(
                        {"url": url, "kind": kind, "status": 200, "ok": True,
                         "bytes": len(raw),
                         "sha256": hashlib.sha256(raw).hexdigest(),
                         "fetched_at": "2026-09-19T00:00:00Z", "note": note})
                    return raw

            fetcher = Fetcher()
            summary = COLLECTOR.collect_injury_archive(fetcher, root)
            base = os.path.join(root, "sports", "official", "archive")
            written = sorted(os.listdir(base))
            # two ESPN json snapshots + two official documents
            self.assertEqual(len(written), 4, written)
            self.assertEqual(summary["captures"]["espn_nfl_injuries"]["game_impacting"], 3)
            self.assertEqual(summary["captures"]["espn_nba_injuries"]["game_impacting"], 3)
            # a second run on the same date is idempotent
            fetcher2 = Fetcher()
            summary2 = COLLECTOR.collect_injury_archive(fetcher2, root)
            self.assertEqual(len(summary2["skipped"]), 4)
            self.assertEqual(sorted(os.listdir(base)), written)


HAVE_PRICES = os.path.isdir(os.path.join(realdata.REAL_ROOT, "prices", "yahoo"))


@unittest.skipUnless(HAVE_PRICES, "data/real/prices is not collected in this checkout")
class TestNewParticipantsOnCollectedData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = realdata.build_real_market_data()
        cls.md.signals = masterfeed.build_signal_book(cls.md, realdata.REAL_ROOT)

    def _ctx(self, strategy, t):
        account = Account(participant=strategy.spec.username,
                          starting_cash=config.STARTING_CASH,
                          margin=config.MarginConfig())
        return Context(self.md, t, account, config.CompetitionConfig(),
                       quotes={}, seed=1)

    def test_roster_holds_the_new_participants(self):
        roster = {s.username for s in strategies_mf.build_roster_mf()}
        for name in ("@NFL_Slate_Attention", "@NBA_Slate_Attention",
                     "@NCAA_Upset_Blitz", "@NBAInjury_Forward",
                     "@SportsPred_Forward"):
            self.assertIn(name, roster)

    def test_forward_only_probes_place_no_backdated_trades(self):
        roster = strategies_mf.build_roster_mf()
        probes = [s for s in roster if s.spec.username in
                  ("@NBAInjury_Forward", "@SportsPred_Forward")]
        self.assertEqual(len(probes), 2)
        for probe in probes:
            for t in (self.md.first_competition_index,
                      self.md.first_competition_index + 120,
                      len(self.md.dates) - 1):
                ctx = self._ctx(probe, t)
                self.assertEqual(probe.on_day(ctx), [],
                                 f"{probe.username} placed a backdated order")

    def test_slate_participants_only_order_from_their_baskets(self):
        roster = strategies_mf.build_roster_mf()
        persona = {s.username: s for s in roster}
        baskets = {
            "@NFL_Slate_Attention": {"DKNG", "FLUT", "SRAD"},
            "@NBA_Slate_Attention": {"DKNG", "FLUT", "GENI"},
            "@NCAA_Upset_Blitz": {"DKNG", "GENI", "SRAD"},
        }
        t0, t1 = self.md.first_competition_index, len(self.md.dates) - 1
        for username, basket in baskets.items():
            strategy = persona[username]
            available = self.md.signals.available(
                {"@NFL_Slate_Attention": "nfl_games_7d",
                 "@NBA_Slate_Attention": "nba_games_7d",
                 "@NCAA_Upset_Blitz": "ncaaf_games_7d"}[username])
            ordered = set()
            for t in range(t0, t1 + 1, 5):
                for order in strategy.on_day(self._ctx(strategy, t)):
                    ordered.add(order.symbol)
            self.assertTrue(ordered <= basket,
                            f"{username} ordered outside its basket: "
                            f"{ordered - basket}")
            if available:
                self.assertTrue(ordered, f"{username} never ordered at all")
            else:
                # A persona whose collection is missing must stay silent rather
                # than borrow another league's calendar.
                self.assertEqual(ordered, set(),
                                 f"{username} ordered with no collected signal")


if __name__ == "__main__":
    unittest.main()


class TestAgentInsiderLane(unittest.TestCase):
    """The rendered-extraction lane merges with the other two and deduplicates."""

    def test_agent_rows_merge_and_dedupe(self):
        with tempfile.TemporaryDirectory(prefix="insider-agent-") as root:
            walk_row = {
                "ticker": "AAA", "code": "P", "transaction_date": "2026-08-01",
                "accession": "ACC-9", "shares": 100.0, "price": 12.0,
                "title": "Director", "roles": ["director"],
            }
            _write_jsonl(os.path.join(root, "sec", "form4_transactions.jsonl"),
                         [walk_row])
            _write_jsonl(
                os.path.join(root, "sec_agent", "form4_transactions.jsonl"),
                [
                    {  # same filing via the rendered lane: must deduplicate
                        "ticker": "AAA", "code": "P",
                        "transaction_date": "2026-08-01", "accession": "ACC-9",
                        "shares": 100.0, "price": 12.0, "title": "Director",
                        "roles": ["officer"], "channel": "agent-rendered-extract",
                    },
                    {  # a newer filing only the agent lane has
                        "ticker": "AAA", "code": "P",
                        "transaction_date": "2026-09-15", "accession": "ACC-10",
                        "shares": 75.0, "price": 13.0,
                        "title": "Chief Executive Officer", "roles": ["officer"],
                        "channel": "agent-rendered-extract",
                    },
                ])
            rows, files, note = masterfeed._load_insider_rows(root)
            self.assertEqual(len(rows), 2, "the shared filing must deduplicate")
            self.assertIn("data/real/sec_agent/form4_transactions.jsonl", files)
            self.assertIn("rendered-extraction lane", note)
            dates = sorted(r["transaction_date"] for r in rows)
            self.assertEqual(dates, ["2026-08-01", "2026-09-15"])
            present = masterfeed.insider_collection_present(root)
            self.assertTrue(present["present"])
            self.assertEqual(present["rows"], 2)
            self.assertEqual(present["coverage"], ["2026-08-01", "2026-09-15"])

    def test_agent_lane_alone_registers_available_and_names_the_lane(self):
        with tempfile.TemporaryDirectory(prefix="insider-agent-") as root:
            _write_jsonl(
                os.path.join(root, "sec_agent", "form4_transactions.jsonl"),
                [{"ticker": "AAA", "code": "P", "transaction_date": "2026-09-15",
                  "accession": "ACC-10", "shares": 75.0, "price": 13.0,
                  "title": "Chief Executive Officer", "roles": ["officer"],
                  "channel": "agent-rendered-extract"}])
            md = FakeMD(["2026-09-15", "2026-09-16", "2026-09-17"],
                        symbols=("AAA",))
            book = masterfeed.SignalBook(md.dates)
            masterfeed._insider_signals(book, md, root)
            self.assertEqual(book.availability["insider_buys_30d"]["state"],
                             "AVAILABLE")
            # a purchase dated on the session itself is not yet visible
            self.assertEqual(book.value("insider_buys_30d::AAA", 0), 0.0)
            # visible from the next session onward
            self.assertEqual(book.value("insider_buys_30d::AAA", 1), 1.0)
            self.assertEqual(book.value("insider_ceo_buys_30d::AAA", 1), 1.0)
            self.assertIn("agent-rendered-extract",
                          book.availability["insider_buys_30d"]["note"])


class TestKalshiSignalRowCounting(unittest.TestCase):
    """Regression: the builder must read every row of every collected file.

    The first version of this loop dedented its accounting block out of the
    ``for line in fh`` loop, so each file contributed only its last row and an
    empty file crashed on an unbound name. Both behaviours are pinned here.
    """

    def _book(self, root):
        md = FakeMD(["2026-09-01", "2026-09-18", "2026-09-19"], symbols=("AAA",))
        book = masterfeed.SignalBook(md.dates)
        masterfeed._kalshi_signals(book, md, root)
        return book

    def test_every_row_of_every_file_is_counted(self):
        with tempfile.TemporaryDirectory(prefix="kalshi-") as root:
            _write_jsonl(os.path.join(root, "kalshi", "KXONE_settled.jsonl"), [
                {"close_time": "2026-09-15T05:00:00Z", "volume": 10.0},
                {"close_time": "2026-09-16T05:00:00Z", "volume": 20.0},
                {"close_time": "2026-09-17T05:00:00Z", "open_interest": 30.0},
            ])
            _write_jsonl(os.path.join(root, "kalshi", "KXTWO_settled.jsonl"), [
                {"close_time": "2026-09-17T05:00:00Z", "volume": 5.0},
            ])
            book = self._book(root)
            self.assertEqual(book.availability["kalshi_settled_30d"]["state"],
                             "AVAILABLE")
            self.assertEqual(book.availability["kalshi_settled_30d"]["rows"], 4)
            # trailing-30d settled count at 2026-09-18 sees all four closes
            self.assertEqual(book.value("kalshi_settled_30d", 1), 4.0)
            # volume aggregates per close date, including the open_interest row
            self.assertEqual(book.value("kalshi_volume_30d", 1), 65.0)

    def test_empty_file_does_not_crash_and_counts_nothing(self):
        with tempfile.TemporaryDirectory(prefix="kalshi-") as root:
            _write_jsonl(os.path.join(root, "kalshi", "KXEMPTY_settled.jsonl"), [])
            book = self._book(root)
            self.assertEqual(book.availability["kalshi_settled_30d"]["state"],
                             "MISSING")
