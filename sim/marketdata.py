"""Market data: the real-anchored Season 1 replay plus live provider adapters.

Two ways to obtain daily OHLCV for a competition:

1. ``build_replay()`` - deterministic, offline.  Uses the *real* FRED S&P 500
   and CBOE VIX daily observations that are bundled in ``data/real/fred/``,
   the *real* SPY and AAPL anchors bundled in ``data/real/yahoo/``, and the
   declared scenario parameters in :mod:`sim.universe` for everything else.
   Every generated bar is reproducible from ``(seed, config fingerprint)``.

2. ``provider_from_name()`` - live/historical feeds (Yahoo chart API, Stooq,
   Alpaca, Polygon.io, Finnhub, Tiingo).  These are the paths to use for
   genuine real-time prices.  They cannot be exercised from this sandbox
   because outbound HTTPS is restricted to a small allow-list; each adapter
   raises :class:`ProviderUnavailable` with an actionable message instead of
   silently returning fabricated data.
"""

from __future__ import annotations

import json
import math
import os
import random
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import config
from .calendar import TradingCalendar, REPO_ROOT
from .universe import Instrument, build_universe


class ProviderUnavailable(RuntimeError):
    """Raised when a live feed cannot be reached or is not configured."""


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


# --------------------------------------------------------------------------
# Replay construction
# --------------------------------------------------------------------------

# Real month-end pairs used to fit the SPY / S&P 500 index ratio.
# Left column: real SPY monthly closes from the bundled vendor snapshot.
# Right column: real FRED S&P 500 closes on the corresponding month-end
# session.  Both are real observations; the ratio between them is derived.
_SPY_MONTH_END_CLOSES = [682.06, 683.39, 681.92, 691.97, 685.99, 650.34,
                         718.66, 756.48, 746.77, 747.03, 767.05, 754.05]
_SPY_MONTH_END_DATES = ["2025-10-31", "2025-11-28", "2025-12-31", "2026-01-30",
                        "2026-02-27", "2026-03-31", "2026-04-30", "2026-05-29",
                        "2026-06-30", "2026-07-31", "2026-08-31", "2026-09-16"]


def fit_spy_ratio(calendar: TradingCalendar) -> Tuple[float, float, List[dict]]:
    """Least-squares fit of S&P 500 index level / SPY price.

    Returns ``(ratio, max_abs_pct_error, per_pair_rows)``.  The error column
    is published in the run manifest so the derived SPY series can be audited
    against the twelve real monthly closes it was fitted on.
    """
    rows = []
    num = den = 0.0
    for date, spy_close in zip(_SPY_MONTH_END_DATES, _SPY_MONTH_END_CLOSES):
        if date not in calendar.spx:
            continue
        idx = calendar.spx[date]
        num += idx * spy_close
        den += spy_close * spy_close
        rows.append({"date": date, "spy_close_real": spy_close,
                     "spx_close_real": idx, "ratio": idx / spy_close})
    ratio = num / den if den else 10.028
    worst = 0.0
    for r in rows:
        implied = r["spx_close_real"] / ratio
        r["spy_close_implied"] = round(implied, 2)
        r["abs_pct_error"] = round(100.0 * abs(implied - r["spy_close_real"])
                                   / r["spy_close_real"], 4)
        worst = max(worst, r["abs_pct_error"])
    return ratio, worst, rows


@dataclass
class ReplayDiagnostics:
    ratio: float = 0.0
    ratio_max_abs_pct_error: float = 0.0
    ratio_pairs: List[dict] = None  # type: ignore[assignment]
    spx_annualised_vol: float = 0.0
    spx_total_return_pct: float = 0.0
    vix_mean: float = 0.0
    vix_max: float = 0.0
    simulated_monthly_range_check: Dict[str, float] = None  # type: ignore[assignment]


