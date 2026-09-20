"""Formal significance testing for the published season results.

The robustness panel shows the spread of outcomes across scenarios but does
not test it, and nothing on the site says whether a Sharpe ratio or an alpha
is distinguishable from zero once twenty strategies have been run against the
same tape (the P1 item "formal significance testing", L-08).  This module is
the missing test layer.  Everything here is standard library only and derived
from committed run memory - the equity curves and the robustness panel - so
CI re-runs reproduce byte-identically for a fixed seed.

What is implemented, and the assumption each carries:

* **Bootstrap confidence intervals** for Sharpe, market alpha/beta and max
  drawdown, by resampling the participant's own daily returns with
  replacement (iid bootstrap, 2,000 replicates, fixed seed).  The iid
  assumption understates the uncertainty of autocorrelated return series, so
  the intervals are a floor on uncertainty, not a ceiling - the page says so.
* **Deflated Sharpe Ratio** (Bailey & Lopez de Prado, "The Sharpe Ratio
  Efficient Frontier", Journal of Risk 14(3), 2012, and the 2014 JPM paper
  "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest
  Overfitting and Non-Normality") - the probability that the observed Sharpe
  is still positive after accounting for (a) non-normal returns via the PSR
  correction and (b) having run ``n_trials`` strategies at the same tape,
  via the expected maximum Sharpe of N independent trials.
* **Cross-scenario rank test** - how often the published winner is still the
  winner across the robustness scenarios, against the 1/N chance level with an
  exact binomial tail.

No published number depends on this module: it reads run memory and writes
``docs/assets/data/significance.json``.  Deleting it leaves every other page
unchanged, which is the correct dependency direction for an audit layer.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Dict, List, Optional, Sequence, Tuple

#: Replicates per bootstrap.  2,000 gives 95% percentile bounds that are
#: stable to roughly +/-0.02 on an annualised Sharpe of order 1; higher values
#: only smooth the tails.
DEFAULT_N_BOOT = 2_000
TRADING_DAYS = 252

_ND = statistics.NormalDist()


def daily_returns(curve: Sequence[Tuple[str, float]]) -> List[float]:
    out: List[float] = []
    for (_, prev), (_, cur) in zip(curve, curve[1:]):
        if prev > 0 and cur > 0:
            out.append(cur / prev - 1.0)
    return out


def _sharpe(returns: Sequence[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sd = statistics.stdev(returns)
    if sd <= 0.0:
        return 0.0
    return statistics.mean(returns) / sd


def _ols(y: Sequence[float], x: Sequence[float]) -> Tuple[float, float]:
    """Simple OLS -> (alpha_daily, beta)."""
    n = min(len(y), len(x))
    y, x = list(y[-n:]), list(x[-n:])
    if n < 3:
        return 0.0, 0.0
    mx, my = statistics.mean(x), statistics.mean(y)
    sxx = sum((xi - mx) ** 2 for xi in x)
    if sxx <= 0.0:
        return 0.0, 0.0
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    beta = sxy / sxx
    return my - beta * mx, beta


def _max_drawdown_pct(returns: Sequence[float]) -> float:
    level = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        level *= (1.0 + r)
        peak = max(peak, level)
        if peak > 0:
            worst = min(worst, level / peak - 1.0)
    return 100.0 * worst


def _bootstrap(values_a: Sequence[float], values_b: Optional[Sequence[float]],
               statistic, n_boot: int, seed: int) -> Tuple[List[float], float]:
    """Percentile bootstrap of ``statistic(a, b)``; returns (stats, point)."""
    rng = random.Random(seed)
    n = len(values_a)
    paired = values_b is not None
    point = (statistic(values_a, values_b) if paired
             else statistic(values_a, None))
    stats: List[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        if paired:
            b = values_b or []
            stats.append(statistic([values_a[i] for i in idx],
                                   [b[i] for i in idx]))
        else:
            stats.append(statistic([values_a[i] for i in idx], None))
    stats.sort()
    return stats, point


def _pct(stats: Sequence[float], q: float) -> float:
    if not stats:
        return 0.0
    k = max(0, min(len(stats) - 1, int(round(q * (len(stats) - 1)))))
    return stats[k]


def bootstrap_sharpe(returns: Sequence[float], n_boot: int = DEFAULT_N_BOOT,
                     seed: int = 20260920) -> dict:
    """Annualised Sharpe with a 95% bootstrap interval and P(SR<=0)."""
    rets = list(returns)
    stats, point = _bootstrap(rets, None,
                              lambda a, _b: _sharpe(a), n_boot, seed)
    ann = math.sqrt(TRADING_DAYS)
    below = sum(1 for s in stats if s <= 0.0)
    return {
        "sharpe_annualised": round(ann * point, 4),
        "ci95_low": round(ann * _pct(stats, 0.025), 4),
        "ci95_high": round(ann * _pct(stats, 0.975), 4),
        "p_sharpe_le_zero": round(below / len(stats), 4) if stats else None,
        "sessions": len(rets),
    }


def bootstrap_alpha(returns: Sequence[float], market: Sequence[float],
                    n_boot: int = DEFAULT_N_BOOT,
                    seed: int = 20260921) -> dict:
    """Annualised OLS alpha with a 95% bootstrap interval and P(alpha<=0)."""
    rets, mkt = list(returns), list(market)
    n = min(len(rets), len(mkt))
    rets, mkt = rets[-n:], mkt[-n:]

    def stat(a: Sequence[float], b: Optional[Sequence[float]]) -> float:
        alpha, _beta = _ols(a, b or [])
        return alpha

    stats, point = _bootstrap(rets, mkt, stat, n_boot, seed)
    ann = math.sqrt(TRADING_DAYS)
    below = sum(1 for s in stats if s <= 0.0)
    _alpha_d, beta_point = _ols(rets, mkt)
    bstats, _ = _bootstrap(rets, mkt,
                           lambda a, b: (_ols(a, b or [])[1]), n_boot, seed + 1)
    return {
        "alpha_daily": round(point, 6),
        "alpha_annual_pct": round(100.0 * ann * point, 4),
        "ci95_low_pct": round(100.0 * ann * _pct(stats, 0.025), 4),
        "ci95_high_pct": round(100.0 * ann * _pct(stats, 0.975), 4),
        "p_alpha_le_zero": round(below / len(stats), 4) if stats else None,
        "beta": round(beta_point, 4),
        "beta_ci95": [round(_pct(bstats, 0.025), 4),
                      round(_pct(bstats, 0.975), 4)],
        "sessions": n,
    }


def bootstrap_max_drawdown(returns: Sequence[float],
                           n_boot: int = DEFAULT_N_BOOT,
                           seed: int = 20260922) -> dict:
    """Max drawdown (pct) of a resampled return path, 95% interval.

    Each replicate rebuilds a compounding path from the resampled daily
    returns, so the statistic measures the *path* risk of the same return
    distribution, not the one realised ordering.
    """
    rets = list(returns)
    stats, point = _bootstrap(rets, None,
                              lambda a, _b: _max_drawdown_pct(a),
                              n_boot, seed)
    return {
        "max_drawdown_pct": round(point, 4),
        "ci95_low_pct": round(_pct(stats, 0.025), 4),
        "ci95_high_pct": round(_pct(stats, 0.975), 4),
        "sessions": len(rets),
    }


def deflated_sharpe(returns: Sequence[float], n_trials: int,
                    trial_sr_variance: Optional[float] = None) -> dict:
    """Probability the observed Sharpe beats the expected best of N trials.

    Follows Bailey & Lopez de Prado (2014).  SR is the *non-annualised* mean/std
    of daily returns; the expected maximum SR under the null that all N trials
    are zero-skill is

        SR0 = sqrt(V[SR]) * ((1 - g) * Z(1 - 1/N) + g * Z(1 - 1/(N*e)))

    with g the Euler-Mascheroni constant, e Euler's number, and V[SR] the
    variance of the trials' Sharpe estimates - taken from the cross-scenario
    panel when one is supplied, otherwise from the estimator's own asymptotic
    variance (1 - g3*SR + (g4-1)/4*SR^2)/(T-1), with g3 skewness and g4
    kurtosis of the returns.  DSR = Phi( (SR - SR0) * sqrt(T-1) / sigma_SR ),
    where sigma_SR is the same denominator as the PSR correction.
    """
    rets = list(returns)
    t = len(rets)
    if t < 20 or n_trials < 1:
        return {"dsr": None, "note": "insufficient observations"}
    sr = _sharpe(rets)
    mean = statistics.mean(rets)
    var = statistics.variance(rets)
    g3 = _moment(rets, mean, 3)
    g4 = _moment(rets, mean, 4)          # raw (normal == 3)
    denom2 = max(1e-12, 1.0 - g3 * sr + ((g4 - 1.0) / 4.0) * sr * sr)
    if trial_sr_variance is None:
        v_sr = max(1e-12, denom2 / max(1.0, t - 1))
    else:
        v_sr = max(1e-12, trial_sr_variance)
    n = max(2, int(n_trials))
    euler_gamma = 0.5772156649015329
    z1 = _ND.inv_cdf(1.0 - 1.0 / n)
    z2 = _ND.inv_cdf(1.0 - 1.0 / (n * math.e))
    sr0 = math.sqrt(v_sr) * ((1.0 - euler_gamma) * z1 + euler_gamma * z2)
    dsr = _ND.cdf((sr - sr0) * math.sqrt(max(1.0, t - 1)) / math.sqrt(denom2))
    return {
        "dsr": round(dsr, 4),
        "sharpe_daily": round(sr, 6),
        "expected_max_sharpe_daily_under_null": round(sr0, 6),
        "n_trials": n,
        "sessions": t,
        "skew": round(g3, 4),
        "kurtosis": round(g4, 4),
        "trial_sr_variance_source":
            "robustness panel" if trial_sr_variance is not None
            else "asymptotic estimator variance",
    }


def _moment(xs: Sequence[float], mean: float, k: int) -> float:
    if len(xs) < 2:
        return 0.0
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    if var <= 0:
        return 0.0
    m_k = sum((x - mean) ** k for x in xs) / len(xs)
    return m_k / (var ** (k / 2.0))


def rank_test(returns_by_scenario_pct: Dict[str, Dict[str, float]]) -> dict:
    """How often each participant wins a scenario, vs the 1/N chance level.

    ``returns_by_scenario_pct`` maps username -> {scenario seed: total return
    percent}.  With S scenarios and N participants the null probability that a
    given participant tops any one scenario is 1/N; the number of scenario wins
    out of S follows Binomial(S, 1/N) under the null, so the p-value is the
    exact tail P(X >= wins).
    """
    if not returns_by_scenario_pct:
        return {"scenarios": 0}
    seeds = sorted({s for row in returns_by_scenario_pct.values() for s in row})
    users = sorted(returns_by_scenario_pct)
    n_users = len(users)
    s_count = len(seeds)
    wins = {u: 0 for u in users}
    for seed in seeds:
        best = max(users, key=lambda u: returns_by_scenario_pct[u].get(seed, 0.0))
        wins[best] += 1
    p_null = 1.0 / n_users

    def _binom_tail_ge(k: int, s: int, p: float) -> float:
        return sum(_binom_pmf(i, s, p) for i in range(k, s + 1))

    def _binom_pmf(k: int, s: int, p: float) -> float:
        if k < 0 or k > s:
            return 0.0
        logc = math.lgamma(s + 1) - math.lgamma(k + 1) - math.lgamma(s - k + 1)
        return math.exp(logc + k * math.log(p) + (s - k) * math.log(1 - p))

    rows = {}
    for u in users:
        k = wins[u]
        rows[u] = {
            "scenario_wins": k,
            "win_rate": round(k / s_count, 4) if s_count else 0.0,
            "chance_rate": round(p_null, 4),
            "p_value": (round(_binom_tail_ge(k, s_count, p_null), 4)
                        if s_count else None),
        }
    return {"scenarios": s_count, "participants": n_users, "rows": rows}


def season_significance(curves: Dict[str, Sequence[Tuple[str, float]]],
                        market_curve: Sequence[Tuple[str, float]],
                        scenario_returns_pct: Optional[Dict[str, Dict[str, float]]] = None,
                        n_boot: int = DEFAULT_N_BOOT,
                        seed: int = 20260920) -> dict:
    """Full significance bundle for one season's committed curves.

    ``curves`` maps username -> the participant's equity curve; ``market_curve``
    is the benchmark the alphas are measured against.  ``scenario_returns_pct``
    is the robustness panel's per-scenario total returns (username -> seed ->
    percent), which feeds both the DSR trial variance and the rank test.
    """
    market_rets = daily_returns(market_curve)
    by_user: Dict[str, dict] = {}
    # Cross-scenario Sharpe variance per user, for the DSR trial variance:
    # compute each scenario's (non-annualised) Sharpe from its implied total
    # return is not defined, so the panel contributes variance only when the
    # caller can supply true per-scenario curves.  Season memory commits only
    # total returns per scenario, so the DSR uses the asymptotic estimator
    # variance and the panel feeds the rank test.  Documented on the page.
    trial_variance: Dict[str, Optional[float]] = {
        u: None for u in curves}
    for username, curve in curves.items():
        rets = daily_returns(curve)
        if not rets:
            by_user[username] = {"error": "no returns"}
            continue
        entry = {
            "sharpe": bootstrap_sharpe(rets, n_boot, seed),
            "alpha": bootstrap_alpha(rets, market_rets, n_boot, seed + 1),
            "max_drawdown": bootstrap_max_drawdown(rets, n_boot, seed + 2),
            "deflated_sharpe": deflated_sharpe(
                rets, n_trials=max(2, len(curves)),
                trial_sr_variance=trial_variance.get(username)),
        }
        by_user[username] = entry
    result = {
        "method": {
            "bootstrap": (f"iid percentile bootstrap, {n_boot} replicates, "
                          "resampling each participant's own daily returns; "
                          "iid understates the uncertainty of autocorrelated "
                          "series, so read each interval as a floor"),
            "deflated_sharpe": ("Bailey & Lopez de Prado (2014); n_trials = "
                                f"roster size ({len(curves)}); trial Sharpe "
                                "variance from the estimator's asymptotic "
                                "variance because committed memory holds "
                                "scenario totals, not scenario curves"),
            "rank_test": ("exact binomial tail on scenario wins against the "
                          "1/N chance level"),
            "seed": seed,
        },
        "participants": by_user,
    }
    if scenario_returns_pct:
        result["rank_test"] = rank_test(scenario_returns_pct)
    return result
