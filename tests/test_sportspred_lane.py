"""Tests for the SportsPred dated-snapshot lane (the P1 mapping item).

The P1 backlog item said: map SportsPred's published predictions to a dated
snapshot URL, add them to the injury-archive-style dated snapshot collection,
and register them the way the injury signals are registered. These tests pin
each half of that:

* the snapshot loader that reads ``sportspred_predictions_<date>.json`` and
  ``sportspred_results_<date>.json`` captures (counts, staleness, malformed
  files counting as zeros rather than aborting);
* the signal builder's AVAILABLE / MISSING states and its forward-only array
  semantics (zero before the first capture dated inside the window);
* the collector's dated-write idempotency, oversize-file skip and manifest
  discipline (every request goes through the Fetcher, never raw urllib);
* the workflow wiring (the weekly archive run collects and commits the lane);
* the register row and the forward participant now name the real files.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from fixtures import REPO_ROOT

from sim import masterfeed, strategies_mf


def _load_collector():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "collect_real_data", os.path.join(REPO_ROOT, "scripts",
                                          "collect_real_data.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


COLLECTOR = _load_collector()


class _FakeMD:
    """Minimal window the SignalBook builders read (md.dates only)."""
    dates = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04",
             "2026-09-05"]


class TestSnapshotState(unittest.TestCase):
    def test_counts_predictions_and_results_per_capture_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            arch = os.path.join(tmp, "sportspred", "archive")
            os.makedirs(arch)
            json.dump({"schema_version": 1,
                       "last_run_utc": "2026-09-04T19:50:03Z",
                       "predictions": [{"event_id": "a"}, {"event_id": "b"}]},
                      open(os.path.join(arch, "sportspred_predictions_2026-09-04.json"),
                           "w"))
            json.dump({"schema_version": 1,
                       "results": [{"event_id": "a"}, {"event_id": "b"},
                                   {"event_id": "c"}]},
                      open(os.path.join(arch, "sportspred_results_2026-09-04.json"),
                           "w"))
            state = masterfeed._sportspred_snapshot_state(arch)
            self.assertEqual(list(state), ["2026-09-04"])
            self.assertEqual(state["2026-09-04"]["predictions"], 2)
            self.assertEqual(state["2026-09-04"]["results"], 3)
            self.assertEqual(state["2026-09-04"]["last_run_utc"],
                             "2026-09-04T19:50:03Z")

    def test_malformed_capture_counts_as_zero_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            arch = os.path.join(tmp, "sportspred", "archive")
            os.makedirs(arch)
            with open(os.path.join(arch, "sportspred_predictions_2026-09-04.json"),
                      "w") as fh:
                fh.write("{not json")
            state = masterfeed._sportspred_snapshot_state(arch)
            self.assertEqual(state["2026-09-04"]["predictions"], 0)

    def test_non_record_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            arch = os.path.join(tmp, "sportspred", "archive")
            os.makedirs(arch)
            json.dump({"events": [1, 2]},
                      open(os.path.join(arch, "sportspred_slate_2026-09-04.json"),
                           "w"))
            self.assertEqual(masterfeed._sportspred_snapshot_state(arch), {})


class TestSportspredSignals(unittest.TestCase):
    def _write_capture(self, arch: str, date: str, preds: int, res: int) -> None:
        os.makedirs(arch, exist_ok=True)
        json.dump({"last_run_utc": f"{date}T00:00:00Z",
                   "predictions": [{"event_id": str(i)} for i in range(preds)]},
                  open(os.path.join(arch, f"sportspred_predictions_{date}.json"), "w"))
        json.dump({"results": [{"event_id": str(i)} for i in range(res)]},
                  open(os.path.join(arch, f"sportspred_results_{date}.json"), "w"))

    def test_capture_inside_window_flips_signals_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_capture(os.path.join(tmp, "sportspred", "archive"),
                                "2026-09-03", preds=4, res=1)
            book = masterfeed.SignalBook(_FakeMD.dates)
            masterfeed._sportspred_signals(book, _FakeMD(), tmp)
            self.assertEqual(book.availability["sportspred_predictions"]["state"],
                             "AVAILABLE")
            self.assertEqual(book.availability["sportspred_graded"]["state"],
                             "AVAILABLE")
            # zero before the capture date, the recorded count from it onward:
            self.assertEqual(book.arrays["sportspred_predictions"],
                             [0.0, 0.0, 4.0, 4.0, 4.0])
            self.assertEqual(book.arrays["sportspred_graded"],
                             [0.0, 0.0, 1.0, 1.0, 1.0])

    def test_no_archive_reports_missing_with_collection_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = masterfeed.SignalBook(_FakeMD.dates)
            masterfeed._sportspred_signals(book, _FakeMD(), tmp)
            row = book.availability["sportspred_predictions"]
            self.assertEqual(row["state"], "MISSING")
            self.assertIn("no dated snapshot captured yet", row["note"])
            self.assertFalse(book.available("sportspred_predictions"))

    def test_capture_only_after_the_window_stays_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_capture(os.path.join(tmp, "sportspred", "archive"),
                                "2026-10-01", preds=7, res=0)
            book = masterfeed.SignalBook(_FakeMD.dates)
            masterfeed._sportspred_signals(book, _FakeMD(), tmp)
            row = book.availability["sportspred_predictions"]
            self.assertEqual(row["state"], "MISSING")
            self.assertIn("none dated on or before", row["note"])

    def test_build_signal_book_registers_the_lane_end_to_end(self):
        # the committed checkout holds no sportspred archive yet: the signal
        # must exist in the built book (as MISSING), not vanish silently.
        from sim import realdata
        try:
            md = realdata.build_real_market_data()
        except realdata.RealDataUnavailable:
            self.skipTest("committed real data not present")
        book = masterfeed.build_signal_book(md)
        for name in masterfeed.SPORTSPRED_SIGNALS:
            self.assertIn(name, book.availability)
            self.assertIn(book.availability[name]["state"],
                          ("MISSING", "AVAILABLE"))


class _RecordingFetcher:
    """Fetcher stand-in: records every request like the real one must."""

    def __init__(self, bodies):
        self.bodies = bodies          # name suffix -> bytes or None
        self.manifest = []
        self.requests = []

    def get(self, url, kind, headers=None, note="", **kw):
        self.requests.append({"url": url, "kind": kind, "note": note})
        tail = url.rsplit("/", 1)[-1]
        body = self.bodies.get(tail)
        ok = body is not None
        self.manifest.append({"url": url, "kind": kind, "ok": ok,
                              "bytes": len(body) if ok else 0})
        return body


class TestSportspredCollector(unittest.TestCase):
    def test_dated_caches_written_once_and_all_requests_manifested(self):
        with tempfile.TemporaryDirectory() as out:
            small = json.dumps({"predictions": []}).encode()
            fetcher = _RecordingFetcher({
                name: small for name in COLLECTOR.SPORTSPRED_SNAPSHOT_FILES})
            summary = COLLECTOR.collect_sportspred(fetcher, out)
            captured = [n for n, c in summary["captures"].items() if c.get("ok")]
            self.assertTrue(captured)
            base = os.path.join(out, "sportspred", "archive")
            written = sorted(os.listdir(base))
            self.assertEqual(len(written), len(captured))
            # every file is dated and every request was manifested
            for name in written:
                self.assertIn("_2026-", name)
            self.assertEqual(len(fetcher.requests),
                             len(COLLECTOR.SPORTSPRED_SNAPSHOT_FILES))
            self.assertEqual(len(fetcher.manifest),
                             len(COLLECTOR.SPORTSPRED_SNAPSHOT_FILES))
            # idempotent within the date: a second run fetches nothing
            summary2 = COLLECTOR.collect_sportspred(_RecordingFetcher({}), out)
            self.assertEqual(summary2["captures"], {})
            self.assertEqual(len(summary2["skipped"]),
                             len(COLLECTOR.SPORTSPRED_SNAPSHOT_FILES))
            # nothing was written for a site file that 404s
            self.assertIn("predictions.json", summary["captures"])

    def test_oversize_file_is_recorded_not_stored(self):
        with tempfile.TemporaryDirectory() as out:
            big = b"x" * (COLLECTOR.SPORTSPRED_BYTE_BUDGET + 1)
            fetcher = _RecordingFetcher({"predictions.json": big})
            summary = COLLECTOR.collect_sportspred(fetcher, out)
            self.assertIn("predictions.json", summary["skipped_oversized"])
            self.assertFalse(
                os.path.exists(os.path.join(out, "sportspred", "archive",
                                            f"sportspred_predictions_{summary['date']}.json")))

    def test_collector_uses_the_fetcher_and_declared_source_class(self):
        self.assertEqual(COLLECTOR.SOURCE_CLASS.get("sportspred"),
                         "OFFICIAL-VENDOR")
        self.assertTrue(COLLECTOR.SPORTSPRED_RAW_BASE.startswith(
            "https://raw.githubusercontent.com/"))


class TestWiring(unittest.TestCase):
    def test_archive_workflow_collects_and_commits_the_lane(self):
        text = open(os.path.join(REPO_ROOT, ".github", "workflows",
                                 "injury-archive.yml"), encoding="utf-8").read()
        self.assertIn("--only injury_archive,sportspred", text)
        self.assertIn("data/real/sportspred/archive", text)
        self.assertIn("SportsPred", text)

    def test_register_row_names_the_real_files(self):
        rows = {r["id"]: r for r in masterfeed.MASTER_SITE_SIGNALS}
        row = rows["SportsPred"]
        self.assertEqual(row["signals"],
                         ["sportspred_predictions", "sportspred_graded"])
        self.assertIn("data/predictions.json", row["mapping_note"])
        self.assertIn("data/results.json", row["mapping_note"])
        self.assertIn("data/slate.json", row["mapping_note"])
        self.assertIn("data/real/sportspred/archive", row["evidence"])

    def test_forward_participant_declares_the_signals(self):
        spec = strategies_mf.SportsPredForward.spec
        self.assertEqual(strategies_mf.SportsPredForward.signal_names,
                         ("sportspred_predictions", "sportspred_graded"))
        self.assertEqual(spec.username, "@SportsPred_Forward")
        self.assertIn("predictions.json", spec.thesis)


if __name__ == "__main__":
    unittest.main()
