"""Performance analytics, attribution and post-mortem narrative generation.

Everything in this module is computed from **realised numbers produced by the
simulation** - equity curves, fills, rejected orders and the replay price
series.  Nothing here reads a strategy's declared priors to decide whether it
worked; the priors are only used afterwards, to *label* the causes that the
numbers already identified.  That ordering is deliberate: it is the difference
between an explanation and a rationalisation.

Method references (used for the formulas, all standard):

  * Sharpe ratio (excess return / standard deviation, annualised by sqrt(252))
    https://doi.org/10.1086/260062 - Sharpe (1966), "Mutual Fund Performance",
    Journal of Political Economy 74(1):119-138.
  * Sortino ratio (downside deviation only)
    https://doi.org/10.2469/faj.v50.6.48 - Sortino & Price (1994),
    "Performance Measurement in a Downside Risk Framework",
    Financial Analysts Journal 50(6):59-64.
  * Maximum drawdown / Calmar: standard practice, no single canonical paper.
  * Implementation shortfall (decision price vs realised cost, including
    delayed entry and missed trades)
    https://doi.org/10.2469/faj.v44.5.28 - Perold (1988), "The Implementation
    Shortfall: Paper versus Reality", Financial Analysts Journal 44(5):6-31.
  * Square-root market impact law
    Almgren, Thum, Hauptmann & Li (2005), "Direct Estimation of Equity Market
    Impact", Risk 18(7):58-62 (KNOWN-NOT-FETCHED: neither the publisher page
    nor the author-hosted PDF was retrievable on 2026-09-17 - IR-26); framework
    from Almgren & Chriss (2001), Journal of Risk 3(2):21-40,
    https://doi.org/10.21314/JOR.2001.041.
  * Cross-sectional momentum and its long-horizon reversal
    https://doi.org/10.1111/j.1540-6261.1993.tb04681.x - Jegadeesh & Titman
    (1993), "Returns to Buying Winners and Selling Losers", Journal of
    Finance 48(1):65-91.
  * Low-volatility anomaly (used for the VOL factor)
    https://doi.org/10.1111/j.1540-6261.1972.tb03157.x - Black, Jensen &
    Scholes (1972), "The Capital Asset Pricing Model: Some Empirical Tests".
  * Historical VaR / CVaR (no distributional assumption)
    https://doi.org/10.1111/1467-9965.00068 - Artzner, Delbaen, Eber & Heath
    (1999), "Coherent Measures of Risk", Mathematical Finance 9(3):203-228.
    "Coherent Measures of Risk", Mathematical Finance 9(3):203-228.

Risk-free rate: SIM CHOICE, rf = 0 for Sharpe/Sortino.  A one-year paper
competition in which participants pay nothing on cash balances is already
documented in research/LIMITATIONS.json (L-06); mixing a real T-bill rate into
the numerator while paying no carry on cash would make the ratio inconsistent.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .microstructure import BUY, SELL

TRADING_DAYS = config.TRADING_DAYS_PER_YEAR


# ==========================================================================
# Small numeric helpers (pure stdlib)
# ==========================================================================

def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: Sequence[float], sample: bool = True) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mu = _mean(xs)
    denom = (n - 1) if sample else n
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / denom)


def _percentile(sorted_xs: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile on an already-sorted sequence."""
    if not sorted_xs:
        return 0.0
    if len(sorted_xs) == 1:
        return sorted_xs[0]
    pos = (len(sorted_xs) - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_xs) - 1)
    frac = pos - lo
    return sorted_xs[lo] * (1 - frac) + sorted_xs[hi] * frac


def _skew(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mu = _mean(xs)
    sd = _stdev(xs, sample=False)
    if sd == 0:
        return 0.0
    return (n / ((n - 1) * (n - 2))) * sum(((x - mu) / sd) ** 3 for x in xs)


def _kurtosis(xs: Sequence[float]) -> float:
    """Excess kurtosis (0 for a normal distribution)."""
    n = len(xs)
    if n < 4:
        return 0.0
    mu = _mean(xs)
    sd = _stdev(xs, sample=False)
    if sd == 0:
        return 0.0
    m4 = sum((x - mu) ** 4 for x in xs) / n
    return m4 / sd ** 4 - 3.0


def _ols(y: Sequence[float], x: Sequence[float]) -> dict:
    """Univariate OLS: y = alpha + beta * x."""
    n = min(len(x), len(y))
    if n < 3:
        return {"beta": 0.0, "alpha_daily": 0.0, "r_squared": 0.0,
                "residual_std": 0.0, "n": n, "alpha_annual": 0.0}
    xs, ys = list(x[:n]), list(y[:n])
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((xi - mx) ** 2 for xi in xs)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys))
    beta = sxy / sxx if sxx else 0.0
    alpha = my - beta * mx
    resid = [yi - (alpha + beta * xi) for xi, yi in zip(xs, ys)]
    sst = sum((yi - my) ** 2 for yi in ys)
    ssr = sum(r * r for r in resid)
    r2 = 1.0 - ssr / sst if sst else 0.0
    return {"beta": beta, "alpha_daily": alpha, "alpha_annual": alpha * TRADING_DAYS,
            "r_squared": max(0.0, r2), "residual_std": _stdev(resid), "n": n}


# ==========================================================================
# Return and risk series
# ==========================================================================

def daily_returns(curve: Sequence[Tuple[str, float]]) -> List[float]:
    """Simple daily returns from an equity curve [(date, equity), ...]."""
    out = []
    for i in range(1, len(curve)):
        prev = curve[i - 1][1]
        out.append(curve[i][1] / prev - 1.0 if prev else 0.0)
    return out


def drawdown_series(curve: Sequence[Tuple[str, float]]) -> List[Tuple[str, float]]:
    """Drawdown as a negative fraction of equity at each date."""
    out, peak = [], -math.inf
    for date, eq in curve:
        peak = max(peak, eq)
        out.append((date, (eq / peak - 1.0) if peak else 0.0))
    return out


def max_drawdown(curve: Sequence[Tuple[str, float]]) -> dict:
    """Largest peak-to-trough decline, plus its timing and recovery."""
    if not curve:
        return {"max_drawdown_pct": 0.0, "max_drawdown_usd": 0.0}
    dd = drawdown_series(curve)
    trough_i = min(range(len(dd)), key=lambda i: dd[i][1])
    peak_i = max(range(trough_i + 1), key=lambda i: curve[i][1]) if trough_i else 0
    peak_eq = curve[peak_i][1]
    trough_eq = curve[trough_i][1]
    recovery_i = next((i for i in range(trough_i, len(curve))
                       if curve[i][1] >= peak_eq), None)
    return {
        "max_drawdown_pct": round(100.0 * dd[trough_i][1], 4),
        "max_drawdown_usd": round(trough_eq - peak_eq, 2),
        "peak_date": curve[peak_i][0],
        "peak_equity": round(peak_eq, 2),
        "trough_date": curve[trough_i][0],
        "trough_equity": round(trough_eq, 2),
        "recovery_date": curve[recovery_i][0] if recovery_i is not None else None,
        "sessions_underwater": (len(curve) - 1 - trough_i) if recovery_i is None
                               else (recovery_i - trough_i),
        "never_recovered": recovery_i is None,
    }


def var_cvar(returns: Sequence[float], level: float = 0.95) -> dict:
    """Historical VaR and CVaR (expected shortfall). No distribution assumed."""
    if len(returns) < 20:
        return {"var_95_pct": 0.0, "cvar_95_pct": 0.0, "var_99_pct": 0.0,
                "cvar_99_pct": 0.0}
    s = sorted(returns)
    out = {}
    for tag, lv in (("95", 0.95), ("99", 0.99)):
        var = _percentile(s, 1.0 - lv)
        tail = [r for r in s if r <= var] or [s[0]]
        out[f"var_{tag}_pct"] = round(100.0 * var, 4)
        out[f"cvar_{tag}_pct"] = round(100.0 * _mean(tail), 4)
    return out


def monthly_returns(curve: Sequence[Tuple[str, float]]) -> List[dict]:
    """Calendar-month returns off the equity curve (compounded daily marks)."""
    by_month: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    for date, eq in curve:
        by_month[date[:7]].append((date, eq))
    rows, prev_last = [], None
    for month in sorted(by_month):
        pts = by_month[month]
        first_prev = prev_last if prev_last is not None else pts[0][1]
        last = pts[-1][1]
        rows.append({
            "month": month,
            "sessions": len(pts),
            "start_equity": round(first_prev, 2),
            "end_equity": round(last, 2),
            "return_pct": round(100.0 * (last / first_prev - 1.0), 4) if first_prev else 0.0,
        })
        prev_last = last
    return rows


# ==========================================================================
# Trade reconstruction (round trips from the fill tape)
# ==========================================================================

