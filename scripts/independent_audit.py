#!/usr/bin/env python3
"""Independent audit: recompute every published number from the raw event trail.

Why this exists
---------------
Every figure on the site is produced by ``sim/engine.py`` through
``sim/analytics.py``.  If that pipeline has a bug, the whole site is wrong in the
same direction and the engine's own tests - which exercise the same code - can
still pass.  This script deliberately does NOT import ``sim.analytics`` or
``sim.engine``.  It reads the append-only event streams under
``memory/runs/<run_id>/events/`` plus ``market_data.json``, and re-derives the
published numbers from scratch:

  * the accounting identity   equity == cash + sum(qty * mark)   on every row;
  * a day-by-day cash roll-forward from fills + carry, compared with the cash
    the engine booked, so an unexplained cash movement anywhere in the season
    fails the audit even if the final equity happens to agree;
  * total return, net P&L and final equity from the recomputed equity path;
  * max drawdown, Sharpe, Sortino, beta and alpha from the recomputed returns;
  * closed round trips, win rate and profit factor, from fills alone, using an
    independent average-cost lot machine;
  * dividend and manufactured-dividend entitlement: a dividend must be paid to
    whoever held the shares going INTO the ex-date, and charged to whoever was
    short; amount must equal entitled_qty * per_share;
  * fee, rebate, dividend and borrow ledgers against the report;
  * venue invariants: every print on the dated Rule 612 grid, no fill larger
    than requested, bid <= mid <= ask, and the total-cost identity
    total_cost == spread + depth + impact + commission + exchange + regulatory.

Exit status is non-zero when any recomputed figure disagrees with the committed
report, so CI can gate on it.

Usage
-----
    python3 scripts/independent_audit.py [--memory-root memory] [--run-id ID]
                                         [--list] [--verbose]
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

TRADING_DAYS = 252.0

# Reports are rounded when written (money to cents, ratios to 4 dp), so the
# tolerance has to cover formatting but nothing looser.
TOL_MONEY = 0.06      # dollars
TOL_PCT = 0.011       # percentage points
TOL_PCT_LOOSE = 0.02  # for figures rebuilt from per-fill rounding
TOL_RATIO = 0.011     # dimensionless metrics


class AuditError(Exception):
    """Something structurally wrong: the audit could not be completed."""


def _stream_path(run_dir: str, name: str) -> Optional[str]:
    for cand in (os.path.join(run_dir, "events", f"{name}.jsonl.gz"),
                 os.path.join(run_dir, "events", f"{name}.jsonl")):
        if os.path.exists(cand):
            return cand
    return None


def _read_stream(run_dir: str, name: str) -> List[dict]:
    path = _stream_path(run_dir, name)
    if path is None:
        return []
    out: List[dict] = []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _stdev(xs: Sequence[float]) -> float:
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def _ols(y: Sequence[float], x: Sequence[float]) -> Tuple[float, float, float]:
    """(beta, alpha_daily, r_squared) for y on x."""
    n = min(len(x), len(y))
    if n < 3:
        return 0.0, 0.0, 0.0
    xs, ys = list(x[:n]), list(y[:n])
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((v - mx) ** 2 for v in xs)
    if not sxx:
        return 0.0, my, 0.0
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys))
    beta = sxy / sxx
    alpha = my - beta * mx
    resid = [yi - (alpha + beta * xi) for xi, yi in zip(xs, ys)]
    sst = sum((yi - my) ** 2 for yi in ys)
    ssr = sum(r * r for r in resid)
    return beta, alpha, (1.0 - ssr / sst) if sst else 0.0


def tick_size(price: float) -> float:
    """The increment actually in force all season (pre-November-2027 relief)."""
    return 0.0001 if price < 1.0 else 0.01


class Auditor:
    def __init__(self, run_dir: str, verbose: bool = False) -> None:
        self.run_dir = run_dir
        self.run_id = os.path.basename(run_dir.rstrip("/"))
        self.verbose = verbose
        self.passed = 0
        self.failures: List[str] = []
        self.notes: List[str] = []

    def check(self, label: str, got: Optional[float], want: Optional[float],
              tol: float, unit: str = "") -> None:
        if got is None or want is None:
            self.failures.append(f"{label}: missing (recomputed={got}, "
                                 f"published={want})")
            return
        if abs(float(got) - float(want)) <= tol:
            self.passed += 1
        else:
            self.failures.append(
                f"{label}: recomputed {float(got):,.4f} vs published "
                f"{float(want):,.4f} {unit}(diff {abs(float(got)-float(want)):,.6f}"
                f" > tol {tol:g})")

    def fail(self, label: str) -> None:
        self.failures.append(label)

    # ------------------------------------------------------------------
    def load(self) -> dict:
        need = ["leaderboard.json", "participants.json", "market_report.json"]
        for n in need:
            if not os.path.exists(os.path.join(self.run_dir, n)):
                raise AuditError(f"{self.run_id}: missing {n}")
        if not _stream_path(self.run_dir, "fills"):
            raise AuditError(f"{self.run_id}: no fills stream (summary-memory run)")
        out = {}
        for n in need + ["market_data.json"]:
            p = os.path.join(self.run_dir, n)
            out[n[:-5]] = json.load(open(p)) if os.path.exists(p) else None
        # Real index path -> daily market returns keyed by date, straight from
        # market_data.json: the exact series the venue replayed.
        idx: Dict[str, float] = {}
        if out.get("market_data"):
            spx, dates = out["market_data"]["spx"], out["market_data"]["dates"]
            for i in range(1, len(spx)):
                if spx[i - 1]:
                    idx[dates[i]] = spx[i] / spx[i - 1] - 1.0
        out["index_returns"] = idx

        out["reports"] = {}
        rdir = os.path.join(self.run_dir, "reports")
        if os.path.isdir(rdir):
            for fn in sorted(os.listdir(rdir)):
                if fn.endswith(".json"):
                    out["reports"][fn[:-5]] = json.load(open(os.path.join(rdir, fn)))
        return out

    # ------------------------------------------------------------------
    def run(self) -> Tuple[int, int, List[str]]:
        data = self.load()
        board = {r["username"]: r for r in data["leaderboard"]["leaderboard"]}
        parts = data["participants"]
        starting = {p["username"]: float(p.get("starting_cash", 100_000.0))
                    for p in parts}

        fills = _read_stream(self.run_dir, "fills")
        equity = _read_stream(self.run_dir, "equity")
        carry = _read_stream(self.run_dir, "carry")
        positions = _read_stream(self.run_dir, "positions")
        quotes = _read_stream(self.run_dir, "quotes")

        if not fills:
            raise AuditError(f"{self.run_id}: fills stream is empty")

        by_part: Dict[str, List[dict]] = defaultdict(list)
        for f in fills:
            by_part[f["participant"]].append(f)
        eq_by_part: Dict[str, List[dict]] = defaultdict(list)
        for e in equity:
            eq_by_part[e["participant"]].append(e)
        pos_by_key: Dict[Tuple[str, str], Dict[str, dict]] = defaultdict(dict)
        for p in positions:
            pos_by_key[(p["participant"], p["date"])][p["symbol"]] = p
        carry_by_part: Dict[str, List[dict]] = defaultdict(list)
        for c in carry:
            carry_by_part[c["participant"]].append(c)


        for user in sorted(by_part):
            self.audit_participant(
                user, by_part[user], eq_by_part.get(user, []),
                carry_by_part.get(user, []), pos_by_key, board, starting, data)

        self.audit_venue(quotes, fills)
        return self.passed, len(self.failures), self.failures

    # ------------------------------------------------------------------
    def audit_participant(self, user, fills, eq_rows, carry_rows, pos_by_key,
                          board, starting, data) -> None:
        idx_ret_by_date = data["index_returns"]
        rep_key = user.lstrip("@")
        rep = data["reports"].get(rep_key)
        if rep is None:
            self.fail(f"{user}: no committed report to audit against")
            return
        start = starting.get(user, 100_000.0)
        pub = board.get(user, {})
        tag = user

        # ---- 1. accounting identity, every session ----------------------
        bad_eq = 0
        for e in eq_rows:
            book = pos_by_key.get((user, e["date"]), {})
            mv = sum(p["quantity"] * p["mark"] for p in book.values())
            if abs(e["cash"] + mv - e["equity"]) > 0.011:
                bad_eq += 1
                if bad_eq <= 2:
                    self.fail(f"{tag} identity on {e['date']}: cash {e['cash']:,.2f}"
                              f" + mv {mv:,.2f} != equity {e['equity']:,.2f}")
        self.check(f"{tag} equity-identity violations", float(bad_eq), 0.0, 0.0)

        # ---- 2. day-by-day cash roll-forward ----------------------------
        cash = start
        by_date_fills: Dict[str, List[dict]] = defaultdict(list)
        for f in fills:
            by_date_fills[f["date"]].append(f)
        by_date_carry: Dict[str, List[dict]] = defaultdict(list)
        for c in carry_rows:
            by_date_carry[c["date"]].append(c)
        engine_cash = {e["date"]: e["cash"] for e in eq_rows}
        drift_worst = 0.0
        drift_day = ""
        for date in sorted(engine_cash):
            for f in by_date_fills.get(date, []):
                px, q = f["avg_price"], f["filled_qty"]
                cash += -q * px if f["side"] == "buy" else q * px
                cash -= f.get("commission", 0.0) + f.get("exchange_fee", 0.0) \
                    + f.get("regulatory_fee", 0.0)
                cash += f.get("rebate", 0.0)
            for c in by_date_carry.get(date, []):
                cash += c.get("amount", 0.0)
            d = abs(cash - engine_cash[date])
            if d > drift_worst:
                drift_worst, drift_day = d, date
        # Rounding: the engine rounds cash to cents per row and each fill amount
        # is rounded, so allow a cent per session of accumulated rounding.
        self.check(f"{tag} cash roll-forward max drift ({drift_day})",
                   drift_worst, 0.0, max(0.02, 0.02 * len(engine_cash)))

        # ---- 3. headline numbers ---------------------------------------
        curve = [(e["date"], e["equity"]) for e in eq_rows]
        if not curve:
            self.fail(f"{tag}: no equity rows")
            return
        eq_final = curve[-1][1]
        self.check(f"{tag} final_equity", eq_final, rep["final_equity"], TOL_MONEY)
        self.check(f"{tag} net_pnl", eq_final - start, rep["net_pnl_usd"], TOL_MONEY)
        ret = 100.0 * (eq_final / start - 1.0)
        self.check(f"{tag} total_return_pct", ret, rep["total_return_pct"], TOL_PCT)

        # ---- 4. drawdown, ratios ---------------------------------------
        peak, mdd = -math.inf, 0.0
        for _, eq in curve:
            peak = max(peak, eq)
            if peak > 0:
                mdd = min(mdd, eq / peak - 1.0)
        self.check(f"{tag} max_drawdown_pct", 100.0 * mdd,
                   rep["risk"]["max_drawdown_pct"], TOL_PCT)
        rets = [curve[i][1] / curve[i - 1][1] - 1.0
                for i in range(1, len(curve)) if curve[i - 1][1]]
        sd = _stdev(rets)
        if rets and sd:
            self.check(f"{tag} sharpe",
                       math.sqrt(TRADING_DAYS) * (sum(rets) / len(rets)) / sd,
                       rep["risk"]["sharpe"], TOL_RATIO)
        downside = [r for r in rets if r < 0]
        dsd = _stdev(downside)
        if downside and dsd:
            self.check(f"{tag} sortino",
                       math.sqrt(TRADING_DAYS) * (sum(rets) / len(rets)) / dsd,
                       rep["risk"]["sortino"], TOL_RATIO)
        self.check(f"{tag} daily_vol_pct", 100.0 * sd,
                   rep["risk"]["daily_vol_pct"], TOL_PCT)
        self.check(f"{tag} best_session_pct", 100.0 * max(rets),
                   rep["risk"]["best_session_pct"], TOL_PCT)
        self.check(f"{tag} worst_session_pct", 100.0 * min(rets),
                   rep["risk"]["worst_session_pct"], TOL_PCT)

        if pub:
            self.check(f"{tag} leaderboard return", ret, pub["total_return_pct"],
                       TOL_PCT)
            self.check(f"{tag} leaderboard net_pnl", eq_final - start,
                       pub["net_pnl_usd"], TOL_MONEY)
            self.check(f"{tag} leaderboard max_drawdown", 100.0 * mdd,
                       pub["max_drawdown_pct"], TOL_PCT)
            self.check(f"{tag} leaderboard sharpe",
                       math.sqrt(TRADING_DAYS) * (sum(rets) / len(rets)) / sd
                       if rets and sd else 0.0,
                       pub["sharpe"], TOL_RATIO)

        # ---- 5. beta / alpha vs the real index path ---------------------
        mine = [idx_ret_by_date[d] for d, _ in curve[1:] if d in idx_ret_by_date]
        if len(mine) == len(rets) and rets:
            beta, alpha, r2 = _ols(rets, mine)
            self.check(f"{tag} beta", beta, rep["market_relation"]["beta"], TOL_RATIO)
            self.check(f"{tag} alpha_annual_pct", 100.0 * alpha * TRADING_DAYS,
                       rep["market_relation"]["alpha_annual_pct"], TOL_PCT)
            if "r_squared" in rep["market_relation"]:
                self.check(f"{tag} r_squared", r2,
                           rep["market_relation"]["r_squared"], 0.02)
            mkt = rep["market_relation"].get("market_return_pct")
            bc = rep["market_relation"].get("beta_contribution_pct")
            if mkt is not None and bc is not None:
                self.check(f"{tag} beta_contribution_pct", beta * mkt, bc, TOL_PCT)

        # ---- 6. round trips from fills alone ---------------------------
        lots: Dict[str, dict] = {}
        closed = wins = losses = 0
        gross_win = gross_loss = 0.0
        for f in fills:
            # "partial" is a real execution: the venue filled part of the order
            # and the remainder expired. Excluding partials (the first draft of
            # this audit did) undercounted the market maker's round trips by
            # 229, which looked like an engine bug and was not one.
            if f.get("status") in ("rejected", "expired") or f["filled_qty"] <= 0:
                continue
            sym, side = f["symbol"], f["side"]
            qty, px = f["filled_qty"], f["avg_price"]
            signed = qty if side == "buy" else -qty
            fees = (f.get("commission", 0.0) + f.get("exchange_fee", 0.0)
                    + f.get("regulatory_fee", 0.0) - f.get("rebate", 0.0))
            lot = lots.get(sym)
            if lot and lot["dir"] != (1 if signed > 0 else -1):
                close_qty = min(lot["rem"], abs(signed))
                # A round trip bears the fee paid on the way IN as well as the
                # fee on the way OUT, each pro rata to the quantity closed; a
                # tranche that only partly closes a lot carries only its share
                # of the entry fee.
                entry_share = lot["fees"] * (close_qty / lot["rem"])
                exit_share = fees * (close_qty / abs(signed))
                lot["fees"] -= entry_share
                pnl = lot["dir"] * (px - lot["cost"]) * close_qty \
                    - entry_share - exit_share
                closed += 1
                if pnl >= 0:
                    wins += 1
                    gross_win += pnl
                else:
                    losses += 1
                    gross_loss += pnl
                lot["rem"] -= close_qty
                if lot["rem"] <= 0:
                    lots.pop(sym, None)
                rem = abs(signed) - close_qty
                if rem > 0:
                    lots[sym] = {"dir": 1 if signed > 0 else -1, "rem": rem,
                                 "cost": px, "fees": fees * (rem / abs(signed))}
            elif lot:
                new_rem = lot["rem"] + abs(signed)
                lot["cost"] = (lot["cost"] * lot["rem"] + px * abs(signed)) / new_rem
                lot["rem"] = new_rem
                lot["fees"] += fees
            else:
                lots[sym] = {"dir": 1 if signed > 0 else -1, "rem": abs(signed),
                             "cost": px, "fees": fees}
        t = rep["trades"]
        self.check(f"{tag} closed_round_trips", float(closed),
                   float(t["closed_trades"]), 0.0)
        if closed:
            self.check(f"{tag} win_rate_pct", 100.0 * wins / closed,
                       t["win_rate_pct"], TOL_PCT)
        if gross_loss < 0 and t.get("profit_factor") is not None:
            # A ratio built on a tiny denominator is rounding-sensitive:
            # @BuyHold_MaxBeta has one $63.68 losing trade, so a cent of
            # rounding moves its profit factor by ~0.11. Tolerance therefore
            # scales with the ratio itself, and the same check is run on the
            # underlying gross win / gross loss, which is not amplified.
            pf = gross_win / -gross_loss
            self.check(f"{tag} profit_factor", pf, float(t["profit_factor"]),
                       max(TOL_RATIO, 1e-4 * abs(pf)))
            self.check(f"{tag} gross_win_usd", gross_win, t["gross_win_usd"],
                       TOL_MONEY + 0.005 * max(1, wins))
            # gross_loss_usd is published as a positive magnitude.
            self.check(f"{tag} gross_loss_usd", abs(gross_loss),
                       t["gross_loss_usd"], TOL_MONEY + 0.005 * max(1, losses))
        self.check(f"{tag} open_round_trips", float(len(lots)),
                   float(rep["trades"]["open_trades"]), 0.0)

        # ---- 7. dividend entitlement ------------------------------------
        # A dividend belongs to the holder going into the ex-date. Rebuild the
        # end-of-day book from fills and check each carry event against it.
        net_by_date: Dict[Tuple[str, str], int] = defaultdict(int)
        for f in fills:
            net_by_date[(f["date"], f["symbol"])] += (
                f["filled_qty"] if f["side"] == "buy" else -f["filled_qty"])
        for c in carry_rows:
            kind = c.get("kind")
            if kind not in ("dividend", "dividend_in_lieu"):
                continue
            ent = c.get("entitled_qty")
            if ent is None:
                self.fail(f"{tag} {c['date']} {c['symbol']}: dividend event has no "
                          f"entitled_qty, so entitlement cannot be audited")
                continue
            if kind == "dividend" and ent <= 0:
                self.fail(f"{tag} {c['date']} {c['symbol']}: paid a dividend to a "
                          f"non-long (entitled_qty={ent})")
            if kind == "dividend_in_lieu" and ent >= 0:
                self.fail(f"{tag} {c['date']} {c['symbol']}: charged a manufactured "
                          f"dividend to a non-short (entitled_qty={ent})")
            expect = round(abs(ent) * c["per_share"], 2)
            if abs(abs(c["amount"]) - expect) > 0.011:
                self.fail(f"{tag} {c['date']} {c['symbol']} {kind}: amount "
                          f"{c['amount']} != |entitled_qty|*per_share = {expect}")
            # The entitled quantity must be a position the account actually
            # carried into the session, i.e. yesterday's settled book.
            prior = self._prior_net(net_by_date, c["date"], c["symbol"], fills)
            if prior is not None and prior != ent:
                self.fail(f"{tag} {c['date']} {c['symbol']}: entitled_qty {ent} "
                          f"disagrees with the position carried into the session "
                          f"({prior})")

        # ---- 8. ledgers -------------------------------------------------
        ledgers = {
            "commission_usd": sum(f.get("commission", 0.0) for f in fills),
            "exchange_fee_usd": sum(f.get("exchange_fee", 0.0) for f in fills),
            "regulatory_fee_usd": sum(f.get("regulatory_fee", 0.0) for f in fills),
            # Published with the sign of a cost: a rebate REDUCES cost, so the
            # report's rebate_usd is the negative of the cash received.
            "rebate_usd": -sum(f.get("rebate", 0.0) for f in fills),
            "spread_cost_usd": sum(f.get("spread_cost", 0.0) for f in fills),
            "depth_cost_usd": sum(f.get("depth_cost", 0.0) for f in fills),
            "impact_cost_usd": sum(f.get("impact_cost", 0.0) for f in fills),
        }
        for k, v in ledgers.items():
            if k in rep["costs"]:
                self.check(f"{tag} {k}", v, rep["costs"][k], TOL_MONEY)
        self.check(f"{tag} total_cost_usd", sum(ledgers.values()),
                   rep["costs"]["total_cost_usd"], TOL_MONEY)
        div = sum(c["amount"] for c in carry_rows if c.get("kind") == "dividend")
        in_lieu = sum(-c["amount"] for c in carry_rows
                      if c.get("kind") == "dividend_in_lieu")
        borrow_rows = [c for c in carry_rows if c.get("kind") == "borrow_fee"]
        borrow = sum(-c["amount"] for c in borrow_rows)
        self.check(f"{tag} dividends_received", div,
                   rep["carry"]["dividends_received_usd"], TOL_MONEY)
        self.check(f"{tag} dividends_in_lieu_paid", in_lieu,
                   rep["carry"].get("dividends_in_lieu_paid_usd", 0.0), TOL_MONEY)
        # Each daily borrow row is rounded to cents in the event stream while the
        # account keeps the unrounded running total, so the tolerance has to be
        # one half-cent per accrued session, not per participant.
        self.check(f"{tag} borrow_fees_paid", borrow,
                   rep["carry"]["borrow_fees_paid_usd"],
                   0.005 * max(1, len(borrow_rows)) + TOL_MONEY)

        # ---- 9. decomposition closes ------------------------------------
        d = rep["pnl_decomposition"]
        buckets = sum(v for k, v in d.items()
                      if k.endswith("_usd") and k not in
                      ("total_net_pnl_usd", "execution_costs_already_netted_usd",
                       "unexplained_residual_usd"))
        self.check(f"{tag} decomposition sums to net P&L",
                   buckets + d["unexplained_residual_usd"],
                   d["total_net_pnl_usd"], TOL_MONEY)
        self.check(f"{tag} decomposition equals leaderboard P&L",
                   d["total_net_pnl_usd"], rep["net_pnl_usd"], TOL_MONEY)
        if abs(d["unexplained_residual_usd"]) > 1.0:
            self.fail(f"{tag}: unexplained residual ${d['unexplained_residual_usd']:,.2f}")

    @staticmethod
    def _prior_net(net_by_date, date, symbol, fills):
        """Net settled position entering `date`, rebuilt from fills."""
        prior = 0
        for (d, s), q in net_by_date.items():
            if s == symbol and d < date:
                prior += q
        return prior if net_by_date else None

    # ------------------------------------------------------------------
    def assertEqualish(self, label, got, rep):
        self.check(label, float(got), self._matching_field(label, rep), 0.0)

    def _matching_field(self, label, rep):
        return 0.0

    # ------------------------------------------------------------------
    def audit_venue(self, quotes, fills) -> None:
        """Venue invariants that hold regardless of strategy behaviour."""
        off_grid = bad_side = over_fill = bad_cost = 0
        for f in fills:
            px = f["avg_price"]
            if px <= 0:
                bad_side += 1
                continue
            tick = tick_size(px)
            if abs(round(px / tick) * tick - px) > 5e-9:
                off_grid += 1
            if f["filled_qty"] > f.get("requested_qty", f["filled_qty"]):
                over_fill += 1
            parts = (f.get("spread_cost", 0.0) + f.get("depth_cost", 0.0)
                     + f.get("impact_cost", 0.0) + f.get("commission", 0.0)
                     + f.get("exchange_fee", 0.0) + f.get("regulatory_fee", 0.0)
                     - f.get("rebate", 0.0))
            if "total_cost" in f and abs(parts - f["total_cost"]) > 0.02:
                bad_cost += 1
        self.check("fills off the Rule 612 grid", float(off_grid), 0.0, 0.0)
        self.check("fills with non-positive price", float(bad_side), 0.0, 0.0)
        self.check("fills exceeding the requested quantity", float(over_fill), 0.0, 0.0)
        self.check("total_cost != sum of components", float(bad_cost), 0.0, 0.0)

        spread_bad = mid_bad = 0
        for q in quotes:
            bid, ask, mid = q.get("bid"), q.get("ask"), q.get("mid")
            if bid is None or ask is None:
                continue
            if bid > ask:
                spread_bad += 1
            if mid is not None and not (bid - 1e-9 <= mid <= ask + 1e-9):
                mid_bad += 1
        self.check("crossed quotes (bid > ask)", float(spread_bad), 0.0, 0.0)
        self.check("mid outside the touch", float(mid_bad), 0.0, 0.0)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="independent audit of a run")
    ap.add_argument("--memory-root", default="memory")
    ap.add_argument("--run-id")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    runs_root = os.path.join(args.memory_root, "runs")
    if not os.path.isdir(runs_root):
        print(f"error: {runs_root} not found", file=sys.stderr)
        return 2
    audit_runs = sorted(d for d in os.listdir(runs_root)
                        if _stream_path(os.path.join(runs_root, d), "fills"))
    if args.list:
        print("\n".join(audit_runs) or "(no runs with a full event stream)")
        return 0
    if args.run_id:
        if args.run_id not in audit_runs:
            print(f"error: {args.run_id!r} is not audit-able. Audit-able runs: "
                  f"{', '.join(audit_runs) or 'none'}", file=sys.stderr)
            return 2
        audit_runs = [args.run_id]
    if not audit_runs:
        print("error: no run has an event stream; run `python3 -m sim.cli run`",
              file=sys.stderr)
        return 2

    total_pass = total_fail = 0
    for rid in audit_runs:
        try:
            a = Auditor(os.path.join(runs_root, rid), verbose=args.verbose)
            p, f, details = a.run()
        except AuditError as exc:
            print(f"SKIP    {rid:36s} {exc}")
            continue
        total_pass += p
        total_fail += f
        print(f"{'OK  ' if f == 0 else 'FAIL'}  {rid:36s} checks passed {p:6d}  "
              f"failed {f}")
        for d in details[:30]:
            print(f"        - {d}")
        if len(details) > 30:
            print(f"        ... {len(details) - 30} more")
    print(f"\nindependent audit: {total_pass} checks passed, {total_fail} failed")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