class MarketData:
    """Everything the simulation is allowed to see, indexed by session."""

    def __init__(self, calendar: TradingCalendar, instruments: Sequence[Instrument],
                 bars: Dict[str, List[Bar]], spx: Sequence[float],
                 vix: Sequence[float], diagnostics: dict, source: str,
                 warmup_days: int = 0, warmup_dates: Optional[List[str]] = None) -> None:
        self.calendar = calendar
        self.warmup_days = warmup_days
        self.warmup_dates = list(warmup_dates or [])
        self.dates: List[str] = list(self.warmup_dates) + list(calendar.dates)
        self.instruments = {i.symbol: i for i in instruments}
        self.symbols: List[str] = [i.symbol for i in instruments]
        self.bars = bars
        self.spx = list(spx)
        self.vix = list(vix)
        self.diagnostics = diagnostics
        self.source = source
        self._closes = {s: [b.close for b in bars[s]] for s in self.symbols}
        self._vols = {s: [b.volume for b in bars[s]] for s in self.symbols}
        self._rets: Dict[str, List[float]] = {}
        for s in self.symbols:
            c = self._closes[s]
            self._rets[s] = [0.0] + [c[i] / c[i - 1] - 1.0 for i in range(1, len(c))]
        self.market_ret = [0.0] + [spx[i] / spx[i - 1] - 1.0 for i in range(1, len(spx))]
        # Index of the first *competition* session inside the bar arrays.
        self.first_competition_index = warmup_days
        if len(self.dates) != len(self.spx) or len(self.dates) != len(self.vix):
            raise AssertionError("market data arrays are misaligned")
        for sym, series in self.bars.items():
            if len(series) != len(self.dates):
                raise AssertionError(f"{sym}: {len(series)} bars vs {len(self.dates)} dates")

    # -- no-look-ahead accessors -----------------------------------------
    def history_closes(self, symbol: str, t: int, n: Optional[int] = None) -> List[float]:
        """Closes strictly *before* session ``t`` (never includes day ``t``)."""
        start = 0 if n is None else max(0, t - n)
        return self._closes[symbol][start:t]

    def history_returns(self, symbol: str, t: int, n: Optional[int] = None) -> List[float]:
        start = 1 if n is None else max(1, t - n)
        return self._rets[symbol][start:t]

    def history_volume(self, symbol: str, t: int, n: Optional[int] = None) -> List[int]:
        start = 0 if n is None else max(0, t - n)
        return self._vols[symbol][start:t]

    def adv(self, symbol: str, t: int, window: int = 63) -> float:
        """Average daily volume over up to ``window`` *prior* sessions."""
        vols = self.history_volume(symbol, t, window)
        if not vols:
            return float(self.instruments[symbol].adv_shares)
        return sum(vols) / len(vols)

    def realised_sigma_daily(self, symbol: str, t: int, window: int = 21) -> float:
        rets = self.history_returns(symbol, t, window)
        if len(rets) < 5:
            rets = self.history_returns(symbol, t)
        if len(rets) < 2:
            return self.instruments[symbol].sigma_idio_annual / math.sqrt(252.0)
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        return max(math.sqrt(var), 1e-5)

    def market_sigma_daily(self, t: int, window: int = 21) -> float:
        rets = self.market_ret[max(1, t - window):t]
        if len(rets) < 2:
            rets = self.market_ret[1:t] or [0.01]
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        return max(math.sqrt(var), 1e-5)

    def bar(self, symbol: str, t: int) -> Bar:
        return self.bars[symbol][t]

    def vix_regime_factor(self, t: int) -> float:
        """Real VIX today divided by the window's mean VIX (>= 0.25, <= 4)."""
        mean = sum(self.vix) / len(self.vix)
        return max(0.25, min(4.0, self.vix[t] / mean))

    def __len__(self) -> int:
        return len(self.dates)


_FAT_TAIL_P = 0.04
_FAT_TAIL_SCALE = 2.6
_FAT_TAIL_STD = math.sqrt((1 - _FAT_TAIL_P) * 1.0 + _FAT_TAIL_P * _FAT_TAIL_SCALE ** 2)


def _gauss(rng: random.Random) -> float:
    """Standard normal with a fat-tail mixture.

    SIM CHOICE: 4% of draws are scaled by 2.6 to reproduce the excess
    kurtosis that is a well-documented stylised fact of daily equity returns.
      SOURCE: https://doi.org/10.1080/713665679
              Cont (2001), "Empirical properties of asset returns",
              Quantitative Finance 1(2):223-236.
    """
    z = rng.gauss(0.0, 1.0)
    if rng.random() < _FAT_TAIL_P:
        z *= _FAT_TAIL_SCALE
    # Renormalise so the mixture has unit variance and the declared sigma is
    # the true sigma of the process.
    return z / _FAT_TAIL_STD


