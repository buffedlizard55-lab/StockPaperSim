"""Season 2: the MasterFeed Invitational, with an eligibility-gated price path.

Season 1 answered "what would twenty aggressive strategies have done on a
calibrated replay of the real index path". Season 2 is intended to answer a
narrower and harder question: **what did the strategies that can be mapped to a
MasterSite project do on an eligible official price set**, with every price, date
and event traceable to a collected file. The
committed Yahoo-backed run is retained as **non-eligible research**; the normal
Season 2 entry point now uses the official Nasdaq backend and stops if its
provenance or redistribution status is not accepted.

Design decisions that differ from Season 1, each deliberate:

* **One real path, no seed panel.**  Real prices are a single realisation, so
  there is no "robustness across synthetic seeds" to publish.  Instead of
  pretending otherwise, Season 2 publishes **cost and liquidity stress runs**:
  the same collected prices with the execution stack doubled and with available
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
SEASON2_SEASON = "Season 2 (2025-2026) - eligibility-gated collected prices"

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


def build_market(root: str = realdata.REAL_ROOT,
                 price_source: str = "nasdaq",
                 require_official: bool = True) -> realdata.RealMarketData:
    md = realdata.build_real_market_data(
        root=root, price_source=price_source, require_official=require_official)
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
    """Per participant: which collected signals it reads, and whether they exist.

    A participant that reads only collected prices has an empty list here and is not
    signal-dependent; a participant whose source could not be collected is
    flagged, so the site can say "data missing" instead of publishing a zero
    return as if it were a result.
    """
    book = getattr(md, "signals", None)
    availability = getattr(book, "availability", {}) if book else {}
    price_derived = set(masterfeed.PRICE_DERIVED)
    rows: List[dict] = []
    for strategy in roster:
        names = []
        external = []
        for name in getattr(strategy, "signal_names", ()):
            meta = availability.get(name, {})
            external_names = [n for n in getattr(strategy, "signal_names", ())
                              if n not in price_derived]
            names.append({"signal": name, "state": meta.get("state", "MISSING"),
                          "files": meta.get("files", []), "url": meta.get("url", ""),
                          "sessions_actionable": None,
                          "price_derived": name in price_derived})
        external = [n for n in getattr(strategy, "signal_names", ())
                    if n not in price_derived]
        waiting = [n for n in external if not book.available(n)] if book else []
        idle_reason = None
        if waiting:
            first = waiting[0]
            meta = availability.get(first, {})
            idle_reason = (
                f"no trades: waiting on {', '.join(waiting)} "
                f"({meta.get('state', 'MISSING')}) from {meta.get('url') or 'n/a'} - "
                f"{meta.get('note', '')}")
        # Signal-dependent means "waits on a collected dataset other than the
        # price of an instrument it can trade".  Reading GLD's close is not a
        # data dependency; reading Form 4 filings is.
        rows.append({
            "username": strategy.spec.username,
            "display_name": strategy.spec.display_name,
            "signals": names,
            "external_signals": external,
            "signal_dependent": bool(external),
            "gate_armed": bool(external) and not waiting,
            "waiting_on": waiting,
            "idle_reason": idle_reason,
        })
    return rows


def run_season2(root: str = memory.DEFAULT_ROOT, real_root: str = realdata.REAL_ROOT,
                labels: Sequence[str] = ("primary",), verbose: bool = False,
                price_source: str = "nasdaq",
                require_official: bool = True) -> List[dict]:
    """Run Season 2, refusing non-eligible price data by default.

    Set ``price_source='yahoo', require_official=False`` only for an explicitly
    labelled research replay; that path is never an official competition run.
    """
    md = build_market(real_root, price_source=price_source,
                      require_official=require_official)
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
                          [{**asdict(st.spec), "factor_exposure": st.spec.factor_exposure,
                            "signal_names": list(getattr(st, "signal_names", ()))}
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
            "crosschecks": realdata.crosscheck_against_fred(
                "SPY", real_root, price_source=price_source),
            "eligibility": md.diagnostics.get("official_eligibility"),
            "price_source": price_source,
            "note": ("Every bar traded in this run is byte-identical to a bar in one of "
                     "the files inventoried here, and the independent audit re-checks "
                     "each one against the collected file rather than against this copy."),
        })
        writer.write_json("signal_status.json", _signal_status(md, roster))
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
            # Signal dependence is read off the roster rather than from a list of
            # usernames typed out here: a strategy that gains a signal must not
            # need this file edited to be classified correctly.
            verification["signal_dependent"] = bool(
                getattr(participant.strategy, "signal_names", ()))
            per_participant.append(verification)
        writer.write_json("verification.json", {
            "note": ("equity_residual_usd is the final equity the engine reported minus "
                     "the equity re-derived from the raw fill stream by sim.ledger "
                     "(average-cost accounting) plus the reported net carry. The stored "
                     "fill tape rounds prices to six decimals, so the residual does not "
                     "have to be exactly zero, but it must stay inside "
                     "rounding_bound_usd, which is the worst case that rounding can "
                     "produce for this account. realized_residual_usd compares like with "
                     "like: the engine's realised P&L is net of explicit cash costs, so "
                     "the re-derived figure has those fees taken off first."),
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
            "ledger": {
                "fills": len(ledger_doc["fills"]),
                "round_trips": len(ledger_doc["round_trips"]),
                "net_pnl_usd": ledger_doc["summary"]["net_pnl_usd"],
                "digest_sha256": ledger.ledger_digest(ledger_doc),
                "files": ledger_files,
            },
            "signal_availability": {
                name: meta.get("state")
                for name, meta in (getattr(book, "availability", {}) or {}).items()},
        })
        record["manifest"] = {k: v for k, v in manifest.items() if k != "files"}
        records.append(record)
    return records


__all__ = ["SEASON2_NAME", "SEASON2_SEASON", "SEASON2_SEED", "build_market",
           "run_season2", "season2_config", "STRESS_LABELS"]