def round_trips(fills: Sequence) -> List[dict]:
    """Average-cost round trips per symbol, in fill order.

    A round trip closes when cumulative quantity crosses zero (long) or returns
    to zero (short).  Remaining open inventory is emitted as an open trade so
    that no fill is silently dropped from the attribution.
    """
    open_lots: Dict[str, dict] = {}
    trips: List[dict] = []

    def _emit(symbol: str, lot: dict, exit_date: str, exit_px: float,
              qty: int, fees: float, status: str, all_in: float = 0.0) -> None:
        sign = 1.0 if lot["direction"] == "long" else -1.0
        gross = sign * (exit_px - lot["avg_cost"]) * qty
        trips.append({
            "symbol": symbol,
            "direction": lot["direction"],
            "status": status,
            "entry_date": lot["entry_date"],
            "exit_date": exit_date,
            "quantity": qty,
            "entry_price": round(lot["avg_cost"], 6),
            "exit_price": round(exit_px, 6),
            "gross_pnl": round(gross, 2),
            # Explicit cash costs only (commission, exchange and regulatory
            # fees, less maker rebates).  Spread, depth and impact are NOT
            # subtracted here because they are already inside the fill prices
            # that produced gross_pnl; subtracting them again double counts and
            # shows up as a large "unexplained residual" in the decomposition.
            "fees": round(fees, 2),
            "all_in_cost_usd": round(all_in, 2),
            "net_pnl": round(gross - fees, 2),
            "return_on_risk_pct": round(100.0 * gross / (lot["avg_cost"] * qty), 4)
                                  if lot["avg_cost"] * qty else 0.0,
            "sessions_held": lot.get("sessions"),
        })

    for f in fills:
        if f.filled_qty <= 0 or f.status in ("rejected", "expired"):
            continue
        sym = f.order.symbol
        signed = f.filled_qty if f.order.side == BUY else -f.filled_qty
        side = "long" if signed > 0 else "short"
        px, qty = f.avg_price, abs(signed)
        fees = f.commission + f.exchange_fee + f.regulatory_fee - f.rebate
        all_in = f.total_cost
        lot = open_lots.get(sym)
        if lot is not None and lot["remaining"] > 0 and lot["direction"] != side:
            # Opposite side: close as much of the open lot as this fill allows.
            close_qty = min(lot["remaining"], qty)
            lot_qty = lot["remaining"]
            # Fees paid on the way INTO the lot belong to the round trip, so they
            # are allocated pro rata across the tranches that close it.  Charging
            # only the exit leg's fee (the earlier behaviour) dropped the entry
            # fee entirely whenever a lot closed in one go, which is why the P&L
            # decomposition carried a few hundred dollars of "unexplained"
            # residual per participant.
            entry_fee_share = lot["fees"] * (close_qty / lot_qty) if lot_qty else 0.0
            exit_fee_share = fees * (close_qty / qty) if qty else fees
            entry_allin_share = lot["all_in"] * (close_qty / lot_qty) if lot_qty else 0.0
            exit_allin_share = all_in * (close_qty / qty) if qty else all_in
            _emit(sym, lot, f.date, px, close_qty,
                  entry_fee_share + exit_fee_share, "closed",
                  all_in=entry_allin_share + exit_allin_share)
            lot["remaining"] -= close_qty
            lot["fees"] -= entry_fee_share
            lot["all_in"] -= entry_allin_share
            residual = qty - close_qty
            if lot["remaining"] <= 0:
                open_lots.pop(sym, None)
            if residual > 0:
                # Flipped through zero: the remainder opens a NEW lot in the
                # opposite direction at the same fill price.  Dropping it (the
                # earlier behaviour) lost the entire P&L of the flipped
                # position, which showed up as a multi-thousand-dollar
                # unexplained residual in the P&L decomposition.
                open_lots[sym] = {"direction": side, "remaining": residual,
                                  "avg_cost": px, "entry_date": f.date,
                                  "fees": fees * (residual / qty) if qty else fees,
                                  "all_in": all_in * (residual / qty) if qty else all_in,
                                  "sessions": 0}
            continue
        if lot is None or lot["remaining"] <= 0:
            open_lots[sym] = {"direction": side, "remaining": qty,
                              "avg_cost": px, "entry_date": f.date,
                              "fees": fees, "all_in": all_in, "sessions": 0}
            continue
        # Same side: add to the open lot at the blended average cost.
        prev, new = lot["remaining"], qty
        lot["avg_cost"] = (lot["avg_cost"] * prev + px * new) / (prev + new)
        lot["remaining"] = prev + new
        lot["fees"] += fees
        lot["all_in"] += all_in

    for sym, lot in open_lots.items():
        if lot["remaining"]:
            _emit(sym, lot, "OPEN", lot["avg_cost"], lot["remaining"],
                  lot["fees"], "open", all_in=lot.get("all_in", 0.0))
    return trips


def holding_days(trips: Sequence[dict], date_index: Dict[str, int]) -> List[dict]:
    """Attach session holding periods using the calendar date index."""
    for tr in trips:
        if tr["status"] == "open" or tr["exit_date"] == "OPEN":
            tr["sessions_held"] = None
            continue
        a, b = date_index.get(tr["entry_date"]), date_index.get(tr["exit_date"])
        tr["sessions_held"] = (b - a) if (a is not None and b is not None) else None
    return trips


def market_exposure_sessions(trips: Sequence[dict],
                             date_index: Dict[str, int]) -> dict:
    """Non-overlapping measure of time in market, per symbol and in total.

    ``sessions_held`` is measured per closed tranche of an average-cost lot, so
    every tranche of the same lot starts at that lot's entry date.  When a
    position is trimmed repeatedly the tranche intervals overlap, and summing or
    averaging them overstates how long capital was actually committed.  This
    unions the intervals instead, which is the number that can be compared
    against the length of the season.
    """
    per_symbol: Dict[str, set] = {}
    for tr in trips:
        a = date_index.get(tr.get("entry_date") or "")
        if a is None:
            continue
        exit_date = tr.get("exit_date")
        b = date_index.get(exit_date) if exit_date not in (None, "OPEN") else None
        if b is None:
            b = max(date_index.values()) if date_index else a
        if b < a:
            a, b = b, a
        bucket = per_symbol.setdefault(tr["symbol"], set())
        bucket.update(range(a, b + 1))
    union: set = set()
    for bucket in per_symbol.values():
        union |= bucket
    tranche_sessions = sum(t["sessions_held"] for t in trips
                           if t.get("sessions_held"))
    return {
        "sessions_in_market": len(union),
        "per_symbol_sessions_in_market": {k: len(v) for k, v in
                                          sorted(per_symbol.items(),
                                                 key=lambda kv: -len(kv[1]))},
        "sum_of_tranche_sessions": tranche_sessions,
        "tranche_intervals_overlap": tranche_sessions > len(union),
    }


def trade_statistics(trips: Sequence[dict]) -> dict:
    closed = [t for t in trips if t["status"] == "closed"]
    wins = [t for t in closed if t["net_pnl"] > 0]
    losses = [t for t in closed if t["net_pnl"] <= 0]
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = -sum(t["net_pnl"] for t in losses)
    streaks = _longest_streaks([t["net_pnl"] for t in closed])
    best = max(closed, key=lambda t: t["net_pnl"], default=None)
    worst = min(closed, key=lambda t: t["net_pnl"], default=None)
    holds = [t["sessions_held"] for t in closed if t.get("sessions_held")]
    return {
        "closed_trades": len(closed),
        "open_trades": sum(1 for t in trips if t["status"] == "open"),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 3) if closed else 0.0,
        "gross_win_usd": round(gross_win, 2),
        "gross_loss_usd": round(gross_loss, 2),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else None,
        "avg_win_usd": round(gross_win / len(wins), 2) if wins else 0.0,
        "avg_loss_usd": round(-gross_loss / len(losses), 2) if losses else 0.0,
        "expectancy_usd_per_trade": round(_mean([t["net_pnl"] for t in closed]), 2)
                                    if closed else 0.0,
        "best_trade": {k: best[k] for k in ("symbol", "net_pnl", "entry_date",
                                            "exit_date", "direction")} if best else None,
        "worst_trade": {k: worst[k] for k in ("symbol", "net_pnl", "entry_date",
                                              "exit_date", "direction")} if worst else None,
        "longest_win_streak": streaks["win"],
        "longest_loss_streak": streaks["loss"],
        "avg_sessions_held": round(_mean(holds), 2) if holds else 0.0,
        "median_sessions_held": _percentile(sorted(holds), 0.5) if holds else 0.0,
    }


def _longest_streaks(pnls: Sequence[float]) -> dict:
    best_w = best_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1
            cur_l = 0
            best_w = max(best_w, cur_w)
        else:
            cur_l += 1
            cur_w = 0
            best_l = max(best_l, cur_l)
    return {"win": best_w, "loss": best_l}


