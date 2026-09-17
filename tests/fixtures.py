"""Shared fixtures for the test suite.

Everything here is deliberately cheap: a full Season 1 replay builds in well
under a second, so tests use the real window and the real FRED files rather than
synthetic stand-ins.  Where a test needs a shorter season (the engine smoke
test) it asks for one explicitly.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import sys
from typing import Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sim import config, marketdata, universe                      # noqa: E402

_CACHE: Dict[str, object] = {}


def cfg(**overrides) -> config.CompetitionConfig:
    """A competition config, optionally with fields replaced."""
    return dataclasses.replace(config.CompetitionConfig(), **overrides)


def replay(seed: Optional[int] = None, **cfg_overrides) -> marketdata.MarketData:
    """Cached replay for a given (seed, config) pair."""
    c = cfg(**cfg_overrides)
    key = f"{c.fingerprint()}:{c.seed if seed is None else seed}"
    if key not in _CACHE:
        _CACHE[key] = marketdata.build_replay(c, seed=seed)
    return _CACHE[key]  # type: ignore[return-value]


def short_replay(seed: int = 7) -> marketdata.MarketData:
    """A ~3-month season, for engine tests that would otherwise be slow."""
    return marketdata.build_replay(cfg(end="2025-12-31", seed=seed), seed=seed)


def instruments() -> List[universe.Instrument]:
    if "universe" not in _CACHE:
        _CACHE["universe"] = universe.build_universe()
    return _CACHE["universe"]  # type: ignore[return-value]


def instrument(symbol: str) -> universe.Instrument:
    for inst in instruments():
        if inst.symbol == symbol:
            return inst
    raise KeyError(symbol)


def closes_digest(md: marketdata.MarketData) -> str:
    """A single hash over every simulated bar, for determinism assertions."""
    h = hashlib.sha256()
    for sym in sorted(md.symbols):
        for bar in md.bars[sym]:
            h.update(f"{sym}|{bar.date}|{bar.open:.6f}|{bar.high:.6f}|"
                     f"{bar.low:.6f}|{bar.close:.6f}|{bar.volume}\n".encode())
    return h.hexdigest()


def fred_csv_rows(series: str) -> Dict[str, Optional[float]]:
    """Read a real FRED CSV straight off disk, bypassing sim.calendar.

    Tests that check the simulation against 'the real data' must not go through
    the same parser the simulation uses, or a parsing bug would cancel out.
    """
    import csv
    from sim.calendar import TradingCalendar
    path = TradingCalendar._find(os.path.join(REPO_ROOT, "data", "real", "fred"),
                                 series)
    out: Dict[str, Optional[float]] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or row[0].lower().startswith(("date", "observation")):
                continue
            date = row[0].strip()
            value = (row[1].strip() if len(row) > 1 else "")
            out[date] = float(value) if value not in ("", ".", "NA") else None
    return out


# ---------------------------------------------------------------------------
# Pinned numbers from the published season
# ---------------------------------------------------------------------------
# These are NOT magic constants to be sprinkled through the tests; they are the
# headline results of the committed run in memory/, pinned in one place so that
# (a) a site or report built from an older run is caught, and (b) when the engine
# legitimately changes, exactly one line here has to be updated and the diff
# shows what moved.
#
# History:
#   108.31  Season 1 as first published.
#   108.39  after the IR-28 fix (displayed quotes snapped back onto the Rule 612
#           grid), which changed executed prices by fractions of a tick.
PUBLISHED_TOP_USERNAME = "@BetaChaser_3xProxy"
PUBLISHED_TOP_RETURN_PCT = 108.39
PUBLISHED_SPX_RETURN_PCT = 14.415      # real FRED SP500 over the window
PUBLISHED_VIX_MAX = 31.05              # real FRED VIXCLS, 2026-03-27
PUBLISHED_VIX_MAX_DATE = "2026-03-27"
PUBLISHED_SESSIONS = 251
