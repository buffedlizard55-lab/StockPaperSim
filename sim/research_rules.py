"""Small deterministic research-rule prototypes; never an execution authority.

Input prices must pass the separate official-price/corporate-action gate before
use in a competition. This module validates arithmetic and point-in-time input
order, but does not certify a caller's prices as official. No model is fitted.
"""
from __future__ import annotations

from decimal import Decimal

from .strict_equities import EvidenceError, number, timestamp


def _sma(values, window):
    return sum(values[-window:]) / window


def _ema(values, window):
    value = sum(values[:window]) / window
    alpha = Decimal(2) / (window + 1)
    for price in values[window:]:
        value += alpha * (price - value)
    return value


def evaluate(spec, bars, as_of, holding=False):
    """Return a target position state, not an executable order or a price.

    All windows are in completed observed bars. Callers must separately prove
    that no required sessions are missing. unavailable_at/received_at information
    cannot be replaced with a nominal event date.
    """
    cutoff = timestamp(as_of)
    closes, highs, lows = [], [], []
    previous = None
    for bar in bars:
        ended = timestamp(bar["ended_at"])
        available = timestamp(bar["available_at"])
        received = timestamp(bar["received_at"])
        if not ended <= available <= received <= cutoff:
            raise EvidenceError("POINT_IN_TIME_VIOLATION")
        if previous is not None and ended <= previous:
            raise EvidenceError("DUPLICATE_OR_UNSORTED_BAR")
        previous = ended
        c, h, l = (number(bar[k], True) for k in ("close", "high", "low"))
        if not l <= c <= h:
            raise EvidenceError("OHLC_INVARIANT")
        closes.append(c)
        highs.append(h)
        lows.append(l)
    mode, p = spec.get("implementation"), spec["parameters"]
    if mode not in {"sma", "ema", "momentum", "donchian", "rsi"}:
        return {"status": "DESIGN_ONLY", "target_long": None}
    needed = {"sma": p.get("slow"), "ema": p.get("slow"),
              "momentum": p.get("lookback", 0) + 1,
              "donchian": max(p.get("lookback", 0), p.get("exit_window", 0)) + 1,
              "rsi": p.get("period", 0) + 1}[mode]
    if len(closes) < needed:
        return {"status": "INSUFFICIENT_HISTORY", "target_long": None, "required_bars": needed}
    if mode in {"sma", "ema"}:
        calc = _sma if mode == "sma" else _ema
        target = calc(closes, p["fast"]) > calc(closes, p["slow"])
    elif mode == "momentum":
        target = closes[-1] > closes[-1 - p["lookback"]]
    elif mode == "donchian":
        target = (closes[-1] >= min(lows[-1 - p["exit_window"]:-1]) if holding
                  else closes[-1] > max(highs[-1 - p["lookback"]:-1]))
    else:
        n = p["period"]
        changes = [b - a for a, b in zip(closes, closes[1:])]
        gain = sum(max(Decimal(0), x) for x in changes[:n]) / n
        loss = sum(max(Decimal(0), -x) for x in changes[:n]) / n
        for change in changes[n:]:
            gain = (gain * (n - 1) + max(Decimal(0), change)) / n
            loss = (loss * (n - 1) + max(Decimal(0), -change)) / n
        rsi = 50 if gain == loss == 0 else 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
        target = rsi <= p["exit"] if holding else rsi < p["entry"]
    return {"status": "RESEARCH_SIGNAL_NOT_ORDER", "target_long": bool(target),
            "as_of": as_of, "completed_bars": len(closes)}