def _warmup_business_days(start: str, count: int) -> List[str]:
    """``count`` weekdays immediately before ``start`` (indicator warm-up only).

    SIM CHOICE: warm-up sessions are plain weekdays; holiday exactness does not
    matter because no competition trade is ever executed on a warm-up day.
    """
    import datetime as dt
    day = dt.date.fromisoformat(start) - dt.timedelta(days=1)
    out: List[str] = []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= dt.timedelta(days=1)
    return sorted(out)


def build_replay(cfg: config.CompetitionConfig, seed: Optional[int] = None,
                 warmup_days: int = 126) -> MarketData:
    """Build the deterministic, real-anchored Season 1 replay.

    ``warmup_days`` sessions of *simulated* history are prepended so that
    lookback indicators (63-day momentum, 50-day EMA, 60-day pair spreads) are
    fully warmed up on the first competition session.  Warm-up bars are never
    traded on and are flagged as simulated in the run manifest.
    """
    seed = cfg.seed if seed is None else seed
    cal = TradingCalendar(cfg.start, cfg.end)
    instruments = build_universe()
    n = len(cal)
    spx_real = cal.spx_series()
    vix_real = cal.vix_series()
    vix_mean = sum(vix_real) / len(vix_real)
    ratio, ratio_err, ratio_rows = fit_spy_ratio(cal)

    real_logret = [math.log(spx_real[i] / spx_real[i - 1]) for i in range(1, n)]
    diag = ReplayDiagnostics(
        ratio=ratio, ratio_max_abs_pct_error=ratio_err, ratio_pairs=ratio_rows,
        spx_annualised_vol=math.sqrt(252.0) * _stdev(real_logret),
        spx_total_return_pct=100.0 * (spx_real[-1] / spx_real[0] - 1.0),
        vix_mean=sum(vix_real) / len(vix_real), vix_max=max(vix_real),
    )
    warm_dates = _warmup_business_days(cfg.start, warmup_days)
    warm_rng = random.Random(f"{seed}:warmup:market")
    mu_m = sum(real_logret) / len(real_logret)
    sd_m = _stdev(real_logret)
    warm_logret = [warm_rng.gauss(mu_m, sd_m) for _ in warm_dates]
    # Synthetic warm-up index path, scaled backwards from the real first close.
    warm_spx: List[float] = []
    level = spx_real[0]
    for lr in reversed(warm_logret):
        level = level / math.exp(lr)
        warm_spx.append(level)
    warm_spx.reverse()
    spx = warm_spx + spx_real
    vix = [vix_mean * (0.9 + 0.2 * warm_rng.random()) for _ in warm_dates] + vix_real
    n_all = len(spx)
    market_logret = [0.0] + [math.log(spx[i] / spx[i - 1]) for i in range(1, n_all)]
    warm_offset = len(warm_dates)
    bars: Dict[str, List[Bar]] = {}

    all_dates = warm_dates + cal.dates
    for inst in instruments:
        rng = random.Random(f"{seed}:{cfg.fingerprint()}:{inst.symbol}")
        if inst.symbol == "SPY":
            # Derived from the real index level and the fitted real ratio.
            closes = [spx[i] / ratio for i in range(n_all)]
            sigma_d = max(_stdev(market_logret[1:]), 1e-4)
        else:
            sigma_idio_d = inst.sigma_idio_annual / math.sqrt(252.0)
            # Dividend bookkeeping: declared cash dividends are paid to the
            # holder (see portfolio.pay_dividend).  To keep the *total* return
            # equal to the declared beta*market + alpha, the declared window
            # dividend yield is removed from the price drift.  Endpoint-anchored
            # names (AAPL) are exempt: their anchor is a real unadjusted close,
            # which already reflects the real ex-date price drops, so the cash
            # dividend is genuine additional total return.
            div_drag = 0.0
            if not inst.price_end_anchor and inst.dividends:
                window_ex = {d["ex_date"] for d in inst.dividends
                             if cfg.start <= d["ex_date"] <= cfg.end}
                total_div = sum(d["amount"] for d in inst.dividends
                                if d["ex_date"] in window_ex)
                div_drag = total_div / inst.price_start
            # Endpoint anchoring: when a real end price is known, the path's
            # deterministic drift is solved so that the final simulated close
            # equals the real observed close.  Daily variation stays simulated.
            base_drift = inst.alpha_annual / 252.0 - div_drag / n
            x: List[float] = []
            for t in range(n_all):
                vol_scale = vix[t] / vix_mean
                shock = sigma_idio_d * vol_scale * _gauss(rng)
                x.append(inst.beta * market_logret[t] + shock)
            # Neutralise the net move of the warm-up window so the final warm-up
            # close equals the declared pre-season price level.
            if warm_offset:
                warm_mean = sum(x[:warm_offset]) / warm_offset
                x = [xi - warm_mean for xi in x[:warm_offset]] + x[warm_offset:]
            if inst.price_end_anchor:
                # Real endpoint anchor: solve the constant daily drift over the
                # competition window that lands the final close on the real
                # observed close.  Day-to-day variation stays simulated.
                target = math.log(inst.price_end_anchor / inst.price_start)
                residual = target - sum(x[warm_offset:])
                base_drift = residual / max(1, n)
            x = x[:warm_offset] + [xi + base_drift for xi in x[warm_offset:]]
            closes = []
            p = inst.price_start
            for t in range(n_all):
                p = p * math.exp(x[t])
                closes.append(p)
            sigma_d = max(inst.beta * _stdev(market_logret[1:]), sigma_idio_d)

        day_bars: List[Bar] = []
        prev_close = inst.price_start
        for t in range(n_all):
            c = closes[t]
            # Overnight gap: 45% of daily variance is placed in the overnight
            # leg (SIM CHOICE, informed by the documented split between
            # overnight and intraday returns).
            sigma_overnight = OVERNIGHT_VARIANCE_SHARE ** 0.5 * sigma_d
            gap = rng.gauss(0.0, sigma_overnight)
            o = prev_close * math.exp(gap)
            body = abs(c - o)
            wick = abs(_gauss(rng)) * sigma_d * c * WICK_COEFF
            hi = max(o, c) + wick
            lo = min(o, c) - wick
            lo = max(lo, config.minimum_tick(c) * 2)
            if lo >= min(o, c):
                lo = min(o, c) * (1 - 0.15 * sigma_d)
            if hi <= max(o, c):
                hi = max(o, c) * (1 + 0.15 * sigma_d)
            # Volume: log-normal around declared ADV, amplified by |return|.
            r_t = (c / prev_close - 1.0) if prev_close else 0.0
            vol_mult = math.exp(rng.gauss(-0.5 * 0.35 ** 2, 0.35))
            vol_mult *= 0.45 + 0.55 * min(abs(r_t) / max(sigma_d, 1e-6), 3.0)
            if t >= warm_offset and cal.sessions[t - warm_offset].is_early_close:
                vol_mult *= 0.55
            v = int(inst.adv_shares * vol_mult)
            day_bars.append(Bar(all_dates[t], round(o, 4), round(hi, 4),
                                round(lo, 4), round(c, 4), max(v, 1000)))
            prev_close = c
        bars[inst.symbol] = day_bars

    # Audit: compare simulated SPY monthly ranges against the twelve real ones.
    comp_bars = {s: bs[warm_offset:] for s, bs in bars.items()}
    diag.simulated_monthly_range_check = _monthly_range_audit(comp_bars["SPY"], cal)

    prov = cal.provenance()
    prov.update({
        "mode": "real-anchored-replay",
        "seed": seed,
        "config_fingerprint": cfg.fingerprint(),
        "spy_index_ratio_fitted": round(ratio, 5),
        "spy_ratio_max_abs_pct_error_vs_real_monthly_closes": round(ratio_err, 4),
        "spy_ratio_pairs": ratio_rows,
        "spx_total_return_pct_real": round(diag.spx_total_return_pct, 3),
        "spx_annualised_vol_real": round(diag.spx_annualised_vol, 4),
        "vix_mean_real": round(diag.vix_mean, 3),
        "vix_max_real": round(diag.vix_max, 3),
        "monthly_range_audit_spy": diag.simulated_monthly_range_check,
        "single_name_daily_ohlcv": "SIMULATED from real market factor + real VIX regime + declared scenario parameters",
        "real_anchors": ["SPY.price_start", "SPY.price_end_anchor", "AAPL.price_start",
                          "AAPL.price_end_anchor", "AAPL.dividends", "SP500 daily closes",
                          "VIXCLS daily closes"],
    })
    prov["warmup_sessions_simulated"] = warm_offset
    prov["warmup_dates"] = [warm_dates[0], warm_dates[-1]] if warm_dates else []
    return MarketData(cal, instruments, bars, spx, vix, prov, "replay",
                      warmup_days=warm_offset, warmup_dates=warm_dates)


