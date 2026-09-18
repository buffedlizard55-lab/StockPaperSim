"""Venue-parameter sensitivity: how much of the ranking is skill, how much is the venue?

WHY THIS EXISTS
---------------
IR-29 measured the knife edge by accident.  Snapping the re-centred quote ladder
back onto the Rule 612 grid - a change of at most half a tick on every name in
the universe - moved @OneBigBet_Concentra by 28.3pp and @OverreactionFade_LT by
32.9pp on the same seed.  That is a property of the model, not a bug to smooth
over, and the project's answer is to measure it rather than disclaim it: re-run
the identical season on the identical real market path with one venue parameter
moved at a time, and publish what happens to every participant's return and rank.

TWO RULES MAKE THE MEASUREMENT MEAN SOMETHING
---------------------------------------------
1. **The market path is held fixed.**  sim/marketdata.py seeds each instrument's
   idiosyncratic draws with the configuration fingerprint, so a *perturbed
   config* would generate a different market.  The harness therefore builds the
   replay once from the shipped configuration and re-uses that object for every
   run in the grid - which is what makes the difference attributable to the
   parameter and not to a different draw.
2. **One thing moves per run.**  A perturbation names exactly one parameter, and
   the report records the shipped value and the perturbed value side by side, so
   the reader can see what was and was not held constant.

WHAT IT IS NOT
--------------
It is not a confidence interval: it moves *declared model choices*, not sampling
noise, so the spread it reports is a sensitivity band, not a standard error.  The
idiosyncratic-across-seeds question is the separate one that
analytics.robustness_panel answers, and the two are published side by side
because they say different things.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import config
from . import engine as engine_module


@dataclass(frozen=True)
class Perturbation:
    """One venue parameter moved, with everything else held fixed.

    ``kind`` is "config" for a value inside CompetitionConfig and "venue" for a
    model-form switch that deliberately is *not* a config field (moving one must
    not change the configuration fingerprint the replay was generated from).
    """

    name: str
    parameter: str
    value: object
    baseline: object
    why: str
    kind: str = "config"
    #: Set instead of ``parameter`` when the row moves a *set* of values (the
    #: per-tier spread bounds).  It receives a deep copy of the config and must
    #: change only the declared values.
    mutate: Optional[Callable[[object], None]] = None


#: The grid.  Every entry is a declared modelling choice this project made, not a
#: legal constant: the Rule 612 tick grid and the Rule 610(c) access-fee cap are
#: fixed by regulation and are not perturbed here.
PERTURBATIONS: Tuple[Perturbation, ...] = (
    Perturbation(
        name="spread_k_x0.8", parameter="liquidity.spread_k_ticks",
        value=0.04, baseline=0.05,
        why="The quoted-spread coefficient is a SIM CHOICE: 20% tighter quotes "
            "everywhere, with the per-tier tick bounds unchanged."),
    Perturbation(
        name="spread_k_x1.2", parameter="liquidity.spread_k_ticks",
        value=0.06, baseline=0.05,
        why="The same choice in the other direction: 20% wider quotes."),
    Perturbation(
        name="spread_ticks_plus1", parameter="liquidity min/max_spread_ticks",
        value="every tier +1", baseline="mega 1-3 … micro 2-30",
        mutate=lambda c: _shift_spread_ticks(c, +1),
        why="The literal venue perturbation: the quoted spread is one tick wider "
            "wherever a bound binds, which is what a reader pictures when they "
            "ask how much a tick of spread costs a strategy. Every tier's floor "
            "and ceiling moves; the size of the move in cents is therefore "
            "price-dependent, exactly as it is in the market."),
    Perturbation(
        name="spread_ticks_minus1", parameter="liquidity min/max_spread_ticks",
        value="every tier -1 (floor 1)", baseline="mega 1-3 … micro 2-30",
        mutate=lambda c: _shift_spread_ticks(c, -1),
        why="The other side of the same venue rule: one tick tighter, with the "
            "floor held at one tick because a one-tick quote is the tightest "
            "any venue can publish. It is also the direction that makes the "
            "MODEL's choice of per-tier ceilings visible as a cost."),
    Perturbation(
        name="depth_growth_1.2", parameter="liquidity.depth_growth",
        value=1.20, baseline=1.60,
        why="How fast displayed size grows away from the touch. A flatter "
            "ladder: less size behind the touch at every level above it."),
    Perturbation(
        name="depth_growth_2.0", parameter="liquidity.depth_growth",
        value=2.00, baseline=1.60,
        why="A steeper ladder: more size one and two levels in."),
    Perturbation(
        name="hidden_liquidity_off", parameter="liquidity.hidden_liquidity",
        value=False, baseline=True,
        why="Non-displayed liquidity at the touch models the off-exchange share "
            "of volume (FINRA OTC Transparency). Turning it off is the "
            "conservative case: only the displayed ladder can fill."),
    Perturbation(
        name="impact_coef_x0.8", parameter="impact.coefficient",
        value=0.44, baseline=0.55,
        why="The square-root impact coefficient is a SIM CHOICE inside the "
            "published 0.1-1.0 range; this is its lower edge."),
    Perturbation(
        name="impact_coef_x1.2", parameter="impact.coefficient",
        value=0.66, baseline=0.55,
        why="The same square-root coefficient 20% higher: 0.66 instead of "
            "0.55, still inside the published 0.1-1.0 range."),
    Perturbation(
        name="impact_exponent_0.6", parameter="impact_exponent",
        value=0.6, baseline=0.5, kind="venue",
        why="IR-26: Almgren, Thum, Hauptmann & Li (2005) measured a 3/5 power "
            "law and explicitly rejected the square root for temporary impact. "
            "The exponent is a model form, not a config value, so it moves as "
            "a venue override."),
    Perturbation(
        name="tick_snap_off", parameter="snap_quotes_to_tick",
        value=False, baseline=True, kind="venue",
        why="IR-29: without snapping, the re-centred ladder publishes prices "
            "that are not on the Rule 612 grid - what the engine did before "
            "IR-28. The published season is the snapped one; this measures what "
            "half a tick is worth to the ranking."),
    Perturbation(
        name="makers_3", parameter="market_maker.num_makers",
        value=3, baseline=4,
        why="Number of competing market makers (Avellaneda-Stoikov, SIM CHOICE): "
            "one fewer dealer quoting each side."),
    Perturbation(
        name="taker_fee_x1.5", parameter="costs.taker_fee_per_share",
        value=0.0045, baseline=0.0030,
        why="The taker fee sits at the Rule 610(c) access-fee cap; this asks what "
            "a cap 50% higher would have done. A legal constant does not move in "
            "the model - it is perturbed here only as a cost sensitivity."),
    Perturbation(
        name="maker_rebate_off", parameter="costs.maker_rebate_per_share",
        value=0.0, baseline=0.0020,
        why="Maker rebates to zero: the case where passive liquidity is not paid "
            "for its fill."),
)


def _shift_spread_ticks(cfg: config.CompetitionConfig, delta: int) -> None:
    """Move every per-tier spread bound by ``delta``, floor 1, ceiling >= floor."""
    for field_name in ("min_spread_ticks", "max_spread_ticks"):
        bounds = getattr(cfg.liquidity, field_name)
        moved = {tier: max(1, ticks + delta) for tier, ticks in bounds.items()}
        if field_name == "max_spread_ticks":
            moved = {tier: max(ticks, cfg.liquidity.min_spread_ticks[tier])
                     for tier, ticks in moved.items()}
        setattr(cfg.liquidity, field_name, moved)


def _dotted(cfg: config.CompetitionConfig, path: str):
    """Read a dotted config path, so the grid can be checked against the config."""
    head, _, tail = path.partition(".")
    target = getattr(cfg, head)
    return getattr(target, tail) if tail else target


def _set_dotted(cfg: config.CompetitionConfig, path: str, value: object) -> None:
    head, _, tail = path.partition(".")
    if not tail:
        raise ValueError(f"{path!r} is not a dotted path inside the config")
    target = getattr(cfg, head)
    if not hasattr(target, tail):
        raise KeyError(f"{path!r} is not a field of {type(target).__name__}")
    setattr(target, tail, value)


def perturbed_config(base: config.CompetitionConfig,
                     perturbation: Perturbation) -> config.CompetitionConfig:
    """A deep copy of ``base`` with exactly one config value moved."""
    if perturbation.kind != "config":
        raise ValueError(f"{perturbation.name} is a venue override, not a config value")
    cfg = copy.deepcopy(base)
    if perturbation.mutate is not None:
        perturbation.mutate(cfg)
    else:
        _set_dotted(cfg, perturbation.parameter, perturbation.value)
    return cfg


def venue_overrides(perturbation: Perturbation) -> Dict[str, object]:
    """The model-form switch a "venue" perturbation moves."""
    if perturbation.kind != "venue":
        raise ValueError(f"{perturbation.name} is a config value, not a venue override")
    return {perturbation.parameter: perturbation.value}


def _run_one(cfg: config.CompetitionConfig, md, seed: int,
             overrides: Dict[str, object]) -> dict:
    """One season on the shared replay, with no memory written.

    Memory is deliberately not written: the grid is a measurement *about* the
    published season, and a dozen extra run directories would make the audit
    trail noisier without making it stronger.  What is published is the report
    below, whose every number is re-derivable by re-running this function.
    """
    run = engine_module.CompetitionEngine(cfg, md, seed=seed, writer=None,
                                          full_memory=False,
                                          venue_overrides=overrides)
    return run.run()


def _returns(record: dict) -> Dict[str, float]:
    return {row["username"]: row["total_return_pct"] for row in record["leaderboard"]}


def _ranks(record: dict) -> Dict[str, int]:
    return {row["username"]: row["rank"] for row in record["leaderboard"]}


def _spearman(a: Dict[str, float], b: Dict[str, float]) -> Optional[float]:
    """Rank correlation of two orderings, computed on the ranks themselves.

    No scipy here by design: this project is standard-library only, and Spearman
    on a complete ordering is Pearson correlation of the ranks.
    """
    common = sorted(set(a) & set(b))
    if len(common) < 2:
        return None
    ra = _rank_of({u: a[u] for u in common})
    rb = _rank_of({u: b[u] for u in common})
    n = len(common)
    mean = (n + 1) / 2.0
    cov = sum((ra[u] - mean) * (rb[u] - mean) for u in common)
    var = sum((ra[u] - mean) ** 2 for u in common)
    if var == 0:
        return None
    return cov / var


def _rank_of(values: Dict[str, float]) -> Dict[str, float]:
    """Average ranks, so ties do not silently invent an order."""
    order = sorted(values, key=lambda u: -values[u])
    out: Dict[str, float] = {}
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def sensitivity_report(base: dict,
                       runs: Sequence[Tuple[Perturbation, dict]],
                       grid: Sequence[Perturbation] = PERTURBATIONS) -> dict:
    """Per-participant response to each perturbation, plus the headline numbers."""
    base_returns = _returns(base)
    base_ranks = _ranks(base)
    per_perturbation: Dict[str, Dict[str, float]] = {}
    per_perturbation_rank: Dict[str, Dict[str, int]] = {}
    for perturbation, record in runs:
        per_perturbation[perturbation.name] = _returns(record)
        per_perturbation_rank[perturbation.name] = _ranks(record)

    correlation = {name: _spearman(base_returns, returns)
                   for name, returns in per_perturbation.items()}
    rows: List[dict] = []
    for username in base_returns:
        values = [per_perturbation[p.name][username] for p, _ in runs]
        ranks = [per_perturbation_rank[p.name][username] for p, _ in runs]
        rows.append({
            "username": username,
            "base_return_pct": round(base_returns[username], 3),
            "base_rank": base_ranks[username],
            "perturbed_return_pct": {p.name: round(per_perturbation[p.name][username], 3)
                                     for p, _ in runs},
            "perturbed_rank": {p.name: per_perturbation_rank[p.name][username]
                               for p, _ in runs},
            "min_return_pct": round(min(values), 3),
            "max_return_pct": round(max(values), 3),
            "mean_return_pct": round(sum(values) / len(values), 3),
            "stdev_pp": round(_stdev(values), 3),
            "best_rank": min(ranks),
            "worst_rank": max(ranks),
            "max_abs_rank_change": max(abs(r - base_ranks[username]) for r in ranks),
            "sign_flips": sum(1 for v in values
                              if (v > 0) != (base_returns[username] > 0)),
        })
    rows.sort(key=lambda r: -r["max_abs_rank_change"])

    biggest = max(rows, key=lambda r: abs(r["max_return_pct"] - r["base_return_pct"])
                  if abs(r["max_return_pct"] - r["base_return_pct"])
                  >= abs(r["min_return_pct"] - r["base_return_pct"])
                  else abs(r["min_return_pct"] - r["base_return_pct"]), default=None)
    losers = [r for r in rows if r["min_return_pct"] <= 0 < r["base_return_pct"]]
    return {
        "perturbations": [{"name": p.name, "parameter": p.parameter,
                           "baseline": p.baseline, "value": p.value,
                           "kind": p.kind, "why": p.why} for p in grid],
        "by_participant": rows,
        "summary": {
            "n_perturbations": len(runs),
            "n_participants": len(rows),
            "participants_with_rank_change": sum(
                1 for r in rows if r["max_abs_rank_change"] > 0),
            "max_abs_rank_change": max((r["max_abs_rank_change"] for r in rows),
                                       default=0),
            "max_abs_return_swing_pp": round(max(
                (max(abs(r["max_return_pct"] - r["base_return_pct"]),
                     abs(r["min_return_pct"] - r["base_return_pct"])) for r in rows),
                default=0.0), 3),
            "mean_spearman": round(sum(c for c in correlation.values() if c is not None)
                                   / max(1, sum(1 for c in correlation.values()
                                                if c is not None)), 4),
            "min_spearman": round(min((c for c in correlation.values() if c is not None),
                                      default=0.0), 4),
            "spearman_by_perturbation": {k: (round(v, 4) if v is not None else None)
                                         for k, v in correlation.items()},
            "sign_flips": sum(r["sign_flips"] for r in rows),
            "participants_pushed_below_zero": [r["username"] for r in losers],
            "most_sensitive_participant": biggest["username"] if biggest else None,
            "note": ("This is a sensitivity band over declared model choices, not a "
                     "sampling confidence interval: consecutive points move one "
                     "parameter, hold the real market path and the seed fixed, and "
                     "say nothing about idiosyncratic risk."),
        },
    }


def run_grid(cfg: config.CompetitionConfig, md, seed: Optional[int] = None,
             perturbations: Sequence[Perturbation] = PERTURBATIONS,
             verbose: bool = False) -> dict:
    """Run the base season and the whole grid on one shared replay.

    ``md`` must be built once, from the *unperturbed* configuration, so every run
    in the grid trades the same prices; that is what makes the reported
    difference a property of the parameter.
    """
    seed = cfg.seed if seed is None else seed
    base = _run_one(cfg, md, seed, {})
    runs: List[Tuple[Perturbation, dict]] = []
    for perturbation in perturbations:
        if perturbation.kind == "config":
            run_cfg = perturbed_config(cfg, perturbation)
            overrides: Dict[str, object] = {}
        else:
            run_cfg = cfg
            overrides = venue_overrides(perturbation)
        if verbose:
            print(f"  sensitivity: {perturbation.name:22s} {perturbation.parameter}"
                  f" = {perturbation.value!r} ...", flush=True)
        runs.append((perturbation, _run_one(run_cfg, md, seed, overrides)))
    report = sensitivity_report(base, runs, grid=perturbations)
    report["base"] = {
        "seed": base["seed"],
        "config_fingerprint": base["config_fingerprint"],
        "window": {"start": cfg.start, "end": cfg.end},
        "leaderboard": [{"rank": r["rank"], "username": r["username"],
                         "total_return_pct": r["total_return_pct"]}
                        for r in base["leaderboard"]],
        "market": {"spx_return_pct": base["market"].get("spx_return_pct")},
    }
    return report