def symbol_contribution(trips: Sequence[dict], open_marks: Optional[Dict[str, float]] = None,
                        open_positions: Optional[Dict[str, int]] = None) -> List[dict]:
    """P&L by symbol: realised round trips plus mark-to-market on what is open."""
    by_sym: Dict[str, dict] = defaultdict(lambda: {"realized": 0.0, "open": 0.0,
                                                     "trades": 0, "fees": 0.0,
                                                     "all_in": 0.0})
    for t in trips:
        row = by_sym[t["symbol"]]
        row["fees"] += t["fees"]
        row["all_in"] += t.get("all_in_cost_usd", 0.0)
        if t["status"] == "closed":
            row["realized"] += t["net_pnl"]
            row["trades"] += 1
        else:
            row["open"] += t["net_pnl"]
    if open_positions and open_marks:
        for sym, qty in open_positions.items():
            mark = open_marks.get(sym)
            if mark is None or qty == 0:
                continue
            lot = next((t for t in trips if t["symbol"] == sym
                        and t["status"] == "open"), None)
            if lot is None:
                continue
            sign = 1.0 if qty > 0 else -1.0
            by_sym[sym]["open"] = round(sign * (mark - lot["entry_price"]) * abs(qty), 2)
    rows = [{"symbol": s, "realized_pnl": round(v["realized"], 2),
             "open_pnl": round(v["open"], 2),
             "total_pnl": round(v["realized"] + v["open"], 2),
             "closed_trades": v["trades"], "fees": round(v["fees"], 2),
             "all_in_cost_usd": round(v["all_in"], 2)}
            for s, v in by_sym.items()]
    rows.sort(key=lambda r: r["total_pnl"], reverse=True)
    return rows


# ==========================================================================
# Execution cost accounting
# ==========================================================================

def cost_report(fills: Sequence, starting_cash: float) -> dict:
    comps = defaultdict(float)
    traded_notional = 0.0
    shares = 0
    n_fills = 0
    slips: List[float] = []
    exec_bps: List[float] = []
    drift = 0.0
    for f in fills:
        if f.filled_qty <= 0 or f.status in ("rejected", "expired"):
            continue
        n_fills += 1
        comps["spread_cost"] += f.spread_cost
        comps["depth_cost"] += f.depth_cost
        comps["impact_cost"] += f.impact_cost
        comps["commission"] += f.commission
        comps["exchange_fee"] += f.exchange_fee
        comps["regulatory_fee"] += f.regulatory_fee
        comps["rebate"] -= f.rebate
        traded_notional += f.notional
        shares += f.filled_qty
        slips.append(f.slippage_bps)
        exec_bps.append(f.execution_cost_bps)
        drift += f.drift_cost
    total = sum(comps.values())
    return {
        "fills": n_fills,
        "shares_traded": shares,
        "traded_notional_usd": round(traded_notional, 2),
        "spread_cost_usd": round(comps["spread_cost"], 2),
        "depth_cost_usd": round(comps["depth_cost"], 2),
        "impact_cost_usd": round(comps["impact_cost"], 2),
        "commission_usd": round(comps["commission"], 2),
        "exchange_fee_usd": round(comps["exchange_fee"], 2),
        "regulatory_fee_usd": round(comps["regulatory_fee"], 2),
        "rebate_usd": round(comps["rebate"], 2),
        "total_cost_usd": round(total, 2),
        "total_cost_bps_of_traded": round(10_000.0 * total / traded_notional, 3)
                                    if traded_notional else 0.0,
        "total_cost_pct_of_starting_cash": round(100.0 * total / starting_cash, 4)
                                           if starting_cash else 0.0,
        # Percentages of a NEGATIVE total are not percentages of anything: when
        # rebates exceed fees the participant was a net liquidity provider and
        # the split is reported as None with the flag set, so no consumer can
        # print "-318% of cost was fees".
        "net_liquidity_provider": total <= 0.0,
        "market_vs_liquidity_split": {
            "explicit_fees_pct": round(100.0 * (comps["commission"] + comps["exchange_fee"]
                                                + comps["regulatory_fee"] + comps["rebate"])
                                       / total, 2) if total > 0 else None,
            "spread_and_impact_pct": round(100.0 * (comps["spread_cost"] + comps["depth_cost"]
                                                    + comps["impact_cost"]) / total, 2)
                                     if total > 0 else None,
        },
        "avg_execution_cost_bps_per_fill": round(_mean(exec_bps), 3) if exec_bps else 0.0,
        "median_execution_cost_bps_per_fill": round(_percentile(sorted(exec_bps), 0.5), 3)
                                              if exec_bps else 0.0,
        "worst_execution_cost_bps": round(max(exec_bps), 3) if exec_bps else 0.0,
        "intraday_drift_usd": round(drift, 2),
        "avg_price_difference_bps_per_fill": round(_mean(slips), 3) if slips else 0.0,
        "note_price_difference": "slippage_bps / price_difference is measured against "
                                 "the decision mid and therefore INCLUDES intraday "
                                 "drift (it can be negative). "
                                 "execution_cost_bps is friction only.",
    }


def implementation_shortfall_report(orders: Sequence, fills: Sequence,
                                    closes_by_date: Dict[str, Dict[str, float]]) -> dict:
    """Perold (1988) decomposition, per participant.

    Paper cost   = the decision price (the opening mid), which is what the
                   strategy's own research would have assumed.
    Real cost    = spread + depth + impact + explicit fees, plus the intraday
                   *drift* of the mid between decision and execution, plus the
                   opportunity cost of quantity that never executed, valued at
                   that session's close.

    The components are mutually exclusive by construction: for every fill,
    ``spread_cost + depth_cost + impact_cost + drift_cost`` equals the total
    signed price difference against the decision mid, so summing them cannot
    double count.  ``drift_cost`` is reported separately because it can be
    negative - a market order that takes all day in a falling market is
    *rewarded* by drift, and hiding that inside "cost" would misrepresent it.
    """
    by_order: Dict[Tuple[str, int], List] = defaultdict(list)
    for f in fills:
        by_order[(f.order.participant, id(f.order))].append(f)
    spread = depth = impact = fees = drift = missed = 0.0
    paper_notional = 0.0
    n_orders = n_partials = n_unfilled = 0
    worst_drift = best_drift = 0.0
    for o in orders:
        fl = by_order.get((o.participant, id(o)), [])
        n_orders += 1
        if not fl:
            n_unfilled += 1
            continue
        filled_qty = sum(f.filled_qty for f in fl)
        if filled_qty == 0:
            n_unfilled += 1
            continue
        if filled_qty < o.quantity:
            n_partials += 1
        decision = fl[0].decision_price
        paper_notional += decision * o.quantity
        spread += sum(f.spread_cost for f in fl)
        depth += sum(f.depth_cost for f in fl)
        impact += sum(f.impact_cost for f in fl)
        fees += sum(f.commission + f.exchange_fee + f.regulatory_fee - f.rebate
                    for f in fl)
        d = sum(f.drift_cost for f in fl)
        drift += d
        worst_drift = max(worst_drift, d)
        best_drift = min(best_drift, d)
        unexecuted = o.quantity - filled_qty
        if unexecuted > 0:
            close_px = closes_by_date.get(fl[0].date, {}).get(o.symbol, decision)
            sign = 1.0 if o.side == BUY else -1.0
            missed += sign * (close_px - decision) * unexecuted
    executed = spread + depth + impact + drift
    total = executed + fees + missed
    return {
        "orders": n_orders,
        "fully_unfilled_orders": n_unfilled,
        "partially_filled_orders": n_partials,
        "paper_notional_usd": round(paper_notional, 2),
        "spread_cost_usd": round(spread, 2),
        "depth_cost_usd": round(depth, 2),
        "impact_cost_usd": round(impact, 2),
        "intraday_drift_usd": round(drift, 2),
        "fees_usd": round(fees, 2),
        "missed_trade_cost_usd": round(missed, 2),
        "total_shortfall_usd": round(total, 2),
        "shortfall_bps_of_paper": round(10_000.0 * total / paper_notional, 3)
                                  if paper_notional else 0.0,
        "friction_only_bps_of_paper": round(10_000.0 * (spread + depth + impact + fees)
                                            / paper_notional, 3) if paper_notional else 0.0,
        "worst_single_order_drift_usd": round(worst_drift, 2),
        "best_single_order_drift_usd": round(best_drift, 2),
        "note": "drift is the mid's own movement between decision and "
                "execution; it is a timing outcome, not a fee, and is "
                "reported separately from the friction components.",
    }


# ==========================================================================
# Market / season report (shared context for every narrative)
# ==========================================================================