def _stdev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


# Share of daily variance placed in the overnight (close -> open) leg.
# CALIBRATION TENSION (flagged as IR-06): a smaller overnight share fits the
# twelve real SPY monthly high-low ranges better (best grid point was
# wick=0.25 / overnight=0.05, mean|diff| 0.40 pp) but understates the share
# of daily variance that is documented to arrive overnight.  We keep 0.15 as
# a compromise; the resulting mean absolute difference against the real SPY
# monthly ranges is 0.52 percentage points and is reported in every manifest.
OVERNIGHT_VARIANCE_SHARE = 0.15

# Wick size beyond the open/close body, in units of sigma_daily * close.
# Both constants were calibrated against the twelve REAL SPY monthly
# high-low ranges in data/real/yahoo/SPY_monthly_1y.json; the residual is
# reported in every run manifest under
# diagnostics.monthly_range_audit_spy._mean_abs_diff_pct_points.
WICK_COEFF = 0.20


def _monthly_range_audit(spy_bars: List[Bar], cal: TradingCalendar) -> dict:
    """Compare simulated SPY monthly (high-low)/close against the real ones."""
    real_ranges = {
        "2025-10": (689.70 - 652.84) / 682.06, "2025-11": (685.80 - 650.85) / 683.39,
        "2025-12": (691.66 - 671.20) / 681.92, "2026-01": (697.84 - 676.57) / 691.97,
        "2026-02": (697.14 - 675.78) / 685.99, "2026-03": (688.62 - 629.28) / 650.34,
        "2026-04": (719.79 - 645.11) / 718.66, "2026-05": (758.08 - 714.99) / 756.48,
        "2026-06": (760.40 - 716.58) / 746.77, "2026-07": (755.58 - 729.10) / 747.03,
        "2026-08": (779.37 - 748.80) / 767.05,
    }
    sim: Dict[str, List[float]] = {}
    for b in spy_bars:
        key = b.date[:7]
        sim.setdefault(key, []).append((b.high - b.low) / b.close)
    months: Dict[str, dict] = {}
    errs: List[float] = []
    for key, real in sorted(real_ranges.items()):
        got = sim.get(key)
        if not got:
            continue
        # A monthly high-low range is not the sum of daily ranges; the
        # comparable statistic is (month high - month low) / month-end close.
        hi = max(b.high for b in spy_bars if b.date.startswith(key))
        lo = min(b.low for b in spy_bars if b.date.startswith(key))
        cl = [b.close for b in spy_bars if b.date.startswith(key)][-1]
        simulated = (hi - lo) / cl
        months[key] = {"real_pct": round(100 * real, 3),
                       "simulated_pct": round(100 * simulated, 3),
                       "diff_pct_points": round(100 * (simulated - real), 3)}
        errs.append(abs(simulated - real))
    return {
        "months": months,
        "n_months": len(months),
        "mean_abs_diff_pp": round(100 * (sum(errs) / len(errs)), 3) if errs else None,
        "max_abs_diff_pp": round(100 * max(errs), 3) if errs else None,
        "real_source": "data/real/yahoo/SPY_monthly_1y.json (Yahoo Finance chart "
                       "API v8, fetched 2026-09-17)",
        "statistic": "(month high - month low) / month-end close, in percent",
        "calibration_note": "WICK_COEFF and OVERNIGHT_VARIANCE_SHARE were tuned on "
                            "this audit; see the IR-06 comment block above.",
    }


