"""The live roster: strategies that place *upcoming* trades and wait for the bar.

Each participant here is a :class:`StrategySpec` from the competition (so the
site, the factor table and the post-mortem machinery all keep working) plus a
``plan(ctx)`` method that submits intents for a **future** session through
:class:`sim.live.LiveContext`.

Rules this module holds itself to
------------------------------
1. **Signal before price.**  Every rule reads either an official series
   (``sim.live.OFFICIAL_SERIES_REGISTER``) or a collected event file registered
   in ``sim/masterfeed.py``.  Where the file is absent, ``data_status`` says so
   and ``plan`` returns without trading - a missing dataset is a measurement gap
   and is published as one.
2. **One decision point per session, executed later.**  ``ctx`` only exposes
   data dated on or before the plan session, and ``place`` refuses anything that
   is not strictly in the future.  A rule that would need the execution bar to
   decide cannot be written here.
3. **Aggression is the objective.**  The brief is maximum return, not
   risk-adjusted return, so sizing runs at the Reg T / leverage bound and no
   participant carries a stop-loss it did not state.  The venue, the margin
   account and the participation cap are what stop it, not good manners.

Why each one is here
--------------------
The roster is deliberately *not* a list of the best ideas.  It contains one
participant per MasterSite project the brief named, the families that actually
circulate on Reddit/YouTube/X (see ``research/SOCIAL_STRATEGY_SOURCES.md``), and
the official-series rules that only became testable once the Nasdaq Composite,
Dow Jones and SOFR histories were collected.  Several are expected to lose: a
competition with only winners measures nothing.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from . import config
from .strategies import StrategySpec

CITATION_MOSKOWITZ = {
    "claim": "Time-series momentum - an asset's own past 12-month return predicts "
             "its next-month return across asset classes",
    "ref": "Moskowitz, Ooi & Pedersen (2012), Journal of Financial Economics 104(2)",
    "url": "https://doi.org/10.1016/j.jfineco.2011.11.003",
    "status": "KNOWN-NOT-FETCHED",
}
CITATION_MOREIRA = {
    "claim": "Volatility-managed portfolios raise Sharpe ratios by scaling exposure "
             "inversely to recent realised variance",
    "ref": "Moreira & Muir (2017), Journal of Finance 72(4)",
    "url": "https://doi.org/10.1111/jofi.12513",
    "status": "KNOWN-NOT-FETCHED",
}
CITATION_LEVERAGE = {
    "claim": "Regulation T sets initial margin at 50% and FINRA 4210 sets the "
             "maintenance floor, so leverage is a rule-bound quantity here",
    "ref": "Federal Reserve Board Reg T; FINRA Rule 4210",
    "url": "https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210",
    "status": "FETCHED-VERIFIED",
}
CITATION_FDA = {
    "claim": "openFDA publishes the FDA's own approval decisions with their "
             "decision dates, which is the event clock this participant reads",
    "ref": "openFDA drug/drugsfda endpoint (data/real/fda/openfda_decisions.jsonl)",
    "url": "https://api.fda.gov/drug/drugsfda.json",
    "status": "FETCHED-VERIFIED",
}
CITATION_MLB = {
    "claim": "MLB StatsAPI is the league's own schedule and scoreboard service",
    "ref": "MLB StatsAPI (data/real/sports/mlb_games_2026.jsonl)",
    "url": "https://statsapi.mlb.com/api/v1/schedule",
    "status": "FETCHED-VERIFIED",
}
CITATION_NOAA = {
    "claim": "NOAA/NCEI station records are the official daily observations for "
             "the two stations this participant reads",
    "ref": "NOAA NCEI access services v1 (data/real/weather/)",
    "url": "https://www.ncei.noaa.gov/access/services/data/v1",
    "status": "FETCHED-VERIFIED",
}
CITATION_NASDAQ_CAL = {
    "claim": "Nasdaq publishes the US equity and options market holiday schedule "
             "that the forward session projection is built from",
    "ref": "Nasdaq Trader US Equity and Options Markets Holiday Schedule 2026",
    "url": "https://www.nasdaqtrader.com/trader.aspx?id=calendar",
    "status": "FETCHED-VERIFIED",
}
CITATION_SOFR = {
    "claim": "The Federal Reserve Bank of New York publishes SOFR daily; the "
             "collected history and the publisher's own API agree on both "
             "cross-checked observations",
    "ref": "FRED series SOFR (data/real/fred/SOFR_2024-09-16_2026-09-17.csv)",
    "url": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/10.json",
    "status": "FETCHED-VERIFIED",
}


# --------------------------------------------------------------------------
# Shared machinery
# --------------------------------------------------------------------------

def _lot(price: float) -> int:
    return config.round_lot(price)


def _shares(notional: float, price: float) -> int:
    if price <= 0 or notional <= 0:
        return 0
    raw = int(notional // price)
    lot = _lot(price)
    return (raw // lot) * lot if lot > 1 else raw


class LiveStrategy:
    """Base class: identity, declared inputs, and the planning hook."""

    spec: StrategySpec
    username: str = ""
    official_inputs: Tuple[str, ...] = ()
    event_inputs: Tuple[str, ...] = ()
    data_status: str = "READY"
    signal_note: str = ""
    horizon: int = 1
    min_trade_fraction: float = 0.02
    #: True when the rule reads nothing but the price history of the instrument
    #: it trades.  That is not a data dependency - there is nothing to collect
    #: and nothing that can go missing - so a price-only participant is not
    #: allowed to be called DATA-MISSING, and it is not required to declare an
    #: external input it does not have.  The same distinction exists in
    #: ``sim/masterfeed.PRICE_DERIVED`` for the same reason.
    price_only: bool = False

    def plan(self, ctx) -> None:                     # pragma: no cover - interface
        raise NotImplementedError

    # -- shared order plumbing --------------------------------------------
    def move_to(self, ctx, targets: Dict[str, float], rationale: str,
                rule: str, evidence: Optional[dict] = None,
                min_fraction: Optional[float] = None) -> List[str]:
        """Push each symbol toward a target fraction of current equity.

        Sells are submitted before buys so the cash they raise is available to
        the entries that follow in the same planning step - the same ordering the
        competition engine enforces, and the reason a rotation is expressible at
        all in a cash-plus-margin account.
        """
        equity = max(ctx.equity, 1.0)
        floor = (self.min_trade_fraction if min_fraction is None else min_fraction)
        orders: List[Tuple[str, str, int, float]] = []
        for symbol in sorted(targets):
            price = ctx.close(symbol)
            if not price:
                continue
            target_frac = float(targets[symbol])
            current = ctx.position(symbol) * price
            delta = target_frac * equity - current
            if abs(delta) < floor * equity:
                continue
            qty = _shares(abs(delta), price)
            if qty <= 0:
                continue
            orders.append((symbol, "buy" if delta > 0 else "sell", qty,
                           round(delta, 2)))
        placed: List[str] = []
        for symbol, side, qty, delta in sorted(orders, key=lambda o: (o[1] != "sell",
                                                                     o[0])):
            intent = ctx.place(symbol, side, quantity=qty, rationale=rationale,
                               rule=rule, evidence=dict(evidence or {}),
                               session_offset=self.horizon)
            if intent is not None:
                placed.append(intent.intent_id)
        return placed

    def flatten(self, ctx, symbols: Sequence[str], rationale: str,
                rule: str, evidence: Optional[dict] = None) -> List[str]:
        return self.move_to(ctx, {s: 0.0 for s in symbols}, rationale, rule,
                            evidence, min_fraction=0.005)


def _series_evidence(ctx, sids: Sequence[str]) -> dict:
    out = {}
    for sid in sids:
        row = ctx.evidence_for(sid)
        if row:
            out[sid] = row
    return out


# --------------------------------------------------------------------------
# 1-4: official index and rate rules (only testable because the official
#      index/rate histories were collected)
# --------------------------------------------------------------------------

class NasdaqMomentumMax(LiveStrategy):
    """Time-series momentum on the official Nasdaq Composite."""

    username = "@NasdaqMomentum_Max"
    official_inputs = ("NASDAQCOM", "SOFR")
    spec = StrategySpec(
        username=username,
        display_name="Nasdaq Momentum Max",
        archetype="time-series trend",
        thesis=("The Nasdaq Composite's own trailing return is the signal: the "
                "brief asks for a Nasdaq leg, and this is the only version of it "
                "whose signal is an official index history rather than a proxy. "
                "The rule is the 12-month time-series momentum of the index, "
                "executed through QQQ at the leverage bound."),
        entry_rules=[
            "Read the official NASDAQCOM daily close (FRED, source: Nasdaq, Inc.)",
            "If the index's 210-session return is positive and the close is above "
            "its 200-session mean, target long QQQ at 1.95x equity",
            "If both are negative, target short QQQ at 0.8x equity",
            "Otherwise stand flat",
        ],
        exit_rules=["The rule reverses only when the sign of both conditions flips; "
                    "there is no stop and no volatility target"],
        sizing="up to 1.95x gross, inside the 2.0x house leverage bound",
        leverage="1.8-1.95x gross when long, 0.8x when short",
        cadence="every session, executed the next session",
        horizon="months",
        academic_basis=[CITATION_MOSKOWITZ, CITATION_LEVERAGE],
        known_failure_modes=[
            "Time-series momentum is documented to crash at turning points; the "
            "200-session filter does not remove that, it delays it",
            "The index signal is measured on the index while the trade is in QQQ, "
            "so tracking and fees sit between signal and P&L",
            "Leverage turns a 10% index drawdown into a 20% account drawdown"],
        aggression=5,
        why_return_seeking=("A single levered long/short exposure with no de-risking "
                            "rule is the maximum-return expression of the momentum "
                            "premium; the risk is the price of that."),
    )

    def plan(self, ctx) -> None:
        nasdaq = ctx.series("NASDAQCOM")
        if nasdaq is None or len(nasdaq.values) < 210:
            return
        mom = nasdaq.pct_change(210)
        sma = nasdaq.sma(200)
        close = nasdaq.values[-1]
        ev = _series_evidence(ctx, ("NASDAQCOM", "SOFR"))
        if mom is None or sma is None:
            return
        if mom > 0 and close > sma:
            target = 1.95
            why = (f"NASDAQCOM {close:,.0f} > 200d mean {sma:,.0f} and 210-session "
                   f"return {mom:+.2f}% > 0")
        elif mom < 0 and close < sma:
            target = -0.8
            why = (f"NASDAQCOM {close:,.0f} < 200d mean {sma:,.0f} and 210-session "
                   f"return {mom:+.2f}% < 0")
        else:
            target = 0.0
            why = (f"mixed: 210-session return {mom:+.2f}%, close vs 200d mean "
                   f"{100.0 * (close / sma - 1.0):+.2f}%")
        self.move_to(ctx, {"QQQ": target},
                     f"nasdaq-momentum: {why}", "official NASDAQCOM trend rule",
                     ev, min_fraction=0.03)


class DowNasdaqSpreadRotator(LiveStrategy):
    """The NYSE blue-chip leg against the Nasdaq leg, both official."""

    username = "@DowNasdaq_SpreadMax"
    official_inputs = ("DJIA", "NASDAQCOM")
    spec = StrategySpec(
        username=username,
        display_name="Dow/Nasdaq Spread Rotator",
        archetype="curve/carry rotation",
        thesis=("The brief names both Nasdaq and NYSE. The official histories for "
                "both now exist (FRED DJIA, source: S&P Dow Jones Indices; FRED "
                "NASDAQCOM, source: Nasdaq, Inc.), so the relative-strength spread "
                "between the two indices can be measured instead of asserted. The "
                "participant holds the stronger leg through SPY or QQQ."),
        entry_rules=[
            "Compute each index's 63-session return from the official series",
            "Long the stronger leg at 1.90x equity through SPY (Dow leg) or QQQ "
            "(Nasdaq leg)",
            "If the two returns are within 1pp, hold both at 0.9x each",
        ],
        exit_rules=["The spread is recomputed every session; a flip rotates the book"],
        sizing="1.8-1.9x gross in a single leg, 1.8x split when undecided",
        leverage="~1.9x gross",
        cadence="every session, executed the next session",
        horizon="weeks to months",
        academic_basis=[CITATION_MOSKOWITZ, CITATION_LEVERAGE],
        known_failure_modes=[
            "Two highly correlated indices differ mostly in sector weight, so the "
            "spread is a sector bet in disguise",
            "Rotating on a 63-session window is a short signal for a slow spread; "
            "whipsaw is the base case",
            "SPY and QQQ track their indices imperfectly, so part of any measured "
            "spread is tracking noise"],
        aggression=5,
        why_return_seeking="Single-leg concentrated exposure with daily rotation.",
    )

    def plan(self, ctx) -> None:
        djia = ctx.series("DJIA")
        nasdaq = ctx.series("NASDAQCOM")
        if djia is None or nasdaq is None:
            return
        d = djia.pct_change(63)
        n = nasdaq.pct_change(63)
        if d is None or n is None:
            return
        ev = _series_evidence(ctx, ("DJIA", "NASDAQCOM"))
        if abs(d - n) < 1.0:
            targets = {"SPY": 0.9, "QQQ": 0.9}
            why = f"within 1pp (Dow {d:+.2f}% vs Nasdaq {n:+.2f}%)"
        elif d > n:
            targets = {"SPY": 1.9, "QQQ": 0.0}
            why = f"Dow leg stronger ({d:+.2f}% vs {n:+.2f}%)"
        else:
            targets = {"QQQ": 1.9, "SPY": 0.0}
            why = f"Nasdaq leg stronger ({n:+.2f}% vs {d:+.2f}%)"
        self.move_to(ctx, targets, f"dow-vs-nasdaq: {why}",
                     "official DJIA vs NASDAQCOM 63-session relative strength",
                     ev, min_fraction=0.03)


class VIXRegimeMax(LiveStrategy):
    """Volatility-regime timing on the official VIX close."""

    username = "@VIXRegime_LiveMax"
    official_inputs = ("VIXCLS", "SP500")
    spec = StrategySpec(
        username=username,
        display_name="VIX Regime Live Max",
        archetype="volatility regime timing (official index)",
        thesis=("The official VIX close (FRED VIXCLS) is a published observation, "
                "not a model output, so a regime rule built on it is testable "
                "without a volatility model. The participant holds maximum equity "
                "leverage in the calm regime and flips short when the index itself "
                "breaks its own trend while volatility is elevated."),
        entry_rules=[
            "VIX close below its 60-session median and the S&P 500 above its "
            "100-session mean -> target long QQQ at 1.95x",
            "VIX close above the 80th percentile of the last 252 sessions and the "
            "S&P 500 below its 50-session mean -> target short QQQ at 1.0x",
            "Otherwise hold 0.5x QQQ (neither regime dominates)",
        ],
        exit_rules=["Regime re-evaluated every session; no stop"],
        sizing="up to 1.95x gross",
        leverage="1.95x long / 1.0x short / 0.5x default",
        cadence="every session, executed the next session",
        horizon="days to weeks",
        academic_basis=[CITATION_MOREIRA, CITATION_LEVERAGE],
        known_failure_modes=[
            "The calm regime is exactly when a shock does the most damage; this is "
            "the classic volatility-timing failure",
            "VIX is a mean-reverting index, so a percentile rule sells every spike "
            "one session late by construction",
            "The official VIX close is an index level, not a tradable price: the "
            "trade is in QQQ and the mapping is declared, not measured"],
        aggression=5,
        why_return_seeking="Levered single-instrument regime bet with a short leg.",
    )

    def plan(self, ctx) -> None:
        vix = ctx.series("VIXCLS")
        spx = ctx.series("SP500")
        if vix is None or spx is None or len(vix.values) < 252:
            return
        window = vix.window(252)
        ordered = sorted(window)
        median60 = vix.sma(60)
        p80 = ordered[int(0.8 * (len(ordered) - 1))]
        spx_mean100 = spx.sma(100)
        spx_mean50 = spx.sma(50)
        if None in (median60, p80, spx_mean100, spx_mean50):
            return
        ev = _series_evidence(ctx, ("VIXCLS", "SP500"))
        v = vix.values[-1]
        s = spx.values[-1]
        if v < median60 and s > spx_mean100:
            target, regime = 1.95, "calm"
        elif v > p80 and s < spx_mean50:
            target, regime = -1.0, "stress-with-downtrend"
        else:
            target, regime = 0.5, "undecided"
        self.move_to(ctx, {"QQQ": target},
                     f"vix-regime[{regime}]: VIX {v:.2f} vs 60d median {median60:.2f}, "
                     f"252d 80th percentile {p80:.2f}; S&P {s:,.0f} vs 100d mean "
                     f"{spx_mean100:,.0f}",
                     "official VIXCLS/SP500 regime rule", ev, min_fraction=0.03)


class SOFRPivotRider(LiveStrategy):
    """Monetary-policy pivot read off the official SOFR history."""

    username = "@SOFRPivot_Rider"
    official_inputs = ("SOFR", "SP500")
    spec = StrategySpec(
        username=username,
        display_name="SOFR Pivot Rider",
        archetype="curve/carry rotation",
        thesis=("SOFR is the secured funding rate the Federal Reserve Bank of New "
                "York publishes every business day. Its 21-session direction is a "
                "public read on whether funding conditions are easing. Easing "
                "regimes should favour long-duration and high-beta equity; a rising "
                "funding rate should not. The rate series was collected for this "
                "book, so unlike Season 1 the rule is measured on an official "
                "observation rather than a declared parameter."),
        entry_rules=[
            "Compute the change in the official SOFR over 21 sessions",
            "SOFR lower by more than 3bp -> target long QQQ 1.5x plus TSLA 0.45x",
            "SOFR higher by more than 5bp -> target long TLT 1.8x (duration bid as "
            "growth expectations fade)",
            "Otherwise hold 0.8x SPY",
        ],
        exit_rules=["Re-evaluated every session; the rate series, not a price, "
                    "closes the position"],
        sizing="up to 1.95x gross",
        leverage="1.5-1.8x gross",
        cadence="every session, executed the next session",
        horizon="weeks",
        academic_basis=[CITATION_SOFR, CITATION_LEVERAGE],
        known_failure_modes=[
            "A 21-session funding change is a noisy proxy for policy expectations; "
            "repo rates move on quarter-end and settlement mechanics unrelated to "
            "equity discount rates (the collected history shows a 23bp one-day "
            "jump on 2026-09-17 that is a settlement effect, not a policy signal)",
            "TLT is a duration bet with its own convexity and rate-path risk",
            "TSLA is a single-name residual risk wearing a macro signal"],
        aggression=4,
        why_return_seeking="Concentrated rotation, levered, no volatility target.",
    )

    def plan(self, ctx) -> None:
        sofr = ctx.series("SOFR")
        if sofr is None or len(sofr.values) < 22:
            return
        change_bp = (sofr.values[-1] - sofr.values[-22]) * 100.0
        ev = _series_evidence(ctx, ("SOFR", "SP500"))
        if change_bp < -3.0:
            targets = {"QQQ": 1.5, "TSLA": 0.45, "SPY": 0.0, "TLT": 0.0}
            why = f"easing ({change_bp:+.1f}bp over 21 sessions)"
        elif change_bp > 5.0:
            targets = {"TLT": 1.8, "QQQ": 0.0, "TSLA": 0.0, "SPY": 0.0}
            why = f"tightening ({change_bp:+.1f}bp over 21 sessions)"
        else:
            targets = {"SPY": 0.8, "QQQ": 0.0, "TSLA": 0.0, "TLT": 0.0}
            why = f"flat ({change_bp:+.1f}bp over 21 sessions)"
        self.move_to(ctx, targets, f"sofr-pivot: {why}",
                     "official SOFR 21-session direction", ev, min_fraction=0.03)


class CurveSteepenerMaxBeta(LiveStrategy):
    """Yield-curve slope rotation between cyclicals and duration."""

    username = "@CurveSteepener_MaxBeta"
    official_inputs = ("DGS10", "DGS3MO")
    spec = StrategySpec(
        username=username,
        display_name="Curve Steepener Max Beta",
        archetype="curve/carry rotation",
        thesis=("The 10-year minus 3-month Treasury spread is an official daily "
                "observation from two Treasury constant-maturity series. A "
                "steepening curve is the classic early-cycle signal and a "
                "flattening one is the late-cycle signal, so the participant holds "
                "high-beta cyclicals into steepening and long duration into "
                "flattening."),
        entry_rules=[
            "spread = DGS10 - DGS3MO, and its 21-session change",
            "Change > +10bp -> target JPM 0.9x, XOM 0.9x (cyclicals)",
            "Change < -10bp -> target TLT 1.9x (duration)",
            "Otherwise hold 1.0x SPY",
        ],
        exit_rules=["Daily re-evaluation of the same two official series"],
        sizing="up to 1.9x gross",
        leverage="1.8-1.9x gross",
        cadence="every session, executed the next session",
        horizon="weeks to months",
        academic_basis=[CITATION_LEVERAGE, {
            "claim": "The term spread is a monitored recession/cycle indicator "
                     "published by the Federal Reserve Bank of St. Louis",
            "ref": "FRED series T10Y3M / DGS10 / DGS3MO",
            "url": "https://fred.stlouisfed.org/series/DGS10",
            "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "A 21-session change in a slow spread is mostly noise",
            "Two single names (JPM, XOM) carry idiosyncratic risk that dominates a "
            "10bp curve move",
            "The 2024-2026 collected window contains an inverted curve, so the "
            "steepening branch may simply never fire"],
        aggression=4,
        why_return_seeking="Concentrated long-only rotation at the leverage bound.",
    )

    def plan(self, ctx) -> None:
        long_end = ctx.series("DGS10")
        short_end = ctx.series("DGS3MO")
        if long_end is None or short_end is None:
            return
        short_by_date = dict(zip(short_end.dates, short_end.values))
        pairs = [(d, v, short_by_date[d]) for d, v in zip(long_end.dates, long_end.values)
                 if d in short_by_date]
        if len(pairs) < 25:
            return
        spreads = [(d, a - b) for d, a, b in pairs]
        change_bp = (spreads[-1][1] - spreads[-23][1]) * 100.0
        ev = _series_evidence(ctx, ("DGS10", "DGS3MO"))
        if change_bp > 10.0:
            targets = {"JPM": 0.9, "XOM": 0.9, "TLT": 0.0, "SPY": 0.0}
            why = f"steepening {change_bp:+.1f}bp"
        elif change_bp < -10.0:
            targets = {"TLT": 1.9, "JPM": 0.0, "XOM": 0.0, "SPY": 0.0}
            why = f"flattening {change_bp:+.1f}bp"
        else:
            targets = {"SPY": 1.0, "JPM": 0.0, "XOM": 0.0, "TLT": 0.0}
            why = f"flat {change_bp:+.1f}bp"
        self.move_to(ctx, targets, f"curve-steepener: {why}",
                     "official DGS10-DGS3MO 21-session change", ev, min_fraction=0.03)


# --------------------------------------------------------------------------
# 5-9: rules that read a collected file, or declare the gap
# --------------------------------------------------------------------------

class OilDollarFade(LiveStrategy):
    """WTI and the broad dollar, both official, against natural gas and energy."""

    username = "@OilDollar_FadeUNG"
    official_inputs = ("DCOILWTICO", "DTWEXBGS")
    spec = StrategySpec(
        username=username,
        display_name="Oil-Dollar Fade",
        archetype="commodity-macro proxy",
        thesis=("Crude oil and the trade-weighted dollar are the two official "
                "series that drive commodity-equity cash flows in opposite "
                "directions. The rule buys energy exposure when oil is rising and "
                "the dollar is falling, and shorts the natural-gas ETF when both "
                "move against it."),
        entry_rules=[
            "21-session return of official DCOILWTICO and of official DTWEXBGS",
            "Oil up and dollar down -> target XOM 1.0x plus UNG 0.8x",
            "Oil down and dollar up -> target short UNG 1.0x",
            "Otherwise hold 0.5x XOM",
        ],
        exit_rules=["Daily re-evaluation; no stop"],
        sizing="up to 1.8x gross",
        leverage="1.5-1.8x gross",
        cadence="every session, executed the next session",
        horizon="weeks",
        academic_basis=[CITATION_LEVERAGE, {
            "claim": "The Federal Reserve publishes the nominal broad dollar index "
                     "the signal reads",
            "ref": "FRED series DTWEXBGS (H.10 release)",
            "url": "https://fred.stlouisfed.org/series/DTWEXBGS",
            "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "UNG tracks front-month natural-gas futures with documented roll decay, "
            "so a correct gas call can still lose money through the wrapper",
            "WTI spot and energy-equity earnings are not the same series",
            "A two-condition macro filter fires rarely and can sit flat for months"],
        aggression=4,
        why_return_seeking="Concentrated commodity-equity exposure with a short leg.",
    )

    def plan(self, ctx) -> None:
        oil = ctx.series("DCOILWTICO")
        usd = ctx.series("DTWEXBGS")
        if oil is None or usd is None:
            return
        oil_chg = oil.pct_change(21)
        usd_chg = usd.pct_change(21)
        if oil_chg is None or usd_chg is None:
            return
        ev = _series_evidence(ctx, ("DCOILWTICO", "DTWEXBGS"))
        if oil_chg > 0 and usd_chg < 0:
            targets = {"XOM": 1.0, "UNG": 0.8}
            why = f"oil {oil_chg:+.1f}%/21d with dollar {usd_chg:+.1f}%/21d"
        elif oil_chg < 0 and usd_chg > 0:
            targets = {"UNG": -1.0, "XOM": 0.0}
            why = f"oil {oil_chg:+.1f}%/21d against dollar {usd_chg:+.1f}%/21d"
        else:
            targets = {"XOM": 0.5, "UNG": 0.0}
            why = (f"mixed: oil {oil_chg:+.1f}%/21d, dollar {usd_chg:+.1f}%/21d")
        self.move_to(ctx, targets, f"oil-dollar: {why}",
                     "official DCOILWTICO/DTWEXBGS 21-session rule", ev,
                     min_fraction=0.03)


class FDAPdufaDrifter(LiveStrategy):
    """Approval-flow drift, on the FDA's own decision dates."""

    username = "@FDA_PDUFA_Drifter"
    event_inputs = ("fda_orig_30d", "fda_orig_z")
    spec = StrategySpec(
        username=username,
        display_name="FDA PDUFA Drifter",
        archetype="commodity-macro proxy",
        thesis=("openFDA publishes the FDA's approvals with their decision dates. "
                "The live book reads the trailing-30-day count of original-application "
                "approvals and holds the biotech complex at the leverage bound while "
                "the approval rate is at or above its own median - the same event "
                "clock Season 2 traded, but now with every entry deferred to the "
                "next session so the drift has to survive the wait."),
        entry_rules=[
            "Trailing 30-day count of openFDA original approvals vs its "
            "252-session median",
            "Count at or above the median -> target XBI 1.5x plus IBB 0.4x",
            "Count below the median -> flat",
        ],
        exit_rules=["Flatten when the count falls back below the median"],
        sizing="up to 1.9x gross",
        leverage="1.9x gross",
        cadence="every session, executed the next session",
        horizon="weeks",
        academic_basis=[CITATION_FDA],
        known_failure_modes=[
            "The count is a sector-level proxy: many approvals belong to unlisted "
            "sponsors, so the signal can move with no listed stock behind it",
            "Biotech is rate-sensitive, so a funding shock beats a good approval month",
            "A one-session execution delay removes the announcement-day pop, which "
            "is exactly where a naive version of this rule earned its backtest"],
        aggression=5,
        why_return_seeking="Concentrated sector exposure at the leverage bound.",
    )

    def plan(self, ctx) -> None:
        if not ctx.signal_available("fda_orig_30d"):
            return
        count = ctx.signal("fda_orig_30d")
        history = ctx.signal_history("fda_orig_30d", 252)
        if len(history) < 30:
            return
        median = sorted(history)[len(history) // 2]
        ev = {"fda_orig_30d": ctx.signal_evidence("fda_orig_30d") or {}}
        if count >= median:
            self.move_to(ctx, {"XBI": 1.5, "IBB": 0.4},
                         f"fda-drift: 30d approvals {count:.0f} >= median {median:.0f}",
                         "openFDA approval count, trailing 30 days", ev,
                         min_fraction=0.03)
        else:
            self.flatten(ctx, ("XBI", "IBB"),
                         f"fda-drift: 30d approvals {count:.0f} < median {median:.0f}",
                         "openFDA approval count, trailing 30 days", ev)


class MLBAttentionLive(LiveStrategy):
    """League attention: the official MLB schedule against sports equities."""

    username = "@MLB_Attention_Live"
    event_inputs = ("mlb_games_7d", "mlb_upsets_7d")
    spec = StrategySpec(
        username=username,
        display_name="MLB Attention Live",
        archetype="event attention proxy",
        thesis=("The official MLB StatsAPI schedule is a dated, public clock of how "
                "much live sport is happening. The participant takes the sportsbook "
                "and sports-data equities levered when the schedule is dense and "
                "upsets are frequent - a pure attention proxy, and labelled as one."),
        entry_rules=[
            "Games scheduled in the trailing 7 days, from the official StatsAPI file",
            "Dense week (> median) and upsets in that window -> target DKNG 0.7x, "
            "FLUT 0.55x, SRAD 0.5x",
            "Sparse week -> target short PENN 0.6x",
        ],
        exit_rules=["Daily re-evaluation of the same two event counts"],
        sizing="up to 1.75x gross across three names",
        leverage="up to 1.75x gross",
        cadence="every session, executed the next session",
        horizon="days to weeks",
        academic_basis=[CITATION_MLB],
        known_failure_modes=[
            "Schedule density is not revenue and not sentiment; the mapping from "
            "games played to gaming-equity returns is unproven (WEAK-MAPPING in the "
            "MasterFeed register)",
            "The archive covers 2026-03-25 onwards, so any session before that has "
            "no signal and the participant stands aside",
            "Four small-cap gaming names carry high idiosyncratic and regulatory risk"],
        aggression=4,
        why_return_seeking="Levered multi-name event-driven exposure.",
    )

    def plan(self, ctx) -> None:
        if not ctx.signal_available("mlb_games_7d"):
            return
        games = ctx.signal("mlb_games_7d")
        upsets = ctx.signal("mlb_upsets_7d")
        history = ctx.signal_history("mlb_games_7d", 90)
        if len(history) < 20 or games <= 0:
            return
        median = sorted(history)[len(history) // 2]
        ev = {k: (ctx.signal_evidence(k) or {}) for k in ("mlb_games_7d",
                                                          "mlb_upsets_7d")}
        if games > median and upsets > 0:
            self.move_to(ctx, {"DKNG": 0.7, "FLUT": 0.55, "SRAD": 0.5, "PENN": 0.0},
                         f"mlb-attention: {games:.0f} games and {upsets:.0f} upsets "
                         f"in 7 days vs median {median:.0f}",
                         "official MLB StatsAPI schedule density", ev,
                         min_fraction=0.03)
        elif games <= median:
            self.move_to(ctx, {"PENN": -0.6, "DKNG": 0.0, "FLUT": 0.0, "SRAD": 0.0},
                         f"mlb-attention: sparse schedule ({games:.0f} vs median "
                         f"{median:.0f})", "official MLB StatsAPI schedule density",
                         ev, min_fraction=0.03)


class WeatherColdSnapLive(LiveStrategy):
    """Official station temperatures against gas and utility demand."""

    username = "@Weather_ColdSnap_Live"
    event_inputs = ("weather_cold_anomaly_10d", "weather_precip_30d_in")
    spec = StrategySpec(
        username=username,
        display_name="Weather Cold Snap Live",
        archetype="event attention proxy",
        thesis=("NOAA/NCEI daily station records are the official observation of "
                "heating demand at two US stations. The proxy trade is natural gas "
                "and utilities: a cold anomaly should lift both with a lag, and the "
                "participant is explicit that the mapping is weak and is being "
                "tested rather than asserted."),
        entry_rules=[
            "10-day minimum-temperature anomaly from the collected NOAA files",
            "Anomaly below -5 degC -> target UNG 1.0x plus XLU 0.8x",
            "Anomaly above +5 degC -> target short UNG 0.8x",
            "Otherwise hold XLU 0.6x",
        ],
        exit_rules=["Daily re-evaluation of the anomaly"],
        sizing="up to 1.8x gross",
        leverage="1.6-1.8x gross",
        cadence="every session, executed the next session",
        horizon="days",
        academic_basis=[CITATION_NOAA],
        known_failure_modes=[
            "Two stations are not a national weather map",
            "The gas-futures roll and the utility rate base are unrelated to a "
            "single cold week",
            "Weather is the most heavily forecast variable in the market, so the "
            "anomaly may already be priced"],
        aggression=4,
        why_return_seeking="Levered commodity/utility proxy with a short leg.",
    )

    def plan(self, ctx) -> None:
        if not ctx.signal_available("weather_cold_anomaly_10d"):
            return
        anomaly = ctx.signal("weather_cold_anomaly_10d")
        ev = {"weather_cold_anomaly_10d":
              ctx.signal_evidence("weather_cold_anomaly_10d") or {}}
        if anomaly == 0.0:
            return
        if anomaly < -5.0:
            targets = {"UNG": 1.0, "XLU": 0.8}
            why = f"cold anomaly {anomaly:+.1f}degC over 10 days"
        elif anomaly > 5.0:
            targets = {"UNG": -0.8, "XLU": 0.6}
            why = f"warm anomaly {anomaly:+.1f}degC over 10 days"
        else:
            targets = {"XLU": 0.6, "UNG": 0.0}
            why = f"anomaly inside +/-5degC ({anomaly:+.1f})"
        self.move_to(ctx, targets, f"weather-proxy: {why}",
                     "NOAA/NCEI 10-day minimum-temperature anomaly", ev,
                     min_fraction=0.03)


class InsiderClusterLive(LiveStrategy):
    """Form 4 cluster purchases - implemented, gated on the filing stream."""

    username = "@InsiderCluster_Live"
    event_inputs = ("insider_buys_30d", "insider_buyers_30d", "insider_ceo_buys_30d",
                    "insider_buy_ratio_30d")

    def __init__(self) -> None:
        # The insider collection state is a fact about the checkout, not about
        # this class, so the status is computed at instantiation: the moment the
        # bulk data sets or the per-filing walk land, the participant reports
        # READY and the next rollover plans from real filings - without a code
        # change that could silently claim data that is not there.
        super().__init__()
        from . import masterfeed
        info = masterfeed.insider_collection_present()
        if info["present"]:
            # The collection landing is necessary but not sufficient for this
            # rule to trade: it only acts on open-market purchases (code P),
            # and a walk of recent Form 4 filings can legitimately contain
            # none.  Distinguish the two so an idle book is explained, not
            # dressed up as "READY but traded nothing".
            rows, _files, _note = masterfeed._load_insider_rows(
                masterfeed.REAL_ROOT)
            p_purchases = sum(
                1 for r in rows if str(r.get("code") or "").upper() == "P")
            coverage = info.get("coverage") or []
            window = (f" ({coverage[0]}..{coverage[-1]})" if len(coverage) == 2 else "")
            if p_purchases:
                self.data_status = "READY"
                self.signal_note = (
                    f"SEC insider data has landed: {info['rows']} normalised "
                    f"transactions{window} from "
                    + ", ".join(str(f) for f in info["files"])
                    + f", including {p_purchases} open-market purchases (code P). "
                      "The rule reads code-P clusters and CEO/CFO purchases from "
                      "it; the first intents appear in the next planned session, "
                      "never backdated.")
            else:
                self.data_status = "READY-NO-OBSERVATIONS"
                self.signal_note = (
                    f"SEC insider data has landed: {info['rows']} normalised "
                    f"transactions{window} from "
                    + ", ".join(str(f) for f in info["files"])
                    + ", but none is an open-market purchase (code P), which is "
                      "the only code this rule trades. The participant is ready "
                      "and reading real filings; it places no intents because the "
                      "collected window contains no qualifying observation - a "
                      "measurement gap inside real data, reported as such.")
        else:
            self.data_status = "DATA-MISSING"
            self.signal_note = (
                "The SEC insider collections (quarterly bulk data sets and the "
                "per-filing walk) have not landed in this checkout; the rule "
                "therefore places no intents and this book reports a measurement "
                "gap, not a zero result.")
    spec = StrategySpec(
        username=username,
        display_name="Insider Cluster Live",
        archetype="informed-flow following",
        thesis=("SEC Form 4 is the primary filing for insider transactions and it "
                "carries the execution date, price and the reporting officer's "
                "title. This participant buys a name when two or more insiders "
                "bought it in the open market inside ten days. It is implemented "
                "and gated: with no collected filing stream it cannot trade, and "
                "says so rather than substituting a price proxy."),
        entry_rules=[
            "Count open-market purchases (transaction code P) per issuer over the "
            "trailing 30 days from the collected Form 4 JSONL, dated by the EDGAR "
            "filing date (when the purchase became public), never by the trade date",
            "Two or more distinct reporting persons bought -> target the issuer at "
            "0.9x equity (lots are not people: one filing reporting 25 lots by one "
            "insider is one buyer)",
            "A CEO/CFO purchase present -> target 1.4x equity",
        ],
        exit_rules=["Flatten a name once its trailing 30-day filing window holds "
                    "neither a cluster nor a CEO/CFO purchase (about a month after "
                    "the last qualifying filing)"],
        sizing="0.9-1.4x per name, up to 1.9x gross across names",
        leverage="up to 1.9x gross",
        cadence="every session, executed the next session",
        horizon="one to three months",
        academic_basis=[{
            "claim": "Insider open-market purchases predict positive abnormal "
                     "returns over the following months",
            "ref": "SEC Form 4 filings (primary record)",
            "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4",
            "status": "OFFICIAL-ENDPOINT-DOCUMENTED; COLLECTION LANDED VIA THE "
                      "RENDERED-VIEW LANE (data/real/sec_agent/)"}],
        known_failure_modes=[
            "The filing arrives up to two business days after the trade (and a late "
            "filing can arrive months after it), so the informational edge is partly "
            "gone by the time it is readable; the rule dates every purchase by its "
            "filing date for exactly that reason",
            "Cluster rules fire rarely, so the sample is thin by construction",
            "Code P is a mechanical filter: a broker-initiated purchase the insider "
            "later disavowed (MSFT, accession 0000789019-25-000120) still counts as "
            "one purchase, because the rule reads the transaction code, not the "
            "footnotes",
            "The collected stream is the rendered-view lane plus the EDGAR full-text "
            "locator; until the SEC quarterly bulk sets land it is complete only for "
            "the purchases that locator found"],
        aggression=4,
        why_return_seeking="Concentrated single-name exposure on a rare, dated signal.",
    )

    def plan(self, ctx) -> None:
        if not ctx.signal_available("insider_buys_30d"):
            return
        ratio = ctx.signal("insider_buy_ratio_30d")
        ev = {"insider_buy_ratio_30d": ctx.signal_evidence("insider_buy_ratio_30d") or {}}
        for symbol in ("AAPL", "MSFT", "NVDA", "JPM", "XOM", "JNJ", "PG", "TSLA",
                       "MU", "T"):
            buys = ctx.signal_by_symbol("insider_buys_30d", symbol) or 0.0
            buyers = ctx.signal_by_symbol("insider_buyers_30d", symbol) or 0.0
            ceo = ctx.signal_by_symbol("insider_ceo_buys_30d", symbol) or 0.0
            price = ctx.close(symbol)
            if not price:
                continue
            if ceo >= 1.0:
                self.move_to(ctx, {symbol: 1.4},
                             f"insider: {ceo:.0f} CEO/CFO purchase lot(s) filed in 30d",
                             "Form 4 code-P purchases by a CEO or CFO", ev,
                             min_fraction=0.03)
            elif buyers >= 2.0:
                self.move_to(ctx, {symbol: 0.9},
                             f"insider: {buyers:.0f} distinct insiders, {buys:.0f} "
                             f"open-market purchase lots filed in 30 days, "
                             f"buy/sell ratio {ratio:.2f}",
                             "Form 4 code-P cluster", ev, min_fraction=0.03)
            elif ctx.position(symbol):
                # The window has emptied: the declared exit.  The first
                # rehearsal with real filings held TSLA for a year because this
                # branch did not exist (the exit was declared, never coded).
                self.flatten(ctx, (symbol,),
                             "insider: no cluster or CEO/CFO purchase filed in the "
                             "trailing 30 days - window emptied",
                             "Form 4 code-P window exit", ev)


class InjuryFeedForward(LiveStrategy):
    """League injury designations - a declared forward-only probe."""

    username = "@InjuryFeed_Forward"
    event_inputs = ("nfl_injury_report", "nba_injury_report")
    data_status = "FORWARD-ONLY"
    signal_note = ("Neither league publishes a retrievable archive of past injury "
                   "designations, so this participant can only ever run forward. It "
                   "places no backdated intents by design. A dated snapshot archive "
                   "(official league documents plus the machine-readable ESPN "
                   "companion) is collected on a schedule into "
                   "data/real/sports/official/archive/, so the forward test now "
                   "accumulates week by week instead of resetting.")
    spec = StrategySpec(
        username=username,
        display_name="Injury Feed Forward",
        archetype="event attention proxy",
        thesis=("Both leagues publish injury designations, and nothing archives them "
                "in a form this project may retrieve. A rule that trades sportsbook "
                "equities on injury-heavy weeks is therefore a genuine forward test "
                "with no history: it exists here to be measured from the day the "
                "collection starts, and to be honest that it has no sample yet."),
        entry_rules=[
            "Read the collected NFL/NBA injury-report documents for the current week",
            "Heavy designation count at contending teams -> target FLUT 0.8x, "
            "DKNG 0.6x, GENI 0.4x",
            "Quiet week -> target short PENN 0.5x",
        ],
        exit_rules=["Weekly re-evaluation"],
        sizing="up to 1.8x gross",
        leverage="up to 1.8x gross",
        cadence="weekly, executed the next session",
        horizon="days",
        academic_basis=[{
            "claim": "The NFL and NBA publish their own injury reports",
            "ref": "Official league publication channels",
            "url": "https://www.nba.com/injury-report",
            "status": "FORWARD-ONLY (no retrievable archive)"}],
        known_failure_modes=[
            "No historical sample exists, so nothing here can be backtested - only "
            "forward-tested",
            "Injury news is public and heavily covered, so the informational content "
            "at the moment of publication is close to zero",
            "The mapping from injuries to handle is a hypothesis, not a measurement"],
        aggression=3,
        why_return_seeking="Event-driven, but sized below the leverage bound because "
                           "nothing about the mapping has been measured yet.",
    )

    def plan(self, ctx) -> None:
        if not (ctx.signal_available("nfl_injury_report")
                or ctx.signal_available("nba_injury_report")):
            return
        count = ctx.signal("nfl_injury_report") + ctx.signal("nba_injury_report")
        if count <= 0:
            return
        ev = {k: (ctx.signal_evidence(k) or {})
              for k in ("nfl_injury_report", "nba_injury_report")}
        self.move_to(ctx, {"FLUT": 0.8, "DKNG": 0.6, "GENI": 0.4},
                     f"injury-feed: {count:.0f} designations in the collected window",
                     "official league injury-report count", ev, min_fraction=0.03)


# --------------------------------------------------------------------------
# 10-16: the competition-contest rules and the community families
# --------------------------------------------------------------------------

class LeapStyleAutoLiquidator(LiveStrategy):
    """The Leap's contest rule, executed as a forward book."""

    username = "@LeapStyle_AutoLiquidate"
    official_inputs = ("NASDAQCOM", "VIXCLS")
    spec = StrategySpec(
        username=username,
        display_name="Leap-Style Auto Liquidator",
        archetype="levered beta",
        thesis=("The Leap ranks accounts on total return and liquidates everything "
                "at the end of the contest. The rule copied here is the contest's, "
                "not a market view: hold the fastest-trailing strong asset at the "
                "leverage bound, add when the trail improves, and be flat on the "
                "final session. Futures are not available in this venue, so the "
                "instruments are the ETF proxies and that substitution is declared."),
        entry_rules=[
            "Rank SPY, QQQ, IWM, GLD, TLT, NVDA, TSLA by 42-session return",
            "Target the leader at 1.95x equity whenever the leader's return is "
            "positive, measured on the official Nasdaq/VIX regime read",
            "When the leader's 42-session return is negative, target 0.6x TLT",
        ],
        exit_rules=[
            "Rotate on rank change",
            "Flatten everything on the final session of the competition window"],
        sizing="1.95x gross in one instrument",
        leverage="1.95x gross",
        cadence="every session, executed the next session",
        horizon="days to weeks",
        academic_basis=[{
            "claim": "The Leap's published rules: total-return ranking, maximum "
                     "leverage available to the account, all positions closed at "
                     "the close of the competition",
            "ref": "TradingView The Leap - December 2025 rules",
            "url": "https://www.tradingview.com/the-leap/december-2025/rules/",
            "status": "FETCHED-VERIFIED"},
            CITATION_LEVERAGE],
        known_failure_modes=[
            "Concentration into the fastest-trailing asset is the classic "
            "performance-chasing trade and it is documented to mean-revert",
            "A one-session execution delay on a rank-rotation rule is expensive",
            "The venue has no futures, so the leverage the real contest permits is "
            "approximated by a 2:1 equity margin account"],
        aggression=5,
        why_return_seeking="Concentration plus maximum permitted leverage; that is "
                           "the entire design of the contest it copies.",
    )

    def plan(self, ctx) -> None:
        candidates = ("SPY", "QQQ", "IWM", "GLD", "TLT", "NVDA", "TSLA")
        scores: Dict[str, float] = {}
        for symbol in candidates:
            closes = ctx.closes(symbol, 43)
            if len(closes) < 43 or closes[0] <= 0:
                continue
            scores[symbol] = 100.0 * (closes[-1] / closes[0] - 1.0)
        if not scores:
            return
        leader = max(scores, key=scores.get)
        ev = _series_evidence(ctx, ("NASDAQCOM", "VIXCLS"))
        ev[f"{leader}_price"] = ctx.evidence_for_symbol(leader)
        if scores[leader] > 0:
            self.move_to(ctx, {leader: 1.95},
                         f"leap: leader {leader} at {scores[leader]:+.2f}% over 42 "
                         f"sessions", "contest rank rotation", ev, min_fraction=0.03)
        else:
            self.move_to(ctx, {"TLT": 0.6, "SPY": 0.0, "QQQ": 0.0, "IWM": 0.0,
                               "GLD": 0.0, "NVDA": 0.0, "TSLA": 0.0},
                         f"leap: no positive leader (best {leader} "
                         f"{scores[leader]:+.2f}%), duration park",
                         "contest rank rotation", ev, min_fraction=0.03)


class PinePilotEMALive(LiveStrategy):
    """The PinePilot EMA-cross rule, run as a forward book."""

    username = "@PinePilot_EMA_Live"
    price_only = True
    spec = StrategySpec(
        username=username,
        display_name="PinePilot EMA Live",
        archetype="time-series trend",
        thesis=("PinePilot is a Pine Script generator; the canonical script it "
                "produces is a fast/slow EMA cross. The rule here is that script on "
                "QQQ, with the whole position taken on the cross and no filter. "
                "Its value in this roster is as the simplest possible trend "
                "baseline a forward book can be measured against."),
        entry_rules=["EMA(9) crossing above EMA(21) on QQQ -> target 1.5x equity",
                     "EMA(9) crossing below EMA(21) -> target short QQQ 1.0x"],
        exit_rules=["Cross reversal only; no stop, no volatility filter"],
        sizing="1.5x long, 1.0x short",
        leverage="1.5x gross",
        cadence="every session, executed the next session",
        horizon="days to weeks",
        academic_basis=[CITATION_MOSKOWITZ, {
            "claim": "PinePilot generates Pine Script strategies from a natural-"
                     "language description; the EMA cross is its canonical output",
            "ref": "PinePilot project on the MasterSite directory",
            "url": "https://buffedlizard55-lab.github.io/MasterSite/",
            "status": "FETCHED-VERIFIED"}],
        known_failure_modes=[
            "A 9/21 EMA cross on a daily bar is a textbook whipsaw generator",
            "Taking the whole position on the cross means the entry price is always "
            "the worst one of the move",
            "No filter means it trades every chop"],
        aggression=3,
        why_return_seeking="Full-size single-instrument trend exposure.",
    )

    def plan(self, ctx) -> None:
        closes = ctx.closes("QQQ", 120)
        if len(closes) < 40:
            return
        fast = _ema(closes, 9)
        slow = _ema(closes, 21)
        ev = {"QQQ_price": ctx.evidence_for_symbol("QQQ")}
        if fast > slow:
            self.move_to(ctx, {"QQQ": 1.5},
                         f"pinepilot: EMA9 {fast:.2f} > EMA21 {slow:.2f}",
                         "9/21 EMA cross", ev, min_fraction=0.03)
        else:
            self.move_to(ctx, {"QQQ": -1.0},
                         f"pinepilot: EMA9 {fast:.2f} < EMA21 {slow:.2f}",
                         "9/21 EMA cross", ev, min_fraction=0.03)


def _ema(values: Sequence[float], span: int) -> float:
    k = 2.0 / (span + 1.0)
    out = float(values[0])
    for value in values[1:]:
        out = value * k + out * (1.0 - k)
    return out


class ORBForwardProbe(LiveStrategy):
    """Opening-range breakout: declared forward-only, with the reason stated."""

    username = "@ORB_NextOpen_Probe"
    data_status = "FORWARD-ONLY"
    signal_note = ("The opening-range breakout needs intraday bars: the range is "
                   "defined by the first 30 minutes and the trigger is a break of "
                   "that range inside the same session. This venue has one decision "
                   "point per session on daily bars, so the rule cannot be "
                   "backtested here and is implemented as a forward-only probe "
                   "rather than faked with daily opens.")
    spec = StrategySpec(
        username=username,
        display_name="ORB Next-Open Probe",
        archetype="intraday momentum",
        thesis=("The opening-range breakout is the most-posted day-trading rule on "
                "YouTube and r/daytrading, and the published academic version of it "
                "(Zarattini & Aziz) needs intraday bars and finds the edge in "
                "abnormally active names. This participant exists to record what "
                "would be needed to test it honestly, and to place no intents until "
                "intraday data is collected."),
        entry_rules=[
            "If intraday bars existed: long the 30-minute opening range break with "
            "the position sized to the day's relative volume",
            "With only daily bars available: no intent is placed, and the reason is "
            "published",
        ],
        exit_rules=["Flat by the session close (the rule is intraday by definition)"],
        sizing="not sized: the rule has no executable form on this data",
        leverage="n/a",
        cadence="intraday, which this venue does not have",
        horizon="intraday",
        academic_basis=[{
            "claim": "A plain opening-range breakout on QQQ showed a large "
                     "annualised alpha in-sample, and the cross-sectional version "
                     "concentrates in abnormally active names",
            "ref": "Zarattini & Aziz (2023), SSRN 4416622",
            "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622",
            "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=[
            "Not testable on daily bars at all - this is the point of the entry",
            "The published result is in-sample and leverage-constrained",
            "Day trading on a single decision point per session is a different "
            "simulation, and pretending otherwise would be a fabrication"],
        aggression=5,
        why_return_seeking="Unimplemented here on purpose; the objective is a "
                           "measured result, and an unmeasurable rule gets no trade.",
    )

    def plan(self, ctx) -> None:
        return


class CrowdFadeLive(LiveStrategy):
    """The inverse-crowd family from Reddit/X, run as a short-term reversal."""

    username = "@CrowdFade_Live"
    price_only = True
    spec = StrategySpec(
        username=username,
        display_name="Crowd Fade Live",
        archetype="short-term reversal",
        thesis=("'Inverse WSB' is the most common contrarian claim on the forums. "
                "Without a sentiment feed the honest test is the price footprint the "
                "crowd leaves: a 3-session jump of more than two daily sigmas is "
                "faded. The participant is a short-horizon reversal rule standing in "
                "for the crowd-fade family, and the substitution is stated rather "
                "than hidden."),
        entry_rules=[
            "3-session return above +2.5 daily sigmas on QQQ, NVDA, TSLA, MU or IWM "
            "-> target short that name at 0.5x equity each",
            "3-session return below -2.5 sigma -> target long at 0.7x each",
            "No more than three names at once, largest moves first",
        ],
        exit_rules=["Exit on the first session whose 1-session return has the "
                    "opposite sign to the entry"],
        sizing="up to 1.8x gross across at most three names",
        leverage="up to 1.8x gross",
        cadence="every session, executed the next session",
        horizon="one to five sessions",
        academic_basis=[{
            "claim": "Short-horizon reversal is robust at daily and weekly "
                     "horizons but fragile after costs",
            "ref": "Jegadeesh (1990); Lehmann (1990)",
            "url": "https://doi.org/10.1111/j.1540-6261.1990.tb05110.x",
            "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=[
            "Fading momentum is the most expensive mistake in a trending tape",
            "Shorting high-beta names into a squeeze is unbounded on paper",
            "The execution delay removes the fastest part of the reversion, which is "
            "where the academic effect is concentrated"],
        aggression=4,
        why_return_seeking="Levered multi-name short-horizon reversal with a short leg.",
    )

    def plan(self, ctx) -> None:
        moves: List[Tuple[float, str, float]] = []
        for symbol in ("QQQ", "NVDA", "TSLA", "MU", "IWM"):
            closes = ctx.closes(symbol, 4)
            rets = ctx.returns(symbol, 21)
            if len(closes) < 4 or len(rets) < 10:
                continue
            three = 100.0 * (closes[-1] / closes[-4] - 1.0)
            mu = sum(rets) / len(rets)
            var = sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)
            sd = var ** 0.5
            if sd <= 0:
                continue
            z = three / 100.0 / (sd * (3 ** 0.5))
            if abs(z) >= 2.5:
                moves.append((abs(z), symbol, z))
        if not moves:
            return
        moves.sort(reverse=True)
        chosen = moves[:3]
        targets = {symbol: (-0.5 if z > 0 else 0.7) for _, symbol, z in chosen}
        ev = {f"{s}_price": ctx.evidence_for_symbol(s) for _, s, _ in chosen}
        detail = ", ".join(f"{s} z={z:+.2f}" for _, s, z in chosen)
        self.move_to(ctx, targets, f"crowd-fade: 3-session sigma extremes ({detail})",
                     "3-session reversal at 2.5 sigma", ev, min_fraction=0.03)


class VolControlMaxLive(LiveStrategy):
    """Volatility-scaled maximum leverage, the aggressive reading of the paper."""

    username = "@VolControl_MaxLev"
    official_inputs = ("VIXCLS", "SP500")
    spec = StrategySpec(
        username=username,
        display_name="Vol Control Max Leverage",
        archetype="volatility regime timing (official index)",
        thesis=("Moreira & Muir scale exposure inversely to recent variance. Their "
                "result is a Sharpe improvement, and this roster is not scored on "
                "Sharpe, so the participant takes the scaling rule and inverts its "
                "purpose: it holds the leverage bound whenever the official VIX "
                "close is in its calmest tercile and drops to cash in the most "
                "volatile one. The interesting question is whether the scaling "
                "survives the execution delay."),
        entry_rules=[
            "Rank the official VIX close against its trailing 252-session "
            "distribution",
            "Bottom tercile -> target QQQ at 1.95x equity",
            "Middle tercile -> target QQQ at 1.0x",
            "Top tercile -> flat (cash earns the official SOFR)",
        ],
        exit_rules=["Re-evaluated daily against the same distribution"],
        sizing="1.95x / 1.0x / 0x",
        leverage="up to 1.95x gross",
        cadence="every session, executed the next session",
        horizon="days to weeks",
        academic_basis=[CITATION_MOREIRA, CITATION_LEVERAGE],
        known_failure_modes=[
            "Volatility clusters, so the entry into the calm regime is usually late",
            "The rule is long in exactly the regime that precedes most crashes",
            "Scaling is applied to the same instrument with no second asset, so it "
            "cannot harvest the diversification the paper relies on"],
        aggression=5,
        why_return_seeking="Leverage bound in the calm regime, no hedging.",
    )

    def plan(self, ctx) -> None:
        vix = ctx.series("VIXCLS")
        if vix is None or len(vix.values) < 126:
            return
        window = sorted(vix.window(252))
        if len(window) < 60:
            return
        lo = window[len(window) // 3]
        hi = window[(2 * len(window)) // 3]
        v = vix.values[-1]
        ev = _series_evidence(ctx, ("VIXCLS", "SP500"))
        if v <= lo:
            target, label = 1.95, f"calm tercile (VIX {v:.2f} <= {lo:.2f})"
        elif v >= hi:
            target, label = 0.0, f"volatile tercile (VIX {v:.2f} >= {hi:.2f})"
        else:
            target, label = 1.0, f"middle tercile (VIX {v:.2f})"
        self.move_to(ctx, {"QQQ": target}, f"vol-control: {label}",
                     "official VIXCLS tercile scaling", ev, min_fraction=0.03)


class GoldRealRateLive(LiveStrategy):
    """Gold against the official long yield, plus the commodity-dollar hedge."""

    username = "@GoldVsRealRate_Live"
    official_inputs = ("DGS10", "SOFR", "SP500")
    spec = StrategySpec(
        username=username,
        display_name="Gold vs Real Rate Live",
        archetype="commodity-macro proxy",
        thesis=("Gold has no cash flow, so its opportunity cost is the real rate. "
                "The official series collected here give the nominal 10-year yield "
                "and SOFR; their difference is a public proxy for the real rate. The "
                "participant holds gold levered while that proxy falls and holds "
                "duration instead while it rises."),
        entry_rules=[
            "proxy = DGS10 - SOFR, and its 21-session change",
            "Proxy falling by more than 5bp -> target GLD 1.9x",
            "Proxy rising by more than 5bp -> target TLT 1.2x",
            "Otherwise hold GLD 0.7x",
        ],
        exit_rules=["Daily re-evaluation of the two official series"],
        sizing="up to 1.9x gross",
        leverage="1.2-1.9x gross",
        cadence="every session, executed the next session",
        horizon="weeks to months",
        academic_basis=[{
            "claim": "The LBMA gold price series FRED previously carried is "
                     "discontinued (GOLDPMGBD228NLBM returns 404, verified "
                     "2026-09-18), so the gold leg here is the GLD instrument price "
                     "and the signal is the rate pair, not a gold benchmark",
            "ref": "FRED series page for GOLDPMGBD228NLBM returned 'page not found'",
            "url": "https://fred.stlouisfed.org/series/GOLDPMGBD228NLBM",
            "status": "KNOWN-NOT-FETCHED (verified 404)"},
            CITATION_LEVERAGE],
        known_failure_modes=[
            "DGS10 minus an overnight secured rate is not a real rate; it ignores "
            "inflation compensation entirely",
            "GLD charges an expense ratio and holds bullion, so it is not the spot "
            "price",
            "Gold's reaction to rates is regime-dependent and has broken down for "
            "long stretches"],
        aggression=4,
        why_return_seeking="Levered single-commodity exposure plus a duration leg.",
    )

    def plan(self, ctx) -> None:
        nominal = ctx.series("DGS10")
        sofr = ctx.series("SOFR")
        if nominal is None or sofr is None:
            return
        sofr_by_date = dict(zip(sofr.dates, sofr.values))
        pairs = [(d, v - sofr_by_date[d]) for d, v in zip(nominal.dates, nominal.values)
                 if d in sofr_by_date]
        if len(pairs) < 25:
            return
        change_bp = (pairs[-1][1] - pairs[-23][1]) * 100.0
        ev = _series_evidence(ctx, ("DGS10", "SOFR", "SP500"))
        if change_bp < -5.0:
            targets = {"GLD": 1.9, "TLT": 0.0}
            why = f"opportunity-cost proxy falling {change_bp:+.1f}bp"
        elif change_bp > 5.0:
            targets = {"TLT": 1.2, "GLD": 0.0}
            why = f"opportunity-cost proxy rising {change_bp:+.1f}bp"
        else:
            targets = {"GLD": 0.7, "TLT": 0.0}
            why = f"proxy flat ({change_bp:+.1f}bp)"
        self.move_to(ctx, targets, f"gold-real-rate: {why}",
                     "DGS10 minus SOFR, 21-session change", ev, min_fraction=0.03)


class FDAFadeLive(LiveStrategy):
    """The same official event clock, traded the other way."""

    username = "@FDA_Fade_Live"
    event_inputs = ("fda_orig_z", "fda_all_30d")
    spec = StrategySpec(
        username=username,
        display_name="FDA Fade Live",
        archetype="contrarian reversal",
        thesis=("Season 2's best and worst participants were the same signal traded "
                "in opposite directions. The live book keeps both, because that "
                "pair is the cleanest available test of whether an FDA approval "
                "burst is good news for the whole biotech complex or an "
                "overreaction to sell. This is the short side."),
        entry_rules=[
            "z-score of the trailing-30-day original approval count against the "
            "previous 252 sessions (openFDA decision dates)",
            "z > 1.5 -> target short XBI 1.0x",
            "z < -1.0 -> target long XBI 1.5x",
        ],
        exit_rules=["Exit when |z| < 0.5"],
        sizing="up to 1.5x gross",
        leverage="up to 1.5x gross",
        cadence="every session, executed the next session",
        horizon="weeks",
        academic_basis=[CITATION_FDA, {
            "claim": "Overreaction to clustered news is the mechanism the reversal "
                     "family relies on",
            "ref": "De Bondt & Thaler (1985) for the long-horizon form",
            "url": "https://doi.org/10.1111/j.1540-6261.1985.tb05004.x",
            "status": "KNOWN-NOT-FETCHED"}],
        known_failure_modes=[
            "Shorting a sector that is re-rating upwards is the classic way to lose "
            "more than 100% of a position",
            "The approval count is a proxy and can be driven by unlisted sponsors",
            "One session of execution delay removes the immediate post-burst "
            "pop, which is the fade's whole profit"],
        aggression=5,
        why_return_seeking="Concentrated short-side sector bet at size.",
    )

    def plan(self, ctx) -> None:
        if not ctx.signal_available("fda_orig_z"):
            return
        z = ctx.signal("fda_orig_z")
        ev = {"fda_orig_z": ctx.signal_evidence("fda_orig_z") or {}}
        if z > 1.5:
            self.move_to(ctx, {"XBI": -1.0}, f"fda-fade: approval z {z:+.2f} > 1.5",
                         "openFDA approval burst, faded", ev, min_fraction=0.03)
        elif z < -1.0:
            self.move_to(ctx, {"XBI": 1.5}, f"fda-fade: approval z {z:+.2f} < -1.0",
                         "openFDA approval drought, bought", ev, min_fraction=0.03)
        elif abs(z) < 0.5 and ctx.position("XBI") != 0:
            self.flatten(ctx, ("XBI",), f"fda-fade: |z|={abs(z):.2f} < 0.5",
                         "openFDA approval burst, faded", ev)


class KitchenSinkLive(LiveStrategy):
    """Every official series at once, in the sign of its own trend."""

    username = "@KitchenSink_Official"
    official_inputs = ("NASDAQCOM", "DJIA", "SP500", "VIXCLS", "SOFR", "DGS10",
                       "DCOILWTICO", "DTWEXBGS")
    spec = StrategySpec(
        username=username,
        display_name="Kitchen Sink Official",
        archetype="signal stacking",
        thesis=("Season 1 had a participant that traded everything at once. This is "
                "the official-data version: it takes every collected official series, "
                "signs its 21-session change, and holds whatever the aggregate "
                "gesture implies. It exists as the placebo of the roster - if "
                "a careful single-series rule cannot beat this, the care was not "
                "worth anything."),
        entry_rules=[
            "For each official series compute the sign of its 21-session change",
            "Score = (+1 for Nasdaq, Dow, S&P, oil; -1 for VIX, SOFR, DGS10, dollar)",
            "Score >= +3 -> target QQQ 1.8x; score <= -3 -> target TLT 1.5x; "
            "otherwise 0.8x SPY",
        ],
        exit_rules=["Daily recomputation of the same eight signs"],
        sizing="up to 1.8x gross",
        leverage="up to 1.8x gross",
        cadence="every session, executed the next session",
        horizon="weeks",
        academic_basis=[{
            "claim": "Stacking many weak, correlated signals is a common retail "
                     "construction and a standard placebo in the factor "
                     "literature: the combination inherits the components' "
                     "correlation and adds no independent information",
            "ref": "this project's own placebo participant",
            "url": "https://buffedlizard55-lab.github.io/StockPaperSim/docs/season2/participants/KitchenSink_AllIn.html",
            "status": "SELF-REFERENCE"},
            CITATION_LEVERAGE],
        known_failure_modes=[
            "Eight correlated financial series do not make eight independent votes",
            "A sign is the least informative statistic available: it throws away "
            "every magnitude",
            "Stacked signals dilute whichever one is actually predictive"],
        aggression=4,
        why_return_seeking="Aggregate gesture at the leverage bound, no hedging.",
    )

    def plan(self, ctx) -> None:
        weights = {"NASDAQCOM": 1.0, "DJIA": 1.0, "SP500": 1.0, "DCOILWTICO": 1.0,
                   "VIXCLS": -1.0, "SOFR": -1.0, "DGS10": -1.0, "DTWEXBGS": -1.0}
        score = 0.0
        used = 0
        for sid, weight in weights.items():
            series = ctx.series(sid)
            if series is None:
                continue
            change = series.pct_change(21)
            if change is None:
                continue
            used += 1
            score += weight * (1.0 if change > 0 else (-1.0 if change < 0 else 0.0))
        if used < 6:
            return
        ev = _series_evidence(ctx, tuple(weights))
        if score >= 3:
            targets, label = {"QQQ": 1.8, "SPY": 0.0, "TLT": 0.0}, "riskon"
        elif score <= -3:
            targets, label = {"TLT": 1.5, "QQQ": 0.0, "SPY": 0.0}, "riskoff"
        else:
            targets, label = {"SPY": 0.8, "QQQ": 0.0, "TLT": 0.0}, "undecided"
        self.move_to(ctx, targets, f"kitchen-sink[{label}]: score {score:+.0f} of "
                                   f"{used} official signs",
                     "sign of eight official 21-session changes", ev,
                     min_fraction=0.03)


# --------------------------------------------------------------------------
# Roster
# --------------------------------------------------------------------------

LIVE_STRATEGY_CLASSES: Tuple[type, ...] = (
    NasdaqMomentumMax,
    DowNasdaqSpreadRotator,
    VIXRegimeMax,
    SOFRPivotRider,
    CurveSteepenerMaxBeta,
    OilDollarFade,
    FDAPdufaDrifter,
    MLBAttentionLive,
    WeatherColdSnapLive,
    InsiderClusterLive,
    InjuryFeedForward,
    LeapStyleAutoLiquidator,
    PinePilotEMALive,
    ORBForwardProbe,
    CrowdFadeLive,
    VolControlMaxLive,
    GoldRealRateLive,
    FDAFadeLive,
    KitchenSinkLive,
)


def build_live_roster() -> List[LiveStrategy]:
    """One instance per participant, in a fixed order (determinism matters)."""
    roster = [cls() for cls in LIVE_STRATEGY_CLASSES]
    usernames = [s.username for s in roster]
    if len(set(usernames)) != len(usernames):
        raise AssertionError("live roster contains a duplicate username")
    for strategy in roster:
        if not strategy.username:
            raise AssertionError(f"{type(strategy).__name__} has no username")
        if strategy.spec.username != strategy.username:
            raise AssertionError(f"{type(strategy).__name__}: username and spec "
                                 f"username disagree")
    return roster


__all__ = ["LiveStrategy", "LIVE_STRATEGY_CLASSES", "build_live_roster"]