def market_report(md, t0: int, t1: int) -> dict:
    """What the market actually did over the competition window."""
    dates = md.dates[t0:t1]
    spx = md.spx[t0:t1]
    curve = list(zip(dates, spx))
    rets = daily_returns(curve)
    vix = md.vix[t0:t1]
    worst = sorted(zip(dates, rets), key=lambda kv: kv[1])[:5]
    best = sorted(zip(dates, rets), key=lambda kv: kv[1], reverse=True)[:5]
    dd = max_drawdown(curve)
    # Three largest distinct drawdown episodes.
    episodes = _drawdown_episodes(curve, top=3)
    # Per-instrument price returns over the window.
    per_symbol = {}
    for s in md.symbols:
        c0 = md.bar(s, t0 - 1).close if t0 > 0 else md.bar(s, t0).open
        c1 = md.bar(s, t1 - 1).close
        per_symbol[s] = round(100.0 * (c1 / c0 - 1.0), 3)
    ranked = sorted(per_symbol.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "window": {"start": dates[0], "end": dates[-1], "sessions": len(dates)},
        "spx_return_pct": round(100.0 * (spx[-1] / spx[0] - 1.0), 3),
        "spx_annualised_vol_pct": round(100.0 * math.sqrt(TRADING_DAYS) * _stdev(rets), 3),
        "spy_price_return_pct": per_symbol.get("SPY"),
        "vix": {"mean": round(_mean(vix), 2), "max": round(max(vix), 2),
                "min": round(min(vix), 2),
                "max_date": dates[vix.index(max(vix))],
                "sessions_above_25": sum(1 for v in vix if v > 25),
                "sessions_above_30": sum(1 for v in vix if v > 30)},
        "max_drawdown": dd,
        "drawdown_episodes": episodes,
        "worst_sessions": [{"date": d, "spx_return_pct": round(100 * r, 3)}
                           for d, r in worst],
        "best_sessions": [{"date": d, "spx_return_pct": round(100 * r, 3)}
                          for d, r in best],
        "up_sessions": sum(1 for r in rets if r > 0),
        "down_sessions": sum(1 for r in rets if r < 0),
        "cross_section": {
            "best": [{"symbol": s, "return_pct": v} for s, v in ranked[:3]],
            "worst": [{"symbol": s, "return_pct": v} for s, v in ranked[-3:]],
            "per_symbol_pct": per_symbol,
            "dispersion_stdev_pp": round(_stdev(list(per_symbol.values())), 2),
            "spread_best_minus_worst_pp": round(ranked[0][1] - ranked[-1][1], 2),
        },
    }


def _drawdown_episodes(curve: Sequence[Tuple[str, float]], top: int = 3) -> List[dict]:
    """Largest *non-overlapping* peak-to-trough-to-recovery episodes.

    An episode runs from a running peak until the series makes a new high.
    Segmenting this way (instead of taking the N deepest points of a rolling
    drawdown series) avoids reporting the same selloff three times.
    """
    episodes: List[dict] = []
    peak_i, trough_i = 0, None
    for i in range(1, len(curve)):
        if curve[i][1] >= curve[peak_i][1]:
            if trough_i is not None:
                episodes.append(_episode(curve, peak_i, trough_i, i, recovered=True))
            peak_i, trough_i = i, None
        elif trough_i is None or curve[i][1] < curve[trough_i][1]:
            trough_i = i
    if trough_i is not None:
        episodes.append(_episode(curve, peak_i, trough_i, len(curve) - 1,
                                 recovered=False))
    episodes.sort(key=lambda e: e["depth_pct"])
    return episodes[:top]


def _episode(curve, peak_i: int, trough_i: int, end_i: int,
             recovered: bool) -> dict:
    peak_val, trough_val = curve[peak_i][1], curve[trough_i][1]
    return {
        "peak_date": curve[peak_i][0],
        "trough_date": curve[trough_i][0],
        "recovery_date": curve[end_i][0] if recovered else None,
        "depth_pct": round(100.0 * (trough_val / peak_val - 1.0), 3),
        "sessions_peak_to_trough": trough_i - peak_i,
        "sessions_trough_to_recovery": (end_i - trough_i) if recovered else None,
        "rebound_pct": round(100.0 * (curve[end_i][1] / trough_val - 1.0), 3)
                       if recovered else None,
    }