# --------------------------------------------------------------------------
# Live / historical providers (the path to genuine real-time prices)
# --------------------------------------------------------------------------

Transport = Callable[[str, Dict[str, str]], bytes]


def _default_transport(url: str, headers: Dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        return resp.read()


class DataProvider:
    """Base class for historical/daily bar providers."""

    name = "base"
    docs_url = ""
    key_env = ""
    realtime = False
    notes = ""

    def __init__(self, transport: Optional[Transport] = None, api_key: str = "") -> None:
        self.transport = transport or _default_transport
        self.api_key = api_key or (os.environ.get(self.key_env, "") if self.key_env else "")

    def require_key(self) -> str:
        if not self.api_key:
            raise ProviderUnavailable(
                f"{self.name}: no API key. Set {self.key_env} or pass api_key=. "
                f"Docs: {self.docs_url}")
        return self.api_key

    def _get(self, url: str, headers: Optional[Dict[str, str]] = None) -> bytes:
        try:
            return self.transport(url, headers or {})
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise ProviderUnavailable(f"{self.name}: HTTP {exc.code} for {url}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ProviderUnavailable(
                f"{self.name}: network unreachable ({exc}). This sandbox blocks "
                f"outbound HTTPS except for a small allow-list; run this command "
                f"on a machine with internet access, or use the bundled replay "
                f"(python -m sim.cli run --source replay).") from exc

    def fetch_daily(self, symbol: str, start: str, end: str) -> List[Bar]:
        raise NotImplementedError


class YahooChartProvider(DataProvider):
    """Yahoo Finance v8 chart endpoint (no key, but see terms of use).

    Verified working from this project's research environment on 2026-09-17:
    GET https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1mo&range=1y
    The sibling v7 ``/quote`` endpoint returned ``{"code":"Unauthorized"}``
    for the same session, so quote-style lookups are NOT used anywhere here.
      SOURCE: https://query1.finance.yahoo.com/v8/finance/chart/SPY
      IRREGULARITY: research/IRREGULARITIES.json IR-02 (redistribution/terms).
    """

    name = "yahoo"
    docs_url = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    realtime = False
    notes = ("Unofficial endpoint. Responses are not covered by a data licence; "
             "check Yahoo's terms of use before redistributing.")

    def fetch_daily(self, symbol: str, start: str, end: str) -> List[Bar]:
        import datetime as dt
        p1 = int(dt.datetime.strptime(start, "%Y-%m-%d").replace(
            tzinfo=dt.timezone.utc).timestamp())
        p2 = int(dt.datetime.strptime(end, "%Y-%m-%d").replace(
            tzinfo=dt.timezone.utc).timestamp())
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
               f"?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit")
        raw = self._get(url, {"User-Agent": "StockPaperSim/1.0 (research)"})
        payload = json.loads(raw.decode("utf-8"))
        result = (payload.get("chart") or {}).get("result")
        if not result:
            err = (payload.get("chart") or {}).get("error")
            raise ProviderUnavailable(f"yahoo: no data for {symbol}: {err}")
        res = result[0]
        ts = res.get("timestamp") or []
        q = (res.get("indicators") or {}).get("quote") or [{}]
        q = q[0]
        out: List[Bar] = []
        for i, epoch in enumerate(ts):
            c = q.get("close", [None])[i]
            if c is None:
                continue
            out.append(Bar(
                date=dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%d"),
                open=float(q["open"][i]), high=float(q["high"][i]),
                low=float(q["low"][i]), close=float(c),
                volume=int(q.get("volume", [0])[i] or 0),
            ))
        return out


