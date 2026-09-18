"""Season 2: the MasterFeed Invitational, run on real collected prices.

Season 1 answered "what would twenty aggressive strategies have done on a
calibrated replay of the real index path".  Season 2 answers a narrower and
harder question: **what did the strategies that can be mapped to a MasterSite
project actually do on the real prices of the instruments they traded**, with
every price, date and event traceable to a collected file.

Design decisions that differ from Season 1, each deliberate:

* **One real path, no seed panel.**  Real prices are a single realisation, so
  there is no "robustness across synthetic seeds" to publish.  Instead of
  pretending otherwise, Season 2 publishes **cost and liquidity stress runs**:
  the same real prices with the execution stack doubled and with available
  liquidity halved.  That measures sensitivity to the assumptions that really
  are still modelled (IR-40, IR-41).
* **Real dividends.**  Ex-dates and amounts come from the vendor event feed
  collected into ``data/real/prices/yahoo/*.json``, not from a declared schedule.
* **Signals must exist.**  A participant whose signal file is missing produces no
  trades and is reported as DATA-MISSING; nothing is back-filled with a proxy.
* **The ledger is a first-class output.**  Every run writes ``ledger_fills``,
  ``ledger_trips``, a CSV and a per-participant verification block.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Dict, List, Optional, Sequence, Tuple

import hashlib

from . import analytics, config, engine, ledger, masterfeed, memory, realdata
from .strategies_mf import build_roster_mf

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


SEASON2_SEED = 20260918
SEASON2_NAME = "StockPaperSim MasterFeed Invitational"
SEASON2_SEASON = "Season 2 (2025-2026) - real collected prices"

STRESS_LABELS = ("primary", "stress-costs2x", "stress-thinliquidity")


def season2_config(label: str = "primary") -> config.CompetitionConfig:
    """Competition config for Season 2, optionally under a stress assumption."""
    cfg = config.CompetitionConfig(
        name=SEASON2_NAME,
        season=SEASON2_SEASON,
        start=realdata.SEASON2_START,
        end=realdata.SEASON2_END,
        starting_cash=config.STARTING_CASH,
        seed=SEASON2_SEED,
        scenarios=1,
        force_liquidate_at_end=True,
        rank_metric="total_return_pct",
    )
    if label == "stress-costs2x":
        # Double every modelled execution cost and charge TradingView's $1 per
        # order, to separate "the signal worked" from "the venue was cheap".
        cfg.liquidity.spread_k_ticks *= 2.0
        cfg.impact.coefficient *= 2.0
        cfg.costs.commission_per_order = 1.0
        cfg.costs.taker_fee_per_share *= 2.0
    elif label == "stress-thinliquidity":
        # Halve the volume available to the participant and tighten the
        # participation cap: what happens if the real tape was thinner than the
        # vendor's consolidated volume suggests.
        cfg.liquidity.max_participation = 0.05
        cfg.liquidity.touch_size_round_lots *= 0.5
    elif label != "primary":
        raise ValueError(f"unknown Season 2 stress label {label!r}")
    return cfg


def build_market(root: str = realdata.REAL_ROOT) -> realdata.RealMarketData:
    md = realdata.build_real_market_data(root=root)
    md.signals = masterfeed.build_signal_book(md, root=root)  # type: ignore[attr-defined]
    return md


def _signal_fire_counts(md, book) -> dict:
    """How many competition sessions each signal was non-zero / actionable for.

    The brief asks for an explanation of *why* a strategy returned what it did.
    For a signal-driven strategy the first fact in that explanation is whether
    the signal ever fired, so it is counted here (over the competition window
    only) and published next to the result.
    """
    out: Dict[str, dict] = {}
    t0 = md.first_competition_index
    for name, array in book.arrays.items():
        if name.startswith("insider_") and "::" in name:
            continue
        window = array[t0:]
        out[name] = {
            "sessions_actionable": sum(1 for v in window if v != 0.0),
            "sessions_in_window": len(window),
            "last_value": round(window[-1], 4) if window else None,
            "state": book.availability.get(name, {}).get("state", "MISSING"),
        }
    return out


def _signal_status(md, roster) -> List[dict]:
    book = getattr(md, "signals", None)
    rows: List[dict] = []
    for strategy in roster:
        username = strategy.spec.username
        rows.append({
            "username": username,
            "display_name": strategy.spec.display_name,
            "signal_availability": getattr(book, "availability", {}) if book else {},
        })
    return rows


def run_season2(root: str = memory.DEFAULT_ROOT, real_root: str = realdata.REAL_ROOT,
                labels: Sequence[str] = ("primary",), verbose: bool = False) -> List[dict]:
    """Run Season 2 for each requested assumption set and write memory + ledger."""
    md = build_market(real_root)
    book = getattr(md, "signals")
    roster = build_roster_mf()
    records: List[dict] = []
    for label in labels:
        cfg = season2_config(label)
        run_id = f"season2-{label}-seed{SEASON2_SEED}"
        writer = memory.RunWriter(run_id, root=root)
        comp = engine.CompetitionEngine(cfg, md, seed=SEASON2_SEED, writer=writer,
                                        roster=roster, full_memory=(label == "primary"),
                                        verbose=verbose)
        record = comp.run()
        record["run_id"] = run_id
        record["label"] = label
        record["full_memory"] = (label == "primary")

        writer.write_json("market_report.json", record["market"])
        writer.write_json("factor_report.json", record["factors"])
        writer.write_json("leaderboard.json", {
            "leaderboard": record["leaderboard"], "rank_metric": cfg.rank_metric})
        writer.write_json("irregularities.json", record["irregularities"])
        writer.write_json("participants.json",
                          [{**asdict(st.spec), "factor_exposure": st.spec.factor_exposure}
                           for st in comp.roster])
        writer.write_json("masterfeed.json", {
            "site": masterfeed.MASTER_SITE_URL,
            "register": masterfeed.signal_register(),
            "availability": getattr(book, "availability", {}),
            "provenance": getattr(book, "provenance", {}),
            "fire_counts": _signal_fire_counts(md, book),
        })
        writer.write_json("data_provenance.json", {
            "diagnostics": md.diagnostics,
            "inventory": realdata.data_inventory(real_root),
        })
        if label == "primary":
            writer.write_json("signal_book.json", book.as_dict())
            writer.write_json("market_data.json", engine._market_data_dump(md))

        # ---- the verified trade ledger ---------------------------------
        # Built from the engine's own Fill objects, then written next to the
        # event memory.  The independent audit re-reads the *gzipped fills
        # stream* and re-derives the same numbers without importing the engine.
        fills: List[dict] = []
        for participant in comp.participants:
            for fill in participant.account.fills:
                fills.append(fill.to_row())
        ledger_doc = ledger.build_ledger(fills, md)
        written = ledger.write_ledger(writer.dir, ledger_doc)
        ledger_files = {}
        for rel in written:
            path = os.path.join(writer.dir, rel)
            with open(path, "rb") as handle:
                ledger_files[rel] = {
                    "sha256": _sha256_bytes(handle.read()),
                    "bytes": os.path.getsize(path)}
        writer.write_json("ledger_manifest.json", ledger_files)

        per_participant: List[dict] = []
        for participant in comp.participants:
            user = participant.spec.username
            rep = next((r for r in (record.get("reports") or [])
                        if r.get("username") == user), {})
            carry = (rep.get("carry") or {}).get("net_carry_usd", 0.0)
            decomposition = rep.get("pnl_decomposition") or {}
            verification = ledger.verify_ledger(
                [f for f in fills if f["participant"] == user], md,
                engine_final_equity=rep.get("final_equity"),
                engine_realized_pnl=decomposition.get(analytics.DECOMPOSITION_REALIZED),
                carry_net_usd=carry or 0.0, starting_cash=cfg.starting_cash)
            verification["username"] = user
            verification["sessions_with_orders"] = rep.get("sessions_with_orders")
            verification["signal_dependent"] = user in (
                "@FDACatalyst_Rider", "@FDA_ClusterFade", "@InsiderCopycat_Max",
                "@InsiderCluster_Alpha", "@CEO_CFO_Conviction", "@MLB_Attention_Momo",
                "@MLB_Upset_Short", "@Weather_ColdSnap_Max", "@Kalshi_Attention_Timer",
                "@YieldCurve_Rotator")
            per_participant.append(verification)
        writer.write_json("verification.json", {
            "note": ("equity_residual_usd is the difference between the final equity "
                     "the engine reported and the equity re-derived from the raw fill "
                     "stream by sim.ledger (average-cost accounting) plus the reported "
                     "net carry. It should be exactly zero; anything else is a bug."),
            "ledger_files": written,
            "ledger_digest_sha256": ledger.ledger_digest(ledger_doc),
            "summary": ledger_doc["summary"],
            "per_participant": per_participant,
            "max_abs_equity_residual_usd": max(
                (abs(v["equity_residual_usd"]) for v in per_participant
                 if v.get("equity_residual_usd") is not None), default=0.0),
            "all_residuals_within_rounding_bound": all(
                v.get("within_rounding_bound", True) for v in per_participant),
        })

        manifest = writer.finalise({
            "run_id": run_id,
            "season": "season2",
            "label": label,
            "seed": SEASON2_SEED,
            "competition": {"name": cfg.name, "season": cfg.season,
                            "start": cfg.start, "end": cfg.end,
                            "starting_cash": cfg.starting_cash,
                            "rank_metric": cfg.rank_metric},
            "config_fingerprint": cfg.fingerprint(),
            "winner": record["winner"],
            "top3": [{"rank": r["rank"], "username": r["username"],
                      "total_return_pct": r["total_return_pct"]}
                     for r in record["leaderboard"][:3]],
            "market": record["market"],
            "factors": record["factors"],
            "timing": record["timing"],
            "irregularity_count": len(record["irregularities"]),
            "data_sources": {
                "market": md.diagnostics.get("price_series"),
                "fred": md.diagnostics.get("market_series"),
                "collected_from": "data/real (see data/real/collection_manifest.json)",
            },
            "ledger_summary": ledger_doc["summary"],
        })
        record["manifest"] = {k: v for k, v in manifest.items() if k != "files"}
        records.append(record)
    return records


__all__ = ["SEASON2_NAME", "SEASON2_SEASON", "SEASON2_SEED", "build_market",
           "run_season2", "season2_config", "STRESS_LABELS"]