def factor_report(md, t0: int, t1: int) -> dict:
    """Realised long/short factor returns over the competition window.

    These are computed from the same replay the participants traded on, so a
    strategy's narrative can be checked against the *actual* premium it was
    built to harvest, rather than against its own prior belief.

    Factors: MOM (12-1 style, 63 sessions back skipping 5, monthly rebalance),
    REV (5-session reversal, weekly), BETA (high vs low declared beta),
    VOL (low vs high realised vol - i.e. the low-vol anomaly),
    LIQ (illiquid vs liquid by dollar volume).
    """
    syms = [s for s in md.symbols if md.instruments[s].tradable_by_strategies]
    rets = {s: [md.bar(s, t).close / md.bar(s, t - 1).close - 1.0
                for t in range(t0, t1)] for s in syms}
    n = t1 - t0

    def _tercile(rank_key: Dict[str, float], long_top: bool) -> Tuple[List[str], List[str]]:
        ordered = sorted(syms, key=lambda s: rank_key.get(s, 0.0), reverse=long_top)
        k = max(1, len(ordered) // 3)
        return ordered[:k], ordered[-k:]

    def _portfolio_daily(long: Sequence[str], short: Sequence[str],
                         weights: Optional[Dict[str, float]] = None) -> List[float]:
        out = []
        for i in range(n):
            leg = 0.0
            for s in long:
                w = (weights or {}).get(s, 1.0 / max(len(long), 1))
                leg += w * rets[s][i]
            for s in short:
                w = (weights or {}).get(s, 1.0 / max(len(short), 1))
                leg -= w * rets[s][i]
            out.append(leg / 2.0)
        return out

    mom = _rolling_factor(md, syms, rets, t0, n, lookback=63, skip=5,
                          rebalance=21, long_top=True)
    rev = _rolling_factor(md, syms, rets, t0, n, lookback=5, skip=0,
                          rebalance=5, long_top=False)
    beta_key = {s: md.instruments[s].beta for s in syms}
    longs, shorts = _tercile(beta_key, True)
    beta_f = _portfolio_daily(longs, shorts)
    vol_key = {s: md.realised_sigma_daily(s, t0 + n - 1, 63) for s in syms}
    longs, shorts = _tercile(vol_key, False)
    vol_f = _portfolio_daily(longs, shorts)
    liq_key = {s: md.adv(s, t0 + n - 1, 63) * md.bar(s, t0 + n - 1).close for s in syms}
    longs, shorts = _tercile(liq_key, False)
    liq_f = _portfolio_daily(longs, shorts)

    def _summary(name: str, series: Sequence[float], description: str) -> dict:
        cum = 1.0
        for r in series:
            cum *= (1.0 + r)
        return {"factor": name, "description": description,
                "cumulative_return_pct": round(100.0 * (cum - 1.0), 3),
                "mean_daily_bps": round(10_000.0 * _mean(series), 2),
                "annualised_vol_pct": round(100.0 * math.sqrt(TRADING_DAYS)
                                            * _stdev(series), 2),
                "sharpe": round(math.sqrt(TRADING_DAYS) * _mean(series) / _stdev(series), 3)
                          if _stdev(series) else 0.0}

    mkt = [md.spx[t0 + i] / md.spx[t0 + i - 1] - 1.0 for i in range(n)]
    return {
        "market": _summary("MKT", mkt, "S&P 500 index (real FRED SP500 series)"),
        "momentum": _summary("MOM", mom, "long top tercile / short bottom tercile by "
                                         "63-session return skipping the last 5, "
                                         "rebalanced monthly"),
        "reversal": _summary("REV", rev, "short top tercile / long bottom tercile by "
                                         "5-session return, rebalanced weekly"),
        "beta": _summary("BETA", beta_f, "long high-declared-beta tercile, short low"),
        "low_vol": _summary("VOL", vol_f, "long low realised-vol tercile, short high "
                                          "(the low-volatility anomaly)"),
        "liquidity": _summary("LIQ", liq_f, "long illiquid tercile by dollar volume, "
                                            "short liquid"),
    }


def _rolling_factor(md, syms: Sequence[str], rets: Dict[str, List[float]], t0: int,
                    n: int, lookback: int, skip: int, rebalance: int,
                    long_top: bool) -> List[float]:
    """Long/short tercile factor return series with periodic rebalancing."""
    longs: List[str] = []
    shorts: List[str] = []
    out: List[float] = []
    for i in range(n):
        if i % rebalance == 0 or not longs:
            t = t0 + i
            key: Dict[str, float] = {}
            for s in syms:
                closes = md.history_closes(s, t, lookback + skip + 1)
                if len(closes) >= lookback + skip + 1:
                    num = closes[-1 - skip] if skip else closes[-1]
                    key[s] = num / closes[0] - 1.0
            if key:
                ordered = sorted(key, key=lambda s: key[s], reverse=long_top)
                k = max(1, len(ordered) // 3)
                longs, shorts = ordered[:k], ordered[-k:]
        leg = 0.0
        for s in longs:
            leg += rets[s][i] / max(len(longs), 1)
        for s in shorts:
            leg -= rets[s][i] / max(len(shorts), 1)
        out.append(leg / 2.0)
    return out


# ==========================================================================
# Per-participant performance report
# ==========================================================================

def performance_report(participant: dict, md, t0: int, t1: int,
                       starting_cash: float, market: dict,
                       factors: dict) -> dict:
    """Full metric bundle for one participant.

    ``participant`` is the engine's per-participant record: keys ``username``,
    ``account_summary``, ``equity_curve``, ``fills``, ``orders``, ``marks``
    (list of daily mark rows), ``position_rows``.
    """
    curve = [(d, e) for d, e in participant["equity_curve"]]
    rets = daily_returns(curve)
    # Alignment: the equity curve holds one mark per session t0..t1-1, so its
    # return series covers sessions t0+1..t1-1.  The market returns must be
    # built over exactly the same sessions, otherwise the regression is shifted
    # by one day and beta comes out near zero no matter what the book holds.
    mkt_rets = [md.spx[t] / md.spx[t - 1] - 1.0 for t in range(t0 + 1, t1)]
    if len(mkt_rets) != len(rets):
        n = min(len(mkt_rets), len(rets))
        mkt_rets, rets_aligned = mkt_rets[-n:], rets[-n:]
    else:
        rets_aligned = rets
    reg = _ols(rets_aligned, mkt_rets)
    dd = max_drawdown(curve)
    acct = participant["account_summary"]
    date_index = {d: i for i, d in enumerate(md.dates)}
    trips = holding_days(round_trips(participant["fills"]), date_index)
    tstats = trade_statistics(trips)
    exposure = market_exposure_sessions(trips, date_index)
    tstats.update(exposure)
    tstats["holding_period_note"] = (
        "sessions_held is measured per closed tranche of an average-cost lot, "
        "so tranches of the same lot share its entry date and their intervals "
        "overlap; they must not be summed. sessions_in_market is the union."
        if exposure["tranche_intervals_overlap"] else
        "sessions_held is measured per closed tranche of an average-cost lot; "
        "sessions_in_market is the union of those intervals."
    )
    costs = cost_report(participant["fills"], starting_cash)
    marks_last = participant.get("final_marks", {})
    contrib = symbol_contribution(trips, marks_last,
                                  participant.get("final_positions"))
    exposure = participant.get("marks", [])
    gross = [r["gross_exposure"] / starting_cash for r in exposure]
    net = [r["net_exposure"] / starting_cash for r in exposure]
    lev = [r["leverage"] for r in exposure]
    downside = [r for r in rets if r < 0]
    eq_final = curve[-1][1] if curve else starting_cash
    years = len(curve) / TRADING_DAYS
    cagr = (eq_final / starting_cash) ** (1.0 / years) - 1.0 if years > 0 and eq_final > 0 else -1.0
    traded_notional = costs["traded_notional_usd"]
    avg_equity = _mean([e for _, e in curve]) or starting_cash
    sharpe = math.sqrt(TRADING_DAYS) * _mean(rets) / _stdev(rets) if _stdev(rets) else 0.0
    sortino = math.sqrt(TRADING_DAYS) * _mean(rets) / _stdev(downside) if downside and _stdev(downside) else 0.0
    report = {
        "username": participant["username"],
        "strategy_id": participant["strategy_id"],
        "archetype": participant["archetype"],
        "starting_cash": starting_cash,
        "final_equity": round(eq_final, 2),
        "net_pnl_usd": round(eq_final - starting_cash, 2),
        "total_return_pct": round(100.0 * (eq_final / starting_cash - 1.0), 4),
        "cagr_pct": round(100.0 * cagr, 4),
        "sessions": len(curve),
        "risk": {
            **dd,
            "sharpe": round(sharpe, 4),
            "sortino": round(sortino, 4),
            "calmar": round(abs(100.0 * cagr / dd["max_drawdown_pct"]), 4)
                      if dd["max_drawdown_pct"] else None,
            "daily_vol_pct": round(100.0 * _stdev(rets), 4),
            "annualised_vol_pct": round(100.0 * math.sqrt(TRADING_DAYS) * _stdev(rets), 3),
            "skew": round(_skew(rets), 4),
            "excess_kurtosis": round(_kurtosis(rets), 4),
            "best_session_pct": round(100.0 * max(rets), 4) if rets else 0.0,
            "worst_session_pct": round(100.0 * min(rets), 4) if rets else 0.0,
            **var_cvar(rets),
        },
        "market_relation": {
            "beta": round(reg["beta"], 4),
            "alpha_annual_pct": round(100.0 * reg["alpha_annual"], 4),
            "r_squared": round(reg["r_squared"], 4),
            "tracking_error_annual_pct": round(100.0 * math.sqrt(TRADING_DAYS)
                                               * reg["residual_std"], 3),
            "information_ratio": round(math.sqrt(TRADING_DAYS) * reg["alpha_daily"]
                                       / reg["residual_std"], 3)
                                 if reg["residual_std"] else None,
            "market_return_pct": market["spx_return_pct"],
        },
        "trades": tstats,
        "costs": costs,
        "implementation_shortfall": implementation_shortfall_report(
            participant["orders"], participant["fills"],
            participant["closes_by_date"]),
        "carry": {
            "dividends_received_usd": acct.get("dividends_received", 0.0),
            "dividends_in_lieu_paid_usd": acct.get("dividends_in_lieu_paid", 0.0),
            "borrow_fees_paid_usd": acct.get("borrow_fees_paid", 0.0),
            "net_carry_usd": round(acct.get("dividends_received", 0.0)
                                   - acct.get("dividends_in_lieu_paid", 0.0)
                                   - acct.get("borrow_fees_paid", 0.0), 2),
            "margin_calls": acct.get("margin_calls", []),
            "margin_call_count": len(acct.get("margin_calls", [])),
            "day_trades": acct.get("day_trades", 0),
            "day_trade_count": acct.get("day_trade_count", 0),
        },
        "exposure": {
            "avg_gross_pct_of_starting_cash": round(100.0 * _mean(gross), 2) if gross else 0.0,
            "max_gross_pct_of_starting_cash": round(100.0 * max(gross), 2) if gross else 0.0,
            "avg_net_pct_of_starting_cash": round(100.0 * _mean(net), 2) if net else 0.0,
            "max_net_pct_of_starting_cash": round(100.0 * max(net), 2) if net else 0.0,
            "avg_leverage": round(_mean(lev), 3) if lev else 0.0,
            "max_leverage": round(max(lev), 3) if lev else 0.0,
            "sessions_flat": sum(1 for g in gross if g < 0.001),
            "avg_open_positions": round(_mean([r["open_positions"] for r in exposure]), 2)
                                      if exposure else 0.0,
        },
        "turnover": {
            "traded_notional_usd": round(traded_notional, 2),
            "annualised_turnover_x": round(traded_notional / avg_equity / years, 3)
                                     if years > 0 and avg_equity else 0.0,
        },
        "contribution_by_symbol": contrib,
        "monthly_returns": monthly_returns(curve),
        "pnl_decomposition": _decompose(acct, eq_final, starting_cash, costs, contrib),
    }
    report["market_relation"]["beta_contribution_pct"] = round(
        reg["beta"] * market["spx_return_pct"], 3)
    report["market_relation"]["residual_return_pct"] = round(
        report["total_return_pct"] - reg["beta"] * market["spx_return_pct"], 3)
    report["narrative"] = build_narrative(report, participant["spec"], market, factors)
    return report


# Buckets that must sum (with the residual) to the total net P&L.  Declared
# here, next to the code that builds them, and imported by the ledger-closure
# test, so a bucket added later cannot quietly escape the identity.
DECOMPOSITION_REALIZED = "realized_trading_pnl_usd"
DECOMPOSITION_OPEN = "open_position_pnl_usd"
DECOMPOSITION_DIVIDENDS = "dividends_usd"
DECOMPOSITION_IN_LIEU = "dividends_in_lieu_usd"
DECOMPOSITION_BORROW = "borrow_fees_usd"
DECOMPOSITION_BUCKETS = (DECOMPOSITION_REALIZED, DECOMPOSITION_OPEN,
                         DECOMPOSITION_DIVIDENDS, DECOMPOSITION_IN_LIEU,
                         DECOMPOSITION_BORROW)


def _decompose(acct: dict, eq_final: float, starting_cash: float,
               costs: dict, contrib: List[dict]) -> dict:
    """Additive decomposition of the final P&L into named buckets."""
    realized = sum(c["realized_pnl"] for c in contrib)
    open_pnl = sum(c["open_pnl"] for c in contrib)
    divs = acct.get("dividends_received", 0.0)
    in_lieu = acct.get("dividends_in_lieu_paid", 0.0)
    borrow = acct.get("borrow_fees_paid", 0.0)
    fee_total = costs["total_cost_usd"]
    total = eq_final - starting_cash
    explained = realized + open_pnl + divs - in_lieu - borrow
    return {
        DECOMPOSITION_REALIZED: round(realized, 2),
        DECOMPOSITION_OPEN: round(open_pnl, 2),
        DECOMPOSITION_DIVIDENDS: round(divs, 2),
        DECOMPOSITION_IN_LIEU: round(-in_lieu, 2),
        DECOMPOSITION_BORROW: round(-borrow, 2),
        "execution_costs_already_netted_usd": round(-fee_total, 2),
        "total_net_pnl_usd": round(total, 2),
        "unexplained_residual_usd": round(total - explained, 2),
        "note": "Realized and open P&L are net of explicit cash costs "
                "(commission, exchange and regulatory fees less rebates), which "
                "are the only costs charged to cash separately; spread, depth "
                "and impact are already inside the fill prices. The residual is "
                "the rounding gap and is reported rather than hidden.",
    }


# ==========================================================================
# Rule-based post-mortem narrative
# ==========================================================================

def build_narrative(report: dict, spec: dict, market: dict, factors: dict) -> dict:
    """Generate the "why it worked / why it did not" write-up.

    The text is assembled from a fixed library of conditional clauses, each of
    which fires only on a *measured* quantity.  No clause can fire on a
    strategy's declared priors alone, so the narrative cannot flatter a losing
    strategy or blame a winner.
    """
    ret = report["total_return_pct"]
    mkt = market["spx_return_pct"]
    risk = report["risk"]
    dd = risk["max_drawdown_pct"]
    beta = report["market_relation"]["beta"]
    costs = report["costs"]
    trades = report["trades"]
    contrib = report["contribution_by_symbol"]
    factors_of_interest = _relevant_factors(spec, factors)
    clauses: List[dict] = []

    # 1. Headline verdict.
    if ret > mkt + 5:
        verdict = "beat the market"
        clauses.append(_c("verdict", f"Finished at {ret:+.2f}% against the S&P 500's "
                                     f"{mkt:+.2f}% over the same {report['sessions']} "
                                     f"sessions - an excess return of {ret - mkt:+.2f}pp."))
    elif ret > 0 and ret >= mkt - 5:
        verdict = "roughly matched the market"
        clauses.append(_c("verdict", f"Finished at {ret:+.2f}% versus the S&P 500's "
                                     f"{mkt:+.2f}% - effectively in line with simply "
                                     f"holding the index."))
    elif ret > 0:
        verdict = "made money but lagged the index"
        clauses.append(_c("verdict", f"Finished at {ret:+.2f}%, profitable but "
                                     f"{mkt - ret:.2f}pp behind the S&P 500's {mkt:+.2f}%."))
    else:
        verdict = "lost money"
        clauses.append(_c("verdict", f"Finished at {ret:+.2f}% - a "
                                     f"{abs(report['net_pnl_usd']):,.0f} dollar loss on "
                                     f"100,000 of starting capital, in a market that "
                                     f"returned {mkt:+.2f}%."))

    # 2. Was the declared edge actually present in this window?
    for name, f in factors_of_interest:
        sign_expected = spec.get("factor_exposure", {}).get(name, 0)
        realised = f["cumulative_return_pct"]
        helped = (sign_expected > 0 and realised > 0) or \
                 (sign_expected < 0 and realised < 0)
        if helped and ret > 0:
            clauses.append(_c("factor", f"The {name} premium this participant is built to "
                                        f"harvest was live ({realised:+.2f}% over the "
                                        f"season) and the account made money - a tailwind "
                                        f"that cannot be separated from skill on one path."))
        elif helped and ret <= 0:
            clauses.append(_c("factor", f"The {name} premium it is built to harvest was "
                                        f"live ({realised:+.2f}%) and the account STILL "
                                        f"lost {abs(ret):.2f}% - so the loss came from "
                                        f"implementation, timing or leverage, not from a "
                                        f"missing signal."))
        elif not helped and ret <= 0:
            clauses.append(_c("factor", f"The {name} premium it is built to harvest went "
                                        f"the wrong way ({realised:+.2f}% over the "
                                        f"season), so the signal was fighting the tape "
                                        f"and the account lost {abs(ret):.2f}%."))
        else:
            clauses.append(_c("factor", f"The {name} premium went against the declared "
                                        f"exposure ({realised:+.2f}% for a "
                                        f"{sign_expected:+.1f} loading) yet the account "
                                        f"still returned {ret:+.2f}% - the result was "
                                        f"driven by something other than this factor."))

    # 3. Beta vs residual.
    beta_c = beta * mkt
    resid = ret - beta_c
    clauses.append(_c("attribution",
                      f"Of the {ret:+.2f}% total, about {beta_c:+.2f}pp is explained by "
                      f"carrying beta {beta:.2f} against a {mkt:+.2f}% market "
                      f"(R^2 {report['market_relation']['r_squared']:.2f}); the residual "
                      f"{resid:+.2f}pp is everything else - selection, timing, leverage "
                      f"and cost."))

    # 4. Concentration outcome.
    if contrib:
        top = contrib[0]
        bottom = contrib[-1]
        share = 100.0 * top["total_pnl"] / report["net_pnl_usd"] \
            if report["net_pnl_usd"] else 0.0
        clauses.append(_c("concentration",
                          f"{top['symbol']} contributed {top['total_pnl']:+,.0f} USD"
                          f"{' (' + format(share, '.0f') + '% of net P&L)' if abs(share) > 25 else ''}"
                          f" across {top['closed_trades']} closed trades; "
                          f"{bottom['symbol']} was the biggest drag at "
                          f"{bottom['total_pnl']:+,.0f} USD."))

    # 5. Costs.
    drag = costs["total_cost_pct_of_starting_cash"]
    if costs["total_cost_usd"] <= 0:
        clauses.append(_c("costs",
                          f"This participant was a NET LIQUIDITY PROVIDER: rebates "
                          f"({costs['rebate_usd']:,.0f} USD) exceeded the fees it paid, so "
                          f"all-in execution cost was {costs['total_cost_usd']:,.0f} USD "
                          f"({drag:+.2f}% of starting capital) on "
                          f"{costs['traded_notional_usd']:,.0f} USD traded. Gross friction "
                          f"was still real: {costs['spread_cost_usd']:,.0f} USD of spread, "
                          f"{costs['impact_cost_usd']:,.0f} USD of impact and "
                          f"{costs['exchange_fee_usd'] + costs['regulatory_fee_usd']:,.0f} "
                          f"USD of fees - it simply earned "
                          f"{-costs['total_cost_usd']:,.0f} USD back for resting orders."))
    else:
        clauses.append(_c("costs",
                          f"Execution cost {costs['total_cost_usd']:,.0f} USD "
                          f"({drag:.2f}% of starting capital, "
                          f"{costs['avg_execution_cost_bps_per_fill']:.1f} bp per fill, "
                          f"{costs['total_cost_bps_of_traded']:.1f} bp of the "
                          f"{costs['traded_notional_usd']:,.0f} USD traded). "
                          f"{costs['market_vs_liquidity_split']['spread_and_impact_pct']:.0f}% "
                          f"of that was spread/depth/impact and "
                          f"{costs['market_vs_liquidity_split']['explicit_fees_pct']:.0f}% was "
                          f"explicit fees and rebates."))
    if drag > 5 and ret < 0:
        clauses.append(_c("costs", "Costs exceeded 5% of starting capital and the account "
                                   "still lost money: the turnover was not paid for by the "
                                   "edge."))

    # 6. Trade quality.
    if trades["closed_trades"]:
        pf = trades["profit_factor"]
        pf_txt = "no losing trades" if pf is None else f"{pf:.2f}"
        clauses.append(_c("trades",
                          f"{trades['closed_trades']} closed round trips, "
                          f"{trades['win_rate_pct']:.1f}% winners, profit factor "
                          f"{pf_txt}, average winner "
                          f"{trades['avg_win_usd']:+,.0f} vs average loser "
                          f"{trades['avg_loss_usd']:+,.0f} USD, median holding "
                          f"{trades['median_sessions_held']:.0f} sessions."))
        if trades.get("sessions_in_market") is not None:
            overlap = trades.get("tranche_intervals_overlap")
            clauses.append(_c("trades",
                              f"Time in market, measured without double counting: "
                              f"{trades['sessions_in_market']} sessions held at least "
                              f"one position"
                              + (f" (the per-tranche holding periods above sum to "
                                 f"{trades['sum_of_tranche_sessions']} sessions because "
                                 f"tranches of the same average-cost lot share its entry "
                                 f"date, so they overlap and must not be added together)"
                                 if overlap else "") + "."))
        if trades["worst_trade"]:
            wt = trades["worst_trade"]
            clauses.append(_c("trades",
                              f"Single worst trade: {wt['symbol']} "
                              f"{wt['direction']} {wt['entry_date']} to {wt['exit_date']}, "
                              f"{wt['net_pnl']:+,.0f} USD."))

    # 7. Drawdown and leverage (stated as fact, not as risk management).
    clauses.append(_c("drawdown",
                      f"Worst peak-to-trough drawdown {dd:.2f}% "
                      f"({market['max_drawdown']['max_drawdown_pct']:.2f}% for the index), "
                      f"trough on {risk.get('trough_date')}, "
                      f"{'never recovered' if risk.get('never_recovered') else 'recovered ' + str(risk.get('recovery_date'))}. "
                      f"Average gross exposure "
                      f"{report['exposure']['avg_gross_pct_of_starting_cash']:.0f}% of "
                      f"starting capital, peak leverage "
                      f"{report['exposure']['max_leverage']:.2f}x."))
    if report["carry"]["margin_call_count"]:
        clauses.append(_c("drawdown",
                          f"{report['carry']['margin_call_count']} maintenance-margin "
                          f"breach(es) triggered forced liquidation: "
                          f"{report['carry']['margin_calls'][0]}"))

    # 8. Tail behaviour.
    r = report["risk"]
    clauses.append(_c("tail",
                      f"Daily returns were {r['skew']:+.2f} skewed with excess kurtosis "
                      f"{r['excess_kurtosis']:+.2f}; historical 95% VaR "
                      f"{r['var_95_pct']:.2f}%/day with CVaR {r['cvar_95_pct']:.2f}%/day. "
                      f"Worst single session {r['worst_session_pct']:.2f}%, best "
                      f"{r['best_session_pct']:+.2f}%."))

    # 9a. Standing structural caveats (not regime-dependent).
    structural = _structural_caveats(spec)
    if structural:
        clauses.append(_c("caveat",
                          "Standing limitations of this simulation that apply to this "
                          "participant regardless of the regime: " + " ".join(structural)))
    clauses.append(_c("caveat",
                      "The style premia quoted above are measured on the simulated "
                      "replay, whose cross-sectional drifts are DECLARED scenario "
                      "parameters (see sim/universe.py). They describe this "
                      "competition, not the real market: IR-16."))

    # 9b. Did any declared failure mode fire?
    fired, untested = _failure_modes_fired(spec, report, market)
    if fired:
        clauses.append(_c("failure_mode",
                          "Declared failure mode(s) that actually fired: " +
                          "; ".join(fired) + "."))
    else:
        clauses.append(_c("failure_mode",
                          "None of the failure modes declared in the strategy spec fired "
                          "in this window."))
    if untested:
        clauses.append(_c("failure_mode_untested",
                          "Declared failure mode(s) this run CANNOT test, because no "
                          "measurable condition matches their wording (they are neither "
                          "confirmed nor ruled out): " + "; ".join(untested) + "."))

    # 10. Regime context from the real index.
    ep = market["drawdown_episodes"]
    if ep:
        e = ep[0]
        clauses.append(_c("regime",
                          f"Season context: the real S&P 500 fell {abs(e['depth_pct']):.2f}% "
                          f"from {e['peak_date']} to {e['trough_date']}, VIX peaked at "
                          f"{market['vix']['max']:.2f} on {market['vix']['max_date']}, and "
                          f"cross-sectional dispersion was "
                          f"{market['cross_section']['dispersion_stdev_pp']:.1f}pp (stdev of "
                          f"individual 1-year returns)."))

    summary = _summarise(verdict, ret, mkt, resid, dd, drag)
    return {
        "verdict": verdict,
        "headline": summary,
        "clauses": clauses,
        "prose": _prose(report, spec, verdict, clauses),
        "failure_modes_fired": fired,
        "failure_modes_untested": untested,
        "structural_caveats": structural,
        "factors_considered": [n for n, _ in factors_of_interest],
    }


def _prose(report: dict, spec: dict, verdict: str, clauses: List[dict]) -> List[dict]:
    """Assemble the readable post-mortem from the measured clauses.

    Returns a list of titled paragraphs.  The mandate paragraph quotes the
    strategy's own declared spec; every other paragraph is built exclusively
    from clauses that fired on realised numbers.
    """
    by_kind: Dict[str, List[str]] = defaultdict(list)
    for c in clauses:
        by_kind[c["kind"]].append(c["text"])

    def para(title: str, kinds: Sequence[str], fallback: str = "") -> dict:
        texts = [t for k in kinds for t in by_kind.get(k, [])]
        return {"title": title, "text": " ".join(texts) if texts else fallback}

    username = report["username"]
    mandate = (
        f"{username} ({spec.get('display_name', username)}) ran the "
        f"\"{spec.get('archetype', 'unclassified')}\" archetype. Declared thesis: "
        f"{spec.get('thesis', 'not stated').strip()} "
        f"Entry: {'; '.join(spec.get('entry_rules', []) or ['not stated'])}. "
        f"Exit: {'; '.join(spec.get('exit_rules', []) or ['not stated'])}. "
        f"Sizing: {spec.get('sizing', 'not stated')}. Leverage: "
        f"{spec.get('leverage', 'not stated')}. Cadence: {spec.get('cadence', 'daily')}, "
        f"horizon {spec.get('horizon', 'unspecified')}. Aggression "
        f"{spec.get('aggression', 3)}/5. This participant was built to maximise "
        f"return, not to manage risk: {spec.get('why_return_seeking', '').strip()}"
    )
    outcome = (
        f"Outcome: {report['total_return_pct']:+.2f}% over "
        f"{report['sessions']} sessions - "
        f"{report['net_pnl_usd']:+,.0f} USD on "
        f"{report['starting_cash']:,.0f} of paper capital, ending at "
        f"{report['final_equity']:,.0f}. Verdict: {verdict}."
    )
    paragraphs = [
        {"title": "Mandate (as declared before the season)", "text": mandate},
        {"title": "Outcome (as measured after the season)", "text": outcome},
        para("Why: the style premia that were actually live",
             ("factor",), "No declared factor loading could be tested against a "
                          "realised premium for this archetype."),
        para("Why: attribution of the return", ("attribution", "concentration")),
        para("Why: what execution cost", ("costs",)),
        para("Why: trade-level behaviour", ("trades",)),
        para("What the return-maximising mandate cost in drawdown",
             ("drawdown", "tail")),
        para("Regime context (real market data)", ("regime",)),
        para("Declared failure modes that fired", ("failure_mode",)),
        para("Standing caveats: what this simulation cannot represent",
             ("caveat",), "No structural caveats were declared for this archetype."),
    ]
    # Explicit answer to the required question.
    ret = report["total_return_pct"]
    mkt = report["market_relation"]["market_return_pct"]
    resid = report["market_relation"]["residual_return_pct"]
    beta_v = report["market_relation"]["beta"]
    if ret > mkt and resid > 0:
        answer = (f"It worked: it beat the index by {ret - mkt:.2f}pp and the excess "
                  f"survives a beta adjustment ({resid:+.2f}pp residual), so the "
                  f"outperformance is not simply leverage on a rising market.")
    elif ret > mkt and resid <= 0:
        answer = (f"It beat the index by {ret - mkt:.2f}pp, but after beta the "
                  f"residual is {resid:+.2f}pp: the win came from carrying more "
                  f"market risk (beta {beta_v:.2f}) in an up market, not from the "
                  f"signal itself.")
    elif ret > 0:
        answer = (f"It made money ({ret:+.2f}%) but under-delivered versus simply "
                  f"holding the index ({mkt:+.2f}%); the signal did not pay for "
                  f"its own costs, drawdown and turnover.")
    elif resid > 0:
        answer = (f"It lost {abs(ret):.2f}%, but the loss is a DIRECTIONAL one, not a "
                  f"trading one: after removing beta {beta_v:.2f} against a "
                  f"{mkt:+.2f}% market the residual is {resid:+.2f}pp, meaning the "
                  f"trades themselves added value while the position the account "
                  f"ended up carrying lost more.")
    else:
        answer = (f"It did not work: {ret:+.2f}% in a market that returned "
                  f"{mkt:+.2f}%, and a residual of {resid:+.2f}pp after beta - the "
                  f"trading itself destroyed value, not just the market direction.")
    paragraphs.append({"title": "Answer: did it work, and why",
                       "text": answer + " " + " ".join(by_kind.get("verdict", []))})
    return paragraphs


_STRUCTURAL_TAGS = ("GRANULARITY", "PROXY DATA", "TIMING LIMIT", "DAILY DECISION",
                    "DAILY-BAR", "NOT AVAILABLE OFFLINE", "IR-07", "IR-08")


def _structural_caveats(spec: dict) -> List[str]:
    """Failure modes that are properties of the SIMULATION, not of the market.

    These must be surfaced in every narrative, fired or not, because they bound
    how far the result can be interpreted.  A strategy that loses because the
    engine decides once a day has not been shown to lose in reality.
    """
    out = []
    for mode in spec.get("known_failure_modes", []) or []:
        text = mode if isinstance(mode, str) else str(mode)
        if any(tag in text.upper() for tag in _STRUCTURAL_TAGS):
            out.append(text.strip())
    return out


def _c(kind: str, text: str) -> dict:
    return {"kind": kind, "text": text}


def _summarise(verdict: str, ret: float, mkt: float, resid: float,
               dd: float, drag: float) -> str:
    return (f"{verdict.replace('-', ' ').capitalize()}: {ret:+.2f}% total return vs "
            f"{mkt:+.2f}% for the index, {resid:+.2f}pp residual after beta, "
            f"{dd:.1f}% max drawdown, {drag:.2f}% of capital lost to execution costs.")


def _relevant_factors(spec: dict, factors: dict) -> List[Tuple[str, dict]]:
    """Map the strategy's declared factor exposures onto realised factor returns."""
    mapping = spec.get("factor_exposure", {}) or {}
    key_map = {"momentum": "momentum", "reversal": "reversal", "beta": "beta",
               "low_vol": "low_vol", "liquidity": "liquidity", "market": "market"}
    out = []
    for name, exposure in mapping.items():
        key = key_map.get(name)
        if key and key in factors and exposure:
            out.append((name, factors[key]))
    return out


def _failure_modes_fired(spec: dict, report: dict,
                         market: dict) -> Tuple[List[str], List[str]]:
    """Test each declared failure mode against realised numbers.

    Every test is a measurable condition on this run's own output.  Returns
    ``(fired, untested)``: a mode whose wording matches no measurable condition
    is reported as untested rather than being folded into the fired list,
    because "this risk happened" and "we could not measure this risk" are
    different statements and the post-mortem must not blur them.
    """
    fired: List[str] = []
    untested: List[str] = []
    structural = set(_structural_caveats(spec))
    # Structural caveats are reported in their own paragraph; testing them
    # here would just duplicate them under "fired".
    modes = [m for m in (spec.get("known_failure_modes", []) or [])
             if (m if isinstance(m, str) else str(m)).strip() not in structural]
    costs = report["costs"]
    trades = report["trades"]
    pf = trades.get("profit_factor")
    mkt_ret = market["spx_return_pct"]
    ret = report["total_return_pct"]
    borrow = abs(report["carry"]["borrow_fees_paid_usd"])
    checks = {
        "cost": lambda: costs["total_cost_pct_of_starting_cash"] > 3.0,
        "turnover": lambda: report["turnover"]["annualised_turnover_x"] > 20.0,
        "churn": lambda: report["turnover"]["annualised_turnover_x"] > 20.0,
        "drawdown": lambda: report["risk"]["max_drawdown_pct"] < -25.0,
        "whipsaw": lambda: (trades["win_rate_pct"] < 40.0 and
                            trades["closed_trades"] > 10),
        "adverse": lambda: (pf is not None and pf < 1.0 and trades["win_rate_pct"] > 50.0),
        "leverage": lambda: report["exposure"]["max_leverage"] > 1.75,
        "margin": lambda: report["carry"]["margin_call_count"] > 0,
        "liquid": lambda: costs["avg_execution_cost_bps_per_fill"] > 5.0,
        "illiquid": lambda: costs["avg_execution_cost_bps_per_fill"] > 5.0,
        "regime": lambda: market["vix"]["max"] > 28.0,
        "dispersion": lambda: market["cross_section"]["dispersion_stdev_pp"] > 30.0,
        "momentum crash": lambda: factors_cache.get("momentum", {}).get(
            "cumulative_return_pct", 0.0) < 0.0,
        "squeeze": lambda: (trades.get("worst_trade") or {}).get("direction") == "short"
                           and (trades.get("worst_trade") or {}).get("net_pnl", 0) < -5_000,
        "short leg": lambda: (trades.get("worst_trade") or {}).get("direction") == "short"
                             and (trades.get("worst_trade") or {}).get("net_pnl", 0) < -5_000,
        "melt-up": lambda: mkt_ret > 8.0 and ret < 0.0,
        "gap": lambda: report["risk"]["worst_session_pct"] < -4.0,
        "flat": lambda: report["exposure"]["sessions_flat"] > 20,
        "concentrat": lambda: bool(report["contribution_by_symbol"]) and
                            abs(report["contribution_by_symbol"][0]["total_pnl"]) >
                            0.5 * max(abs(report["net_pnl_usd"]), 1.0),
        "inventory": lambda: abs(report["market_relation"]["beta"]) > 1.5 or
                            report["exposure"]["max_gross_pct_of_starting_cash"] > 150.0,
        "stale": lambda: (trades.get("median_sessions_held") or 0) > 10,
        "decay": lambda: report["turnover"]["annualised_turnover_x"] > 5.0 and ret < mkt_ret,
        "bleed": lambda: costs["total_cost_pct_of_starting_cash"] > 1.0 and ret < mkt_ret,
        "borrow": lambda: borrow > 0.01 * report["starting_cash"],
        "trend reversal": lambda: report["risk"]["max_drawdown_pct"] < -25.0,
        "reversal": lambda: report["risk"]["max_drawdown_pct"] < -25.0,
        "anomaly is weak": lambda: abs(ret - mkt_ret) < 10.0,
    }
    for mode in modes:
        text = mode if isinstance(mode, str) else str(mode)
        low = text.lower()
        matched = False
        for tag, fn in checks.items():
            if tag in low:
                matched = True
                try:
                    if fn():
                        fired.append(text.strip())
                        break
                except Exception:
                    continue
        if not matched:
            untested.append(text.strip())
    return fired, untested


# Module-level cache so failure-mode checks can see the realised factor panel.
factors_cache: Dict[str, dict] = {}


def set_factor_cache(factors: dict) -> None:
    factors_cache.clear()
    factors_cache.update(factors)


# ==========================================================================
# Cross-participant league table
# ==========================================================================

def leaderboard(reports: Sequence[dict], rank_metric: str = "total_return_pct") -> List[dict]:
    rows = []
    for i, r in enumerate(sorted(reports, key=lambda r: -r[rank_metric])):
        rows.append({
            "rank": i + 1,
            "username": r["username"],
            "strategy_id": r["strategy_id"],
            "archetype": r["archetype"],
            "total_return_pct": r["total_return_pct"],
            "net_pnl_usd": r["net_pnl_usd"],
            "final_equity": r["final_equity"],
            "max_drawdown_pct": r["risk"]["max_drawdown_pct"],
            "sharpe": r["risk"]["sharpe"],
            "sortino": r["risk"]["sortino"],
            "beta": r["market_relation"]["beta"],
            "alpha_annual_pct": r["market_relation"]["alpha_annual_pct"],
            "closed_trades": r["trades"]["closed_trades"],
            "win_rate_pct": r["trades"]["win_rate_pct"],
            "profit_factor": r["trades"]["profit_factor"],
            "execution_cost_pct": r["costs"]["total_cost_pct_of_starting_cash"],
            "turnover_x": r["turnover"]["annualised_turnover_x"],
            "margin_calls": r["carry"]["margin_call_count"],
            "verdict": r["narrative"]["verdict"],
        })
    return rows


def robustness_panel(scenario_reports: Sequence[dict]) -> dict:
    """Across seeds: how much of a result is edge and how much is the draw.

    Each scenario is one independent realisation of the same declared priors on
    the same *real* market factor path (the FRED S&P 500 and VIX series are
    identical in every scenario; only the idiosyncratic draws and the venue
    microstructure noise change).  Comparing a participant's spread of outcomes
    across scenarios is the only honest way to separate signal from luck with
    a single season of data.
    """
    by_user: Dict[str, List[Tuple[int, float]]] = defaultdict(list)
    for si, sc in enumerate(scenario_reports):
        for r in sc["reports"]:
            by_user[r["username"]].append((si, r["total_return_pct"]))
    index_by_scenario = [sc.get("market", {}).get("spx_return_pct", 0.0)
                         for sc in scenario_reports]
    rows = []
    for user, pairs in by_user.items():
        vals = [v for _, v in pairs]
        s = sorted(vals)
        rows.append({
            "username": user,
            "scenarios": len(vals),
            "mean_return_pct": round(_mean(vals), 3),
            "median_return_pct": round(_percentile(s, 0.5), 3),
            "best_return_pct": round(max(vals), 3),
            "worst_return_pct": round(min(vals), 3),
            "stdev_pp": round(_stdev(vals), 3),
            "positive_scenarios": sum(1 for v in vals if v > 0),
            "beat_index_scenarios": sum(1 for si, v in pairs
                                        if v > index_by_scenario[si]),
            "returns_by_scenario_pct": {str(scenario_reports[si].get("seed", si)):
                                        round(v, 3) for si, v in pairs},
            "index_by_scenario_pct": {str(scenario_reports[si].get("seed", si)):
                                      index_by_scenario[si]
                                      for si, _ in pairs},
        })
    rows.sort(key=lambda r: -r["mean_return_pct"])
    return {"by_participant": rows,
            "note": "One calendar path is one draw from the idiosyncratic "
                    "distribution.  A strategy that wins on the primary seed "
                    "and loses on three others has a result that is not "
                    "distinguishable from luck."}