class StooqProvider(DataProvider):
    """Stooq daily CSV (no key).

    NOTE: a request from this project's research environment on 2026-09-17 was
    answered with ``Access denied``, i.e. the host rejects this client.  The
    adapter is kept because it is a common no-key source, but it must be
    re-verified before use.
      SOURCE: https://stooq.com/q/d/l/?s=aapl.us&i=d
    """

    name = "stooq"
    docs_url = "https://stooq.com/db/h/"
    realtime = False
    notes = "End-of-day CSV; the host returned 'Access denied' on 2026-09-17."

    def fetch_daily(self, symbol: str, start: str, end: str) -> List[Bar]:
        s = symbol.lower() + (".us" if "." not in symbol else "")
        url = (f"https://stooq.com/q/d/l/?s={s}"
               f"&d1={start.replace('-', '')}&d2={end.replace('-', '')}&i=d")
        raw = self._get(url, {"User-Agent": "StockPaperSim/1.0"}).decode("utf-8")
        if raw.strip().lower().startswith("access denied") or "<html" in raw[:200].lower():
            raise ProviderUnavailable("stooq: host refused the request (Access denied)")
        out: List[Bar] = []
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        for ln in lines[1:]:
            parts = ln.split(",")
            if len(parts) < 6:
                continue
            out.append(Bar(parts[0], float(parts[1]), float(parts[2]),
                           float(parts[3]), float(parts[4]), int(float(parts[5]))))
        return out


class AlpacaProvider(DataProvider):
    """Alpaca Markets Data API (free IEX feed with an account, paid SIP feed).

      SOURCE: https://docs.alpaca.markets/docs/market-data
      SOURCE: https://docs.alpaca.markets/reference/stockbars
    """

    name = "alpaca"
    docs_url = "https://docs.alpaca.markets/reference/stockbars"
    key_env = "ALPACA_API_KEY"
    realtime = True
    notes = ("Free plan streams the IEX venue only; the paid 'Alpaca Market "
             "Data' plan streams the full SIP consolidated feed. A paper-trading "
             "account is free and is the natural execution venue for this "
             "project. Requires ALPACA_API_KEY and ALPACA_API_SECRET.")

    def fetch_daily(self, symbol: str, start: str, end: str) -> List[Bar]:
        secret = os.environ.get("ALPACA_API_SECRET", "")
        feed = os.environ.get("ALPACA_FEED", "iex")
        url = ("https://data.alpaca.markets/v2/stocks/bars"
               f"?symbols={symbol}&timeframe=1Day&start={start}T04:00:00Z"
               f"&end={end}T20:00:00Z&feed={feed}")
        raw = self._get(url, {"APCA-API-KEY-ID": self.require_key(),
                              "APCA-API-SECRET-KEY": secret})
        payload = json.loads(raw.decode("utf-8"))
        out: List[Bar] = []
        for b in (payload.get("bars") or {}).get(symbol, []):
            out.append(Bar(date=b["t"][:10], open=float(b["o"]), high=float(b["h"]),
                           low=float(b["l"]), close=float(b["c"]),
                           volume=int(b["v"])))
        return out


class PolygonProvider(DataProvider):
    """Polygon.io aggregates (REST). Free tier is end-of-day/delayed.

      SOURCE: https://polygon.io/docs/rest/stocks/aggregates/daily-aggregate
      SOURCE: https://polygon.io/pricing (plan limits)
    """

    name = "polygon"
    docs_url = "https://polygon.io/docs/rest/stocks/aggregates/daily-aggregate"
    key_env = "POLYGON_API_KEY"
    realtime = True
    notes = ("Free tier: 5 API calls/minute, end-of-day US stock data only. "
             "Real-time consolidated-tape data requires a paid Stocks plan.")

    def fetch_daily(self, symbol: str, start: str, end: str) -> List[Bar]:
        key = self.require_key()
        url = (f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/"
               f"{start}/{end}?adjusted=true&sort=asc&limit=50000&apiKey={key}")
        payload = json.loads(self._get(url, {}).decode("utf-8"))
        out: List[Bar] = []
        import datetime as dt
        for r in payload.get("results") or []:
            day = dt.datetime.fromtimestamp(r["t"] / 1000.0, dt.timezone.utc)
            out.append(Bar(day.strftime("%Y-%m-%d"), float(r["o"]), float(r["h"]),
                           float(r["l"]), float(r["c"]), int(r["v"])))
        return out


class FinnhubProvider(DataProvider):
    """Finnhub stock candles.

      SOURCE: https://finnhub.io/docs/api/stock-candles
    """

    name = "finnhub"
    docs_url = "https://finnhub.io/docs/api/stock-candles"
    key_env = "FINNHUB_API_KEY"
    realtime = True
    notes = ("Free tier includes U.S. equity candles with rate limits; premium "
             "plans are required for full real-time top-of-book data. Verify "
             "current plan terms before production use.")

    def fetch_daily(self, symbol: str, start: str, end: str) -> List[Bar]:
        import datetime as dt
        key = self.require_key()
        f = int(dt.datetime.strptime(start, "%Y-%m-%d").timestamp())
        t = int(dt.datetime.strptime(end, "%Y-%m-%d").timestamp())
        url = (f"https://finnhub.io/api/v1/stock/candle?symbol={symbol}"
               f"&resolution=D&from={f}&to={t}&token={key}")
        payload = json.loads(self._get(url, {}).decode("utf-8"))
        if not payload.get("s") == "ok":
            raise ProviderUnavailable(f"finnhub: {payload}")
        out: List[Bar] = []
        for i, epoch in enumerate(payload["t"]):
            day = dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
            out.append(Bar(day.strftime("%Y-%m-%d"), float(payload["o"][i]),
                           float(payload["h"][i]), float(payload["l"][i]),
                           float(payload["c"][i]), int(payload["v"][i])))
        return out


class TiingoProvider(DataProvider):
    """Tiingo daily end-of-day prices.

      SOURCE: https://www.tiingo.com/documentation/end-of-day
    """

    name = "tiingo"
    docs_url = "https://www.tiingo.com/documentation/end-of-day"
    key_env = "TIINGO_API_KEY"
    realtime = False
    notes = "End-of-day and intraday (IEX) products; see vendor documentation."

    def fetch_daily(self, symbol: str, start: str, end: str) -> List[Bar]:
        key = self.require_key()
        url = (f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
               f"?startDate={start}&endDate={end}")
        payload = json.loads(self._get(url, {"Authorization": f"Token {key}"}).decode("utf-8"))
        return [Bar(r["date"][:10], float(r["open"]), float(r["high"]),
                    float(r["low"]), float(r["close"]), int(r["volume"]))
                for r in payload]


_PROVIDERS: Dict[str, type] = {
    "yahoo": YahooChartProvider, "stooq": StooqProvider, "alpaca": AlpacaProvider,
    "polygon": PolygonProvider, "finnhub": FinnhubProvider, "tiingo": TiingoProvider,
}


def provider_from_name(name: str, **kwargs) -> DataProvider:
    if name not in _PROVIDERS:
        raise ProviderUnavailable(
            f"unknown provider '{name}'. Available: {', '.join(sorted(_PROVIDERS))}")
    return _PROVIDERS[name](**kwargs)


def provider_catalogue() -> List[dict]:
    """Site-ready table describing each feed adapter and its verification state."""
    return [{
        "name": p.name,
        "class": p.__name__,
        "docs_url": p.docs_url,
        "key_env": p.key_env,
        "realtime_capable": p.realtime,
        "notes": p.notes,
        # Honest execution record for this research environment (2026-09-17):
        # the sandbox could reach the Yahoo chart endpoint but not stooq
        # ("Access denied") and not the key-based vendors.
        "attempted_in_this_sandbox": p.name in ("yahoo", "stooq"),
        "succeeded_in_this_sandbox": p.name == "yahoo",
    } for p in _PROVIDERS.values()]


def build_from_provider(provider: DataProvider, cfg: config.CompetitionConfig,
                        calendar: Optional[TradingCalendar] = None) -> MarketData:
    """Build MarketData from a live/historical provider (real prices)."""
    cal = calendar or TradingCalendar(cfg.start, cfg.end)
    instruments = build_universe()
    bars: Dict[str, List[Bar]] = {}
    for inst in instruments:
        fetched = provider.fetch_daily(inst.symbol, cfg.start, cfg.end)
        by_date = {b.date: b for b in fetched}
        series: List[Bar] = []
        last: Optional[Bar] = None
        for d in cal.dates:
            b = by_date.get(d)
            if b is None:
                if last is None:
                    raise ProviderUnavailable(
                        f"{provider.name}: no bar for {inst.symbol} on first session {d}")
                b = Bar(d, last.close, last.close, last.close, last.close, 0)
            series.append(b)
            last = b
        bars[inst.symbol] = series
    spx = [bars["SPY"][i].close for i in range(len(cal))]
    prov = cal.provenance()
    prov.update({"mode": f"provider:{provider.name}", "provider": provider.name,
                 "provider_docs": provider.docs_url,
                 "notes": provider.notes})
    return MarketData(cal, instruments, bars, spx, cal.vix_series(), prov,
                      f"provider:{provider.name}")
