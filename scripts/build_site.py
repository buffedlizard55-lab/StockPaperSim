#!/usr/bin/env python3
"""Build the GitHub Pages site from run memory.

    python3 scripts/build_site.py [--out docs] [--memory-root memory]

Design constraints that shaped this generator:

* **No CDN, no build step at view time, no JavaScript framework.**  GitHub
  Pages serves static files; everything a visitor needs is rendered server-side
  into HTML plus inline SVG charts.  The only JavaScript is ~80 lines of
  vanilla DOM code for sorting the leaderboard and toggling sections.
* **Every number on the site is read out of ``memory/``**, i.e. it was produced
  by an actual run and checksummed in that run's manifest.  Nothing is typed in
  by hand and nothing is recomputed here.
* **Every claim that came from outside the code carries its source link and its
  verification status**, including the honest "KNOWN-NOT-FETCHED" label for the
  academic references that could not be retrieved inside this sandbox.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sim import config, memory, universe                      # noqa: E402

ESC = html.escape

# Format strings live in constants, never inline inside an f-string: Python 3.11
# parses braces inside replacement fields, so a literal format string nested in
# an f-string breaks compilation.
F0 = "{:,.0f}"
F0P = "{:.0f}"
F0PP = "{:.0f}%"
F1 = "{:.1f}"
F1S = "{:+.1f}"
F1SC = "{:+,.1f}"
F2 = "{:.2f}"
F3 = "{:.3f}"
F4 = "{:.4f}"
F5 = "{:.5f}"
FC0S = "{:+,.0f}"
FC0SP = "{:+,.0f}%"
FC2 = "{:,.2f}"
FD0 = "${:,.0f}"


# ==========================================================================
# SVG chart primitives (dependency-free)
# ==========================================================================

def _nice_ticks(lo: float, hi: float, n: int = 5) -> List[float]:
    if hi <= lo:
        return [lo]
    raw = (hi - lo) / max(1, n)
    mag = 10 ** int(_floor_log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw / mag <= m:
            step = m * mag
            break
    else:
        step = 10 * mag
    start = _floor(lo / step) * step
    out = []
    v = start
    while v <= hi + step * 0.5:
        out.append(round(v, 10))
        v += step
    return out


def _floor_log10(x: float) -> float:
    import math
    return math.floor(math.log10(abs(x))) if x else 0


def _floor(x: float) -> int:
    import math
    return math.floor(x)


def line_chart(series: Sequence[Tuple[str, List[Tuple[str, float]]]],
               width: int = 960, height: int = 320, y_format: str = F0,
               title: str = "", y_label: str = "", x_labels: int = 8,
               baseline: Optional[float] = None) -> str:
    """Multi-series line chart. ``series`` = [(label, [(date, value), ...]), ...]."""
    all_pts = [(d, v) for _, pts in series for d, v in pts]
    if not all_pts:
        return ""
    pad_l, pad_r, pad_t, pad_b = 74, 16, 30 if title else 14, 40
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b
    dates = sorted({d for d, _ in all_pts})
    xidx = {d: i for i, d in enumerate(dates)}
    n = max(1, len(dates) - 1)
    vals = [v for _, v in all_pts]
    lo, hi = min(vals), max(vals)
    if baseline is not None:
        lo, hi = min(lo, baseline), max(hi, baseline)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    span = hi - lo
    lo -= span * 0.05
    hi += span * 0.05

    def X(d: str) -> float:
        return pad_l + iw * xidx[d] / n

    def Y(v: float) -> float:
        return pad_t + ih * (1 - (v - lo) / (hi - lo))

    colors = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed",
              "#0891b2", "#be185d", "#4d7c0f"]
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
           f'aria-label="{ESC(title or "line chart")}">']
    if title:
        out.append(f'<text x="{pad_l}" y="18" class="chart-title">{ESC(title)}</text>')
    for t in _nice_ticks(lo, hi, 5):
        y = Y(t)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                   f'y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" class="tick" '
                   f'text-anchor="end">{ESC(y_format.format(t))}</text>')
    if baseline is not None:
        y = Y(baseline)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                   f'y2="{y:.1f}" class="baseline"/>')
    step = max(1, len(dates) // x_labels)
    for i in range(0, len(dates), step):
        x = X(dates[i])
        out.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" '
                   f'y2="{pad_t + ih}" class="grid-v"/>')
        out.append(f'<text x="{x:.1f}" y="{height - pad_b + 18}" class="tick" '
                   f'text-anchor="middle">{ESC(dates[i])}</text>')
    for si, (label, pts) in enumerate(series):
        col = colors[si % len(colors)]
        d = " ".join(f'{"M" if i == 0 else "L"}{X(dt):.1f},{Y(v):.1f}'
                     for i, (dt, v) in enumerate(pts))
        out.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="1.8" '
                   f'stroke-linejoin="round"><title>{ESC(label)}</title></path>')
    out.append("</svg>")
    if len(series) > 1:
        out.append('<div class="legend">')
        for si, (label, _) in enumerate(series):
            col = colors[si % len(colors)]
            out.append(f'<span class="legend-item"><i style="background:{col}"></i>'
                       f'{ESC(label)}</span>')
        out.append("</div>")
    if y_label:
        out.append(f'<div class="axis-label">{ESC(y_label)}</div>')
    return "".join(out)


def bar_chart(rows: Sequence[Tuple[str, float]], width: int = 960, height: int = 260,
              title: str = "", y_format: str = F1SC, horizontal: bool = False,
              colour_pos: str = "#059669", colour_neg: str = "#dc2626") -> str:
    if not rows:
        return ""
    pad_l, pad_r, pad_t, pad_b = 74, 16, 30 if title else 14, 60
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b
    vals = [v for _, v in rows]
    lo, hi = min(vals + [0]), max(vals + [0])
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    span = hi - lo
    lo -= span * 0.06
    hi += span * 0.06

    def Y(v: float) -> float:
        return pad_t + ih * (1 - (v - lo) / (hi - lo))

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
           f'aria-label="{ESC(title or "bar chart")}">']
    if title:
        out.append(f'<text x="{pad_l}" y="18" class="chart-title">{ESC(title)}</text>')
    for t in _nice_ticks(lo, hi, 4):
        y = Y(t)
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                   f'y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" class="tick" '
                   f'text-anchor="end">{ESC(y_format.format(t))}</text>')
    zero = Y(0)
    out.append(f'<line x1="{pad_l}" y1="{zero:.1f}" x2="{width - pad_r}" '
               f'y2="{zero:.1f}" class="baseline"/>')
    bw = iw / len(rows) * 0.72
    for i, (label, v) in enumerate(rows):
        x = pad_l + iw * (i + 0.5) / len(rows)
        y = Y(v)
        top, h = (min(y, zero), abs(zero - y))
        col = colour_pos if v >= 0 else colour_neg
        out.append(f'<rect x="{x - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" '
                   f'height="{max(h, 0.6):.1f}" fill="{col}" rx="1.5">'
                   f'<title>{ESC(label)}: {ESC(y_format.format(v))}</title></rect>')
        out.append(f'<text x="{x:.1f}" y="{height - pad_b + 16}" class="tick" '
                   f'text-anchor="middle" transform="rotate(0 {x:.1f} '
                   f'{height - pad_b + 16})">{ESC(label)}</text>')
    out.append("</svg>")
    return "".join(out)


def hbar_chart(rows: Sequence[Tuple[str, float]], width: int = 960,
               row_h: int = 26, title: str = "", y_format: str = FC0S) -> str:
    """Horizontal bars, one per label - used for per-symbol contribution."""
    if not rows:
        return ""
    height = 40 + row_h * len(rows)
    pad_l, pad_r = 90, 90
    iw = width - pad_l - pad_r
    vals = [v for _, v in rows]
    lo, hi = min(vals + [0]), max(vals + [0])
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1

    def X(v: float) -> float:
        return pad_l + iw * (v - lo) / (hi - lo)

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
           f'aria-label="{ESC(title or "contribution chart")}">']
    if title:
        out.append(f'<text x="{pad_l}" y="18" class="chart-title">{ESC(title)}</text>')
    zero = X(0)
    out.append(f'<line x1="{zero:.1f}" y1="30" x2="{zero:.1f}" y2="{height - 10}" '
               f'class="baseline"/>')
    for i, (label, v) in enumerate(rows):
        y = 34 + i * row_h
        x = X(v)
        left, w = (min(x, zero), abs(x - zero))
        col = "#059669" if v >= 0 else "#dc2626"
        out.append(f'<text x="{pad_l - 8}" y="{y + 14}" class="tick" '
                   f'text-anchor="end">{ESC(label)}</text>')
        out.append(f'<rect x="{left:.1f}" y="{y}" width="{max(w, 0.8):.1f}" '
                   f'height="{row_h - 8}" fill="{col}" rx="2">'
                   f'<title>{ESC(label)}: {ESC(y_format.format(v))}</title></rect>')
        out.append(f'<text x="{width - pad_r + 8}" y="{y + 14}" class="tick">'
                   f'{ESC(y_format.format(v))}</text>')
    out.append("</svg>")
    return "".join(out)


def sparkline(points: Sequence[float], width: int = 120, height: int = 28,
              colour: str = "#2563eb") -> str:
    if len(points) < 2:
        return ""
    lo, hi = min(points), max(points)
    if hi - lo < 1e-12:
        hi = lo + 1
    d = " ".join(
        f'{"M" if i == 0 else "L"}{width * i / (len(points) - 1):.1f},'
        f'{height - 2 - (height - 4) * (v - lo) / (hi - lo):.1f}'
        for i, v in enumerate(points))
    up = points[-1] >= points[0]
    col = colour if colour != "#2563eb" else ("#059669" if up else "#dc2626")
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" aria-hidden="true">'
            f'<path d="{d}" fill="none" stroke="{col}" stroke-width="1.4"/></svg>')


# ==========================================================================
# HTML scaffolding
# ==========================================================================

NAV = [
    ("index.html", "Overview"),
    ("leaderboard.html", "Leaderboard"),
    ("strategies.html", "Strategies"),
    ("market.html", "Market &amp; factors"),
    ("methodology.html", "Methodology"),
    ("data.html", "Data provenance"),
    ("sources.html", "Sources"),
    ("irregularities.html", "Irregularities"),
    ("limitations.html", "Limitations"),
    ("participants/index.html", "Participants"),
]


def page(title: str, body: str, active: str = "", head_extra: str = "",
         depth: int = 0) -> str:
    """Render the page shell. ``depth`` is how many directories below the site
    root this page lives, so relative links keep working (participants/)."""
    pre = "../" * depth
    nav = "".join(
        f'<a class="{"active" if href == active else ""}" href="{pre}{href}">{label}</a>'
        for href, label in NAV)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{ESC(title)} · StockPaperSim</title>
<meta name="description" content="StockPaperSim: a one-year paper-trading stock
competition between 20 return-seeking strategies, simulated on a replay of the
real S&amp;P 500 and VIX path, with full execution-cost modelling and a complete
audit trail.">
<link rel="stylesheet" href="{pre}assets/site.css">
{head_extra}
</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="{pre}index.html">StockPaperSim</a>
    <span class="brand-sub">Alpha Cup · Season 1 · 2025-09-17 → 2026-09-16</span>
  </div>
  <nav class="wrap nav">{nav}</nav>
</header>
<main class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <p>StockPaperSim is a research and education project. Every price series a
    participant traded on is either a real published observation (FRED S&amp;P 500
    and VIX closes, Yahoo Finance monthly SPY bars and AAPL bars/dividends) or an
    explicitly labelled simulation of it. Nothing here is investment advice, and
    no result on this site should be read as evidence about a strategy's real
    future performance. See <a href="{pre}limitations.html">Limitations</a>.</p>
    <p>Built from run memory by <code>scripts/build_site.py</code>.
    Data integrity: every file is SHA-256 checksummed in its run manifest.</p>
  </div>
</footer>
<script src="{pre}assets/site.js"></script>
</body>
</html>
"""


def table(headers: Sequence[str], rows: Sequence[Sequence[str]],
          classes: str = "data", foot: str = "") -> str:
    # Guard: a pre-joined HTML string would iterate character by character and
    # silently emit one row per character. Fail loudly instead.
    if isinstance(rows, str):
        raise TypeError(f"table() expects a sequence of rows, got a string of "
                        f"{len(rows)} characters: {rows[:60]!r}")
    for r in rows:
        if isinstance(r, str):
            raise TypeError(f"table() expects each row to be a sequence of "
                            f"cells, got a string: {r[:60]!r}")
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>"
                   for r in rows)
    return (f'<table class="{classes}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody>{foot}</table>')


def card(title: str, body: str, cls: str = "") -> str:
    return f'<section class="card {cls}"><h2>{title}</h2>{body}</section>'


NA = '<span class="na">n/a</span>'


def _is_finite(v) -> bool:
    """True only for a real number that can be printed honestly."""
    if v is None or isinstance(v, bool):
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def _numeric(v) -> bool:
    """True if v is a number of some kind, finite or not."""
    if v is None or isinstance(v, bool):
        return False
    try:
        float(v)
    except (TypeError, ValueError):
        return False
    return True


def num(v, fmt=FC2, suffix="") -> str:
    if not _is_finite(v):
        # Covers None, NaN and +/-inf: JSON has no NaN, but Python floats do,
        # and "+nan%" on a published page is exactly the kind of thing a reader
        # cannot distinguish from a real number.
        return NA if (v is None or _numeric(v)) else ESC(str(v))
    try:
        return fmt.format(v) + suffix
    except (TypeError, ValueError):
        return ESC(str(v))


def cost_split_sentence(c: dict) -> str:
    """Percentages of a negative total are meaningless; say what happened instead."""
    split = c.get("market_vs_liquidity_split") or {}
    if c.get("net_liquidity_provider") or split.get("spread_and_impact_pct") is None:
        return ('This participant was a <strong>net liquidity provider</strong>: maker '
                f'rebates of {money(abs(c.get("rebate_usd", 0.0)))} exceeded the fees it '
                'paid, so all-in execution cost was negative. Percentages of a negative '
                'total are not reported.')
    return (f'{num(split["spread_and_impact_pct"], F0P)}% of the friction was '
            f'spread/depth/impact, {num(split["explicit_fees_pct"], F0P)}% was explicit '
            'fees and rebates.')


def failure_mode_block(n: dict) -> str:
    """Fired / untestable / structural are three different statements.

    A mode that fired is a measured fact about this run.  A mode this run cannot
    test is neither confirmed nor ruled out, and saying so is the difference
    between a post-mortem and a story.  Structural caveats are properties of the
    simulation itself and are always shown.
    """
    parts = []
    fired = n.get("failure_modes_fired") or []
    untested = n.get("failure_modes_untested") or []
    structural = n.get("structural_caveats") or []
    parts.append("<h4>Actually fired (measured on this run)</h4>" +
                 ("<ul>" + "".join(f"<li>{ESC(x)}</li>" for x in fired) + "</ul>"
                  if fired else '<p class="muted">None.</p>'))
    parts.append("<h4>Cannot be tested by this simulation</h4>" +
                 ("<ul>" + "".join(f"<li>{ESC(x)}</li>" for x in untested) + "</ul>"
                  if untested else
                  '<p class="muted">Every declared failure mode maps to a '
                  'measurable condition.</p>'))
    if structural:
        parts.append("<h4>Structural limits of the simulation itself</h4><ul>" +
                     "".join(f"<li>{ESC(x)}</li>" for x in structural) + "</ul>")
    return "".join(parts)


def date_list(v, suffix: str = "") -> str:
    """Render a list of ISO dates as readable prose.

    ``str(["2025-11-27", ...])`` used to reach the published page verbatim,
    which reads like a debug dump (square brackets, single quotes) rather than
    like a data-provenance table.
    """
    if not v:
        return NA
    if isinstance(v, str):
        v = [v]
    items = [str(x) for x in v]
    out = ", ".join(ESC(x) for x in items)
    lead = f"{len(items)} date" + ("s" if len(items) != 1 else "") + ": "
    return f"{lead}<code>{out}</code>{ESC(suffix)}"


def pf_cell(v) -> str:
    """Profit factor is None when a participant had no losing round trips."""
    if v is None:
        return '<span title="No losing round trips in this season">&#8734;</span>'
    if not _is_finite(v):
        return NA
    return num(v, F2)


def pct_cls(v: Optional[float]) -> str:
    if not _is_finite(v):
        return "na"
    return "pos" if v > 0 else ("neg" if v < 0 else "zero")


def signed(v: Optional[float], digits: int = 2, suffix: str = "%") -> str:
    if not _is_finite(v):
        return NA if (v is None or _numeric(v)) else ESC(str(v))
    return f'<span class="{pct_cls(v)}">{v:+.{digits}f}{suffix}</span>'


def money(v: Optional[float], digits: int = 0) -> str:
    if not _is_finite(v):
        return NA if (v is None or _numeric(v)) else ESC(str(v))
    return f'<span class="{pct_cls(v)}">{v:+,.{digits}f}</span>'


def badge(text: str, kind: str = "") -> str:
    return f'<span class="badge badge-{kind or text.lower().replace(" ", "-")}">{ESC(text)}</span>'


STATUS_KIND = {
    "FETCHED-VERIFIED": "ok", "FETCHED": "ok", "FETCHED-VIA-SEARCH": "warn",
    "SECONDARY": "warn", "KNOWN-NOT-FETCHED": "muted", "ADAPTER-DOCS": "info",
}


def status_badge(status: str) -> str:
    return badge(status, STATUS_KIND.get(status, "muted"))


# ==========================================================================
# Data loading
# ==========================================================================

class SiteData:
    def __init__(self, root: str, run_id: str = "") -> None:
        self.store = memory.MemoryStore(root=root)
        runs = self.store.runs()
        if not runs:
            raise SystemExit(f"no runs in {root}; run `python3 -m sim.cli run` first")
        self.all_runs = runs
        self.run_id = run_id or self._pick_primary([r["run_id"] for r in runs])
        # Season 2 runs live in the same memory store as Season 1's. Season 1's
        # pages must not silently publish Season 2's numbers (or drag Season 2
        # runs into Season 1's scenario panel), so every page set is scoped to
        # the season prefix of the run it is built from.
        season_prefix = self.run_id.split("-", 1)[0] + "-"
        runs = [r for r in runs if r["run_id"].startswith(season_prefix)] or runs
        self.manifest = self.store.load(self.run_id, "manifest.json") or {}
        self.board_doc = self.store.load(self.run_id, "leaderboard.json") or {}
        self.leaderboard: List[dict] = self.board_doc.get("leaderboard", [])
        self.market = self.store.load(self.run_id, "market_report.json") or {}
        self.factors = self.store.load(self.run_id, "factor_report.json") or {}
        self.participants = self.store.load(self.run_id, "participants.json") or []
        self.irregularities = self.store.load(self.run_id, "irregularities.json") or []
        self.reports: Dict[str, dict] = {r["username"]: r
                                         for r in self.store.reports(self.run_id)}
        self.scenarios: List[dict] = []
        for r in runs:
            man = self.store.load(r["run_id"], "manifest.json") or {}
            self.scenarios.append({
                "run_id": r["run_id"], "seed": man.get("seed"),
                "market": self.store.load(r["run_id"], "market_report.json") or {},
                "leaderboard": (self.store.load(r["run_id"], "leaderboard.json")
                                or {}).get("leaderboard", []),
                "reports": self.store.reports(r["run_id"]),
            })
        self.market_data = self.store.load(self.run_id, "market_data.json") or {}
        self.equity_panel = self.store.equity_panel(self.run_id)
        self.panel = self._robustness()

    @staticmethod
    def _pick_primary(run_ids: Sequence[str]) -> str:
        """The run Season 1's pages are built from.

        Prefers a ``season1-*primary*`` run when the store holds more than one
        season, because ``season2-...`` sorts after ``season1-...`` and would
        otherwise be picked by a plain sort.
        """
        prim = [r for r in run_ids if "primary" in r]
        season1 = [r for r in prim if r.startswith("season1")]
        if season1:
            return sorted(season1)[-1]
        return sorted(prim)[-1] if prim else run_ids[-1]

    def _robustness(self) -> Dict[str, dict]:
        from sim import analytics
        rows = analytics.robustness_panel(self.scenarios)["by_participant"]
        return {r["username"]: r for r in rows}

    def scenario_row(self, username: str) -> List[Tuple[int, float]]:
        out = []
        for i, sc in enumerate(self.scenarios):
            for r in sc["reports"]:
                if r["username"] == username:
                    out.append((i, r["total_return_pct"]))
        return out

    def spec(self, username: str) -> dict:
        for p in self.participants:
            if p.get("username") == username:
                return p
        return {}

    def curve(self, username: str) -> List[Tuple[str, float]]:
        rows = self.store.stream(self.run_id, "equity").where(
            participant=username).list()
        return [(r["date"], r["equity"]) for r in rows]

    def index_curve(self) -> List[Tuple[str, float]]:
        md = self.market_data
        if not md:
            return []
        t0 = md.get("first_competition_index", 0)
        spx = md.get("spx", [])
        dates = md.get("dates", [])
        base = spx[t0] if len(spx) > t0 and spx[t0] else 1.0
        return [(dates[i], 100_000.0 * spx[i] / base)
                for i in range(t0, min(len(dates), len(spx)))]


# ==========================================================================
# Pages
# ==========================================================================

def build_index(d: SiteData) -> str:
    m = d.market
    board = d.leaderboard
    top3 = board[:3]
    bottom = board[-1] if board else None
    man = d.manifest
    timing = man.get("timing", {})
    stats = d.store.fill_stats(d.run_id)

    kpis = [
        ("Participants", f"{len(board)}", "unique usernames, unique strategies"),
        ("Sessions traded", f"{m.get('window', {}).get('sessions', 0)}",
         f"{man.get('competition', {}).get('start')} → "
         f"{man.get('competition', {}).get('end')}"),
        ("Starting capital", f"${man.get('competition', {}).get('starting_cash', 0):,.0f}",
         "per participant, paper money"),
        ("S&P 500 over the window", f"{m.get('spx_return_pct', 0):+.2f}%",
         "real FRED SP500 series"),
        ("Winner", top3[0]["username"] if top3 else "-",
         f"{top3[0]['total_return_pct']:+.2f}%" if top3 else ""),
        ("Worst", bottom["username"] if bottom else "-",
         f"{bottom['total_return_pct']:+.2f}%" if bottom else ""),
        ("Orders executed", f"{stats['fills']:,}",
         f"${stats['total_execution_cost_usd']:,.0f} of modelled execution cost"),
        ("Scenarios", f"{len(d.scenarios)}",
         "same real market path, different idiosyncratic draws"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-label">{ESC(k)}</div>'
        f'<div class="kpi-value">{ESC(v)}</div>'
        f'<div class="kpi-sub">{ESC(s)}</div></div>' for k, v, s in kpis)

    rows = []
    for r in board[:10]:
        rows.append([
            f'<a href="participants/{_slug(r["username"])}.html">'
            f'{ESC(r["username"])}</a>',
            ESC(r["archetype"]),
            signed(r["total_return_pct"]),
            money(r["net_pnl_usd"]),
            signed(r["max_drawdown_pct"]),
            num(r["sharpe"], F2),
            num(r["closed_trades"], F0),
            sparkline([e for _, e in d.curve(r["username"])][::5]),
        ])
    board_html = table(
        ["Rank / username", "Archetype", "Return", "Net P&amp;L ($)", "Max DD",
         "Sharpe", "Trades", "Equity path"], rows)

    body = f"""
<section class="hero">
  <h1>Twenty strategies. One real market year. Every trade auditable.</h1>
  <p class="lede">StockPaperSim runs a <strong>one-year paper-trading stock
  competition</strong> in the style of TradingView's <em>The Leap</em>, Trade
  Ideas' <em>PM Challenge</em> and the CandleCharts <em>Showdown</em>: each
  participant gets {man.get('competition', {}).get('starting_cash', 0):,.0f} USD
  of paper capital, a unique username, and a written strategy. They are ranked on
  total return. What makes this one different is that the market they trade is a
  <strong>replay of the real S&amp;P 500 and VIX path</strong>, their orders go
  through a <strong>quoted-spread, depth, impact and regulatory-fee
  model</strong>, and <strong>every order, fill, rejection, mark and margin
  event is written to memory</strong> for later analysis.</p>
  <p class="lede">None of these participants manages risk. That was the design
  brief: each strategy is built to <strong>maximise return only</strong>. The
  consequence is on full display below - the winner is up
  {top3[0]['total_return_pct']:+.0f}% with a
  {abs(top3[0]['max_drawdown_pct']):.0f}% drawdown, and the last place
  participant lost {abs(bottom['total_return_pct']):.0f}% of the account.</p>
</section>

<section class="kpis">{kpi_html}</section>

{card("Top 10 leaderboard", board_html + '<p class="more"><a href="leaderboard.html">Full leaderboard with every metric →</a></p>')}

{card("What the market did (real data)", _market_summary_html(d))}

{card("The three questions this site answers", '''
<ol class="questions">
<li><strong>Who won, and by how much?</strong> The
<a href="leaderboard.html">leaderboard</a> ranks all
''' + str(len(board)) + ''' participants on total return, with drawdown, Sharpe,
beta, trade count, win rate, profit factor, execution cost and turnover beside
each one.</li>
<li><strong>Why did each strategy make or lose money?</strong> Every
<a href="participants/">participant page</a> carries a generated post-mortem:
the return decomposed into beta and residual, the style premia that were
actually live in this window, the symbols that paid and the symbols that
hurt, the cost of execution, and an explicit answer to
<em>"did it work, and why?"</em>.</li>
<li><strong>How much of the result is luck?</strong> One calendar year is one
draw. The <a href="market.html#robustness">robustness panel</a> re-runs the
same competition five more times on the same real index path with different
idiosyncratic draws, and reports mean, median, best and worst per
participant.</li>
</ol>''')}

{card("Honesty about the data", f'''
<p>This project was built under a hard constraint: <strong>no invented
numbers</strong>. Every figure is either a real published observation, a
simulation explicitly labelled as one, or a computation over the two. The
<a href="data.html">data provenance</a> page lists, field by field, which is
which; the <a href="sources.html">source register</a> lists all
{len(config.all_verified_sources())} external references with the verification
status of each, including {sum(1 for r in config.all_verified_sources() if r.get("status") == "KNOWN-NOT-FETCHED")}
academic citations that could <em>not</em> be retrieved inside the build
environment and are therefore marked
{status_badge("KNOWN-NOT-FETCHED")} for manual review.</p>
<p>Anything unusual found along the way - a VIX print on a market holiday, a
regulatory fee tier that is adopted but not yet operative, a calibration choice
that trades one kind of accuracy for another - is written down in
<a href="irregularities.html">Irregularities</a> rather than quietly resolved.
What the simulation cannot do at all is in
<a href="limitations.html">Limitations</a>.</p>
<p class="muted">Run <code>{ESC(str(d.run_id))}</code> · generated
{ESC(str(man.get("finalised_utc", "")))} · {ESC(str(timing.get("elapsed_seconds", "")))}s
of compute for the primary scenario · {provenance_sentence(man)}</p>
''')}
"""
    return page("Overview", body, "index.html")



def provenance_sentence(manifest: dict) -> str:
    """Say honestly which code produced the published run.

    A commit id on its own is not provenance. A season can be - and in this
    repository's history was - generated from a working tree carrying uncommitted
    changes, in which case the recorded commit is the one the run was *based on*
    and not the code that actually ran. The run manifest distinguishes three cases
    (`code.git.dirty` is True, False, or None when git could not be consulted at
    all) and carries a SHA-256 of every engine module, so the footer states which
    of the two a reader is holding instead of implying a guarantee the commit
    string cannot give - least of all after a squash merge, when main never
    contains the PR-head commit a manifest names.
    """
    code = manifest.get("code") or {}
    git = code.get("git") or {}
    n_hashes = len(code.get("python_module_hashes") or {})
    hash_note = (f"{n_hashes} per-module SHA-256 source hashes recorded in the "
                 f"manifest" if n_hashes else "no per-module source hashes in the "
                 f"manifest")
    commit = str(git.get("commit") or "")
    lead = (f"git commit <code>{ESC(commit[:12])}</code>" if commit
            else "no git provenance recorded for this run")
    dirty = git.get("dirty")
    if dirty is None:
        note = (f"its tree state could <strong>not be checked</strong>, so nothing "
                f"here claims a match with any commit; all that pins the code is "
                f"{hash_note}")
    elif dirty:
        branch = ESC(str(git.get("branch") or "an unnamed branch"))
        note = (f"<strong>generated from a modified working tree</strong> on "
                f"{branch}: that commit is what the run was based on, not the whole "
                f"of the code that ran, so reproduce from {hash_note}, or re-run it")
    else:
        note = f"generated from a clean tree, with {hash_note}"
    return f"{lead} &middot; {note}"


def _market_summary_html(d: SiteData) -> str:
    m = d.market
    if not m:
        return "<p>No market report in memory.</p>"
    vix = m.get("vix", {})
    cs = m.get("cross_section", {})
    eps = m.get("drawdown_episodes", [])
    ep_rows = [[ESC(e["peak_date"]), ESC(e["trough_date"]),
                ESC(e.get("recovery_date") or "not recovered"),
                signed(e["depth_pct"]), num(e["sessions_peak_to_trough"], F0P),
                signed(e.get("rebound_pct"))] for e in eps]
    per_sym = sorted(cs.get("per_symbol_pct", {}).items(), key=lambda kv: -kv[1])
    chart = bar_chart([(s, v) for s, v in per_sym], title="",
                      y_format=FC0SP)
    return f"""
<div class="two-col">
  <div>
    <table class="data compact">
      <tr><th>S&amp;P 500 (real FRED SP500)</th><td>{signed(m.get('spx_return_pct'))}</td></tr>
      <tr><th>Annualised index volatility</th><td>{num(m.get('spx_annualised_vol_pct'), F2)}%</td></tr>
      <tr><th>Up / down sessions</th><td>{m.get('up_sessions')} / {m.get('down_sessions')}</td></tr>
      <tr><th>Worst index drawdown</th><td>{signed(m.get('max_drawdown', {}).get('max_drawdown_pct'))}
          ({ESC(m.get('max_drawdown', {}).get('peak_date', ''))} →
          {ESC(m.get('max_drawdown', {}).get('trough_date', ''))})</td></tr>
      <tr><th>VIX mean / max / min</th><td>{num(vix.get('mean'), F2)} /
          {num(vix.get('max'), F2)} / {num(vix.get('min'), F2)}</td></tr>
      <tr><th>VIX peak date</th><td>{ESC(vix.get('max_date', ''))}</td></tr>
      <tr><th>Sessions with VIX &gt; 25 / &gt; 30</th><td>{vix.get('sessions_above_25')} /
          {vix.get('sessions_above_30')}</td></tr>
      <tr><th>Cross-sectional dispersion (stdev of 1y returns)</th>
          <td>{num(cs.get('dispersion_stdev_pp'), F1)} pp</td></tr>
      <tr><th>Best − worst single name</th><td>{num(cs.get('spread_best_minus_worst_pp'), F1)} pp</td></tr>
    </table>
    <h3>Largest index drawdown episodes</h3>
    {table(["Peak", "Trough", "Recovered", "Depth", "Sessions down", "Rebound"], ep_rows)}
  </div>
  <div>
    <h3>One-year price return by instrument</h3>
    {chart}
    <p class="muted small">ETFs and single names from the 17-instrument universe.
    SPY and AAPL are anchored to real observed prices at both ends of the window;
    the other names are simulated from the real index factor, the real VIX regime
    and <em>declared</em> scenario drift and volatility - see
    <a href="data.html">data provenance</a>.</p>
  </div>
</div>
"""


def build_leaderboard(d: SiteData) -> str:
    rows = []
    for r in d.leaderboard:
        rep = d.reports.get(r["username"], {})
        rows.append([
            f'<strong>{r["rank"]}</strong>',
            f'<a href="participants/{_slug(r["username"])}.html">{ESC(r["username"])}</a>',
            ESC(r["archetype"]),
            signed(r["total_return_pct"]),
            money(r["net_pnl_usd"]),
            num(r["final_equity"], F0),
            signed(r["max_drawdown_pct"]),
            num(r["sharpe"], F2),
            num(r.get("sortino"), F2),
            num(r["beta"], F2),
            signed(r["alpha_annual_pct"], 1),
            num(r["closed_trades"], F0),
            num(r["win_rate_pct"], F1),
            pf_cell(r["profit_factor"]),
            num(r["execution_cost_pct"], F2),
            num(r["turnover_x"], F1),
            num(r["margin_calls"], F0P),
            f'<span class="verdict">{ESC(r["verdict"])}</span>',
        ])
    spx = (d.market or {}).get("spx_return_pct")
    window = (d.market or {}).get("window", {})
    beat = sum(1 for r in d.leaderboard
               if spx is not None and r["total_return_pct"] > spx)
    winner = d.leaderboard[0] if d.leaderboard else {}
    kpis = f"""
<div class="kpis">
  <div class="kpi">
    <div class="kpi-label">Benchmark &middot; S&amp;P 500 (real FRED SP500)</div>
    <div class="kpi-value">{signed(spx)}</div>
    <div class="kpi-sub">{ESC(window.get('start', ''))} &rarr;
      {ESC(window.get('end', ''))} &middot; {num(window.get('sessions'), F0P)}
      sessions</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Participants beating the index</div>
    <div class="kpi-value">{beat} / {len(d.leaderboard)}</div>
    <div class="kpi-sub">on total return over the same window</div>
  </div>
  <div class="kpi">
    <div class="kpi-label">Season winner</div>
    <div class="kpi-value">{ESC(winner.get('username', ''))}</div>
    <div class="kpi-sub">{signed(winner.get('total_return_pct'))} &middot;
      {money(winner.get('net_pnl_usd'))}</div>
  </div>
</div>
"""
    body = f"""
<h1>Leaderboard</h1>
{kpis}
<p class="lede">All {len(d.leaderboard)} participants, ranked on
<strong>total return</strong> - the metric used by the competitions this project
reverse-engineered (TradingView's The Leap ranks on realised P&amp;L; Trade
Ideas' PM Challenge on Total Profit). Open positions are force-liquidated on the
final session and the liquidation is costed through the venue, so
&ldquo;total return&rdquo; is fully realised.</p>
<div class="table-scroll">
{table(["#", "Username", "Archetype", "Return", "Net P&amp;L $", "Final equity",
        "Max DD", "Sharpe", "Sortino", "Beta", "Alpha/yr", "Trades", "Win %",
        "Profit factor", "Exec cost %", "Turnover", "Margin calls", "Verdict"], rows)}
</div>
<p class="muted small">Alpha/yr is the annualised intercept of a daily-return
regression on the real S&amp;P 500 return. Sharpe and Sortino use a zero
risk-free rate by design (documented in
<a href="methodology.html">methodology</a>). Execution cost % is total modelled
friction - spread, depth, impact, exchange fees, SEC and FINRA fees, net of
maker rebates - as a percentage of starting capital; it can be negative for a
net liquidity provider.</p>

<h2>Read this table with the robustness panel open</h2>
<p>A single 251-session path is a single draw. The table below re-runs the
identical competition on {len(d.scenarios)} different idiosyncratic realisations
of the same real index path. Participants whose result holds up across all of
them are separating themselves from the noise; participants whose sign flips are
not.</p>
{_robustness_table(d)}
"""
    return page("Leaderboard", body, "leaderboard.html")


def _robustness_table(d: SiteData) -> str:
    rows = []
    for r in sorted(d.panel.values(), key=lambda x: -x["mean_return_pct"]):
        vals = d.scenario_row(r["username"])
        cells = "".join(f'<td>{signed(v, 1)}</td>' for _, v in vals)
        rows.append([
            f'<a href="participants/{_slug(r["username"])}.html">{ESC(r["username"])}</a>',
            num(r["mean_return_pct"], F1S),
            num(r["median_return_pct"], F1S),
            num(r["best_return_pct"], F1S),
            num(r["worst_return_pct"], F1S),
            num(r["stdev_pp"], F1),
            f'{r["positive_scenarios"]}/{r["scenarios"]}',
            f'{r["beat_index_scenarios"]}/{r["scenarios"]}',
            cells,
        ])
    heads = [f'S{i + 1}<br><span class="muted small">{sc["seed"]}</span>'
             for i, sc in enumerate(d.scenarios)]
    head = ["Username", "Mean", "Median", "Best", "Worst", "Stdev (pp)",
            "Profitable", "Beat index"] + heads
    out = ['<div class="table-scroll">', table(head, rows), "</div>"]
    out.append('<p class="muted small">S1 is the primary scenario reported '
               'everywhere else on this site. Every scenario replays the same '
               'real FRED S&amp;P 500 and VIX path; only the idiosyncratic '
               'return draws and the venue noise differ.</p>')
    return "".join(out)


def build_strategies(d: SiteData) -> str:
    cards = []
    for p in d.participants:
        u = p["username"]
        rep = d.reports.get(u, {})
        basis = "".join(
            f'<li><a href="{ESC(b.get("url", "#"))}" rel="noopener noreferrer" '
            f'target="_blank">{ESC(b.get("ref", b.get("url", "")))}</a> '
            f'{status_badge(b.get("status", "KNOWN-NOT-FETCHED"))}<br>'
            f'<span class="muted small">{ESC(b.get("claim", ""))}</span></li>'
            for b in p.get("academic_basis", []))
        entry = "".join(f"<li>{ESC(x)}</li>" for x in p.get("entry_rules", []))
        exit_ = "".join(f"<li>{ESC(x)}</li>" for x in p.get("exit_rules", []))
        fmodes = "".join(f"<li>{ESC(x)}</li>" for x in p.get("known_failure_modes", []))
        fx = p.get("factor_exposure") or {}
        fx_html = ", ".join(f"{k} {v:+.1f}" for k, v in sorted(fx.items())) or "-"
        ret = rep.get("total_return_pct")
        rank = next((r["rank"] for r in d.leaderboard if r["username"] == u), None)
        cards.append(f"""
<article class="strategy" id="{ESC(_slug(u))}">
  <header>
    <h3><a href="participants/{_slug(u)}.html">{ESC(u)}</a>
        <span class="muted">· {ESC(p.get("display_name", ""))}</span></h3>
    <div class="strategy-meta">
      {badge(p.get("archetype", ""), "info")}
      {badge("aggression " + str(p.get("aggression", "-")), "warn")}
      {badge("rank " + str(rank), "ok") if rank else ""}
      {signed(ret) if ret is not None else ""}
    </div>
  </header>
  <p><strong>Thesis.</strong> {ESC(p.get("thesis", ""))}</p>
  <p><strong>Why it is return-seeking rather than risk-managed.</strong>
     {ESC(p.get("why_return_seeking", ""))}</p>
  <div class="two-col">
    <div><h4>Entry rules</h4><ul>{entry}</ul></div>
    <div><h4>Exit rules</h4><ul>{exit_}</ul></div>
  </div>
  <table class="data compact">
    <tr><th>Sizing</th><td>{ESC(p.get("sizing", ""))}</td></tr>
    <tr><th>Leverage</th><td>{ESC(p.get("leverage", ""))}</td></tr>
    <tr><th>Cadence / horizon</th><td>{ESC(p.get("cadence", ""))} · {ESC(p.get("horizon", ""))}</td></tr>
    <tr><th>Declared factor loadings</th><td><code>{ESC(fx_html)}</code></td></tr>
  </table>
  <details><summary>Known failure modes (declared before the season)</summary>
    <ul>{fmodes}</ul></details>
  <details><summary>Academic basis ({len(p.get("academic_basis", []))} references)</summary>
    <ul class="refs">{basis}</ul></details>
</article>""")
    body = f"""
<h1>The roster: {len(d.participants)} strategies, {len(d.participants)} usernames</h1>
<p class="lede">Each participant is a distinct, self-contained strategy with its
own username, its own written rules and its own declared failure modes. The
brief was explicit: <strong>maximise return, do not build risk management
in</strong>. Position sizing exists (a competition cannot run without it) but
there are no stop-loss budgets, no VaR limits, no volatility targets and no
drawdown brakes anywhere in this roster - except where a strategy's edge
<em>is</em> a volatility signal.</p>
<p class="muted small">Archetypes span cross-sectional and time-series momentum,
short-term and long-horizon reversal, channel breakouts, post-event drift,
liquidity squeezes, levered beta, sector rotation, statistical arbitrage,
passive market making, volatility risk premium, VIX regime timing, overnight
carry, concentration, illiquidity-seeking aggression, buy-and-hold as a control,
a signal-stacking &ldquo;kitchen sink&rdquo;, and a deep-value contrarian.</p>
{"".join(cards)}
"""
    return page("Strategies", body, "strategies.html")


def build_participant(d: SiteData, username: str) -> str:
    rep = d.reports.get(username)
    if not rep:
        return page(username, "<h1>Not found</h1>")
    spec = d.spec(username)
    n = rep["narrative"]
    r = rep["risk"]
    m = rep["market_relation"]
    t = rep["trades"]
    c = rep["costs"]
    e = rep["exposure"]
    hold_note = ""
    if t.get("tranche_intervals_overlap"):
        hold_note = ('<p class="muted small">Holding periods are measured per closed '
                     'tranche of an average-cost lot, so tranches of the same lot share '
                     'its entry date and their intervals overlap - they must not be '
                     f'summed. The union is {t.get("sessions_in_market", 0):,} of the '
                     f'{d.manifest.get("session_count", 0)} sessions traded.</p>')
    isf = rep["implementation_shortfall"]
    dec = rep["pnl_decomposition"]
    curve = d.curve(username)
    idx = d.index_curve()
    rank = next((x["rank"] for x in d.leaderboard if x["username"] == username), None)

    equity_chart = line_chart(
        [(username, curve), ("S&P 500 scaled to $100,000", idx)],
        title="Equity curve vs the real S&P 500 (both scaled to $100,000 at the open)",
        y_format=FD0)
    dd_rows = []
    peak = -1e18
    for date, eq in curve:
        peak = max(peak, eq)
        dd_rows.append((date, 100.0 * (eq / peak - 1.0) if peak else 0.0))
    dd_chart = line_chart([("Drawdown (%)", dd_rows[::2])], height=200,
                          title="Drawdown from the running equity peak",
                          y_format=F0PP, baseline=0)
    monthly = [(x["month"][2:], x["return_pct"]) for x in rep["monthly_returns"]]
    monthly_chart = bar_chart(monthly, height=240, y_format=FC0SP,
                              title="Monthly return (%)")
    contrib = rep["contribution_by_symbol"]
    contrib_chart = hbar_chart([(x["symbol"], x["total_pnl"]) for x in contrib],
                               title="P&L contribution by instrument (USD)")
    scen = d.scenario_row(username)
    scen_chart = bar_chart([(f"S{i + 1}", v) for i, v in scen], height=200,
                           y_format=FC0SP,
                           title="Total return in each scenario (%)")

    prose = "".join(
        f'<section class="prose-block"><h3>{ESC(p["title"])}</h3>'
        f'<p>{ESC(p["text"])}</p></section>' for p in n["prose"])

    kpis = [
        ("Rank", f"#{rank}" if rank else "-", "of " + str(len(d.leaderboard))),
        ("Total return", f"{rep['total_return_pct']:+.2f}%",
         f"{rep['net_pnl_usd']:+,.0f} USD"),
        ("Final equity", f"{rep['final_equity']:,.0f}",
         f"from {rep['starting_cash']:,.0f}"),
        ("Max drawdown", f"{r['max_drawdown_pct']:.2f}%",
         f"trough {r.get('trough_date', '-')}"),
        ("Sharpe / Sortino", f"{r['sharpe']:.2f} / {r['sortino']:.2f}", "rf = 0"),
        ("Beta / alpha", f"{m['beta']:.2f} / {m['alpha_annual_pct']:+.1f}%",
         f"R² {m['r_squared']:.2f}"),
        ("Closed trades", f"{t['closed_trades']}",
         f"{t['win_rate_pct']:.1f}% winners"),
        ("Execution cost", f"{c['total_cost_pct_of_starting_cash']:.2f}%",
         f"{c['avg_execution_cost_bps_per_fill']:.2f} bp per fill"),
    ]
    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-label">{ESC(k)}</div>'
        f'<div class="kpi-value">{ESC(v)}</div>'
        f'<div class="kpi-sub">{ESC(s)}</div></div>' for k, v, s in kpis)

    contrib_rows = [[ESC(x["symbol"]), money(x["realized_pnl"]),
                     money(x["open_pnl"]), money(x["total_pnl"]),
                     num(x["closed_trades"], F0P), num(x["fees"], FC2)]
                    for x in contrib]
    monthly_rows = [[ESC(x["month"]), num(x["sessions"], F0P),
                     num(x["start_equity"], F0), num(x["end_equity"], F0),
                     signed(x["return_pct"])] for x in rep["monthly_returns"]]
    isf_rows = [[ESC(str(k).replace("_", " ")), num(v, FC2) if isinstance(v, float)
                 else ESC(str(v))] for k, v in isf.items() if k != "note"]
    cost_rows = [
        ["Spread (crossing the quoted bid/offer)", money(c["spread_cost_usd"])],
        ["Depth (walking the displayed ladder)", money(c["depth_cost_usd"])],
        ["Impact (square-root law on ADV)", money(c["impact_cost_usd"])],
        ["Commission", money(c["commission_usd"])],
        ["Exchange / access fees", money(c["exchange_fee_usd"])],
        ["Regulatory fees (SEC §31 + FINRA TAF)", money(c["regulatory_fee_usd"])],
        ["Maker rebates earned", money(c["rebate_usd"])],
        ["<strong>All-in friction</strong>",
         f'<strong>{money(c["total_cost_usd"])}</strong>'],
        ["Intraday drift (timing, not a fee)", money(c["intraday_drift_usd"])],
    ]

    body = f"""
<nav class="crumbs"><a href="../index.html">Overview</a> ·
 <a href="../leaderboard.html">Leaderboard</a> ·
 <a href="../strategies.html#{ESC(_slug(username))}">Strategy spec</a></nav>
<h1>{ESC(username)} <span class="muted">· {ESC(spec.get('display_name', ''))}</span></h1>
<div class="strategy-meta">
  {badge(spec.get('archetype', ''), 'info')}
  {badge('aggression ' + str(spec.get('aggression', '-')), 'warn')}
  {badge(n['verdict'], 'ok' if rep['total_return_pct'] > 0 else 'neg')}
</div>
<section class="kpis">{kpi_html}</section>

{card("Post-mortem: why it worked, or why it did not", prose)}

{card("Equity path", equity_chart + dd_chart)}

<div class="two-col">
  {card("Monthly returns", monthly_chart + table(["Month", "Sessions", "Start equity", "End equity", "Return"], monthly_rows))}
  {card("Scenario robustness", scen_chart + f'''<p class="muted small">Mean
  {num(d.panel.get(username, {}).get("mean_return_pct"), F1S)}% across
  {len(d.scenarios)} scenarios, median
  {num(d.panel.get(username, {}).get("median_return_pct"), F1S)}%, best
  {num(d.panel.get(username, {}).get("best_return_pct"), F1S)}%, worst
  {num(d.panel.get(username, {}).get("worst_return_pct"), "{:+.1f")}%.
  Profitable in {d.panel.get(username, {}).get("positive_scenarios", 0)} of
  {len(d.scenarios)}; beat the index in
  {d.panel.get(username, {}).get("beat_index_scenarios", 0)}.</p>''')}
</div>

{card("Where the money came from", contrib_chart + table(
    ["Instrument", "Realised P&amp;L", "Open P&amp;L", "Total", "Closed trades", "Fees"],
    contrib_rows))}

<div class="two-col">
{card("P&amp;L decomposition", table(["Bucket", "USD"], [
    ["Realised trading P&amp;L", money(dec["realized_trading_pnl_usd"])],
    ["Open-position P&amp;L at the final mark", money(dec["open_position_pnl_usd"])],
    ["Cash dividends received on longs", money(dec["dividends_usd"])],
    ["Manufactured dividends paid on shorts",
     money(dec.get("dividends_in_lieu_usd", 0.0))],
    ["Borrow fees paid on shorts", money(dec["borrow_fees_usd"])],
    ["<strong>Total net P&amp;L</strong>", f'<strong>{money(dec["total_net_pnl_usd"])}</strong>'],
    ["Unexplained residual (fees/rounding)", money(dec["unexplained_residual_usd"])],
]) + f'<p class="muted small">{ESC(dec["note"])}</p>')}

{card("Execution cost breakdown", table(["Component", "USD"], cost_rows) + f'''
<p class="muted small">{num(c["fills"], F0P)} fills,
{num(c["shares_traded"], F0)} shares,
{num(c["traded_notional_usd"], FD0)} traded.
{cost_split_sentence(c)} Annualised turnover
{num(rep["turnover"]["annualised_turnover_x"], F1)}x.</p>''')}
</div>

<div class="two-col">
{card("Implementation shortfall (Perold 1988)", table(["Component", "USD"], isf_rows)
      + f'<p class="muted small">{ESC(isf.get("note", ""))}</p>')}
{card("Risk realised", table(["Measure", "Value"], [
    ["Max drawdown", signed(r["max_drawdown_pct"])],
    ["Peak → trough", f'{ESC(str(r.get("peak_date")))} → {ESC(str(r.get("trough_date")))}'],
    ["Recovered", ESC(str(r.get("recovery_date") or "never"))],
    ["Daily volatility (annualised)", num(r["annualised_vol_pct"], F2) + "%"],
    ["Best / worst session", f'{signed(r["best_session_pct"])} / {signed(r["worst_session_pct"])}'],
    ["Skew / excess kurtosis", f'{num(r["skew"], F2)} / {num(r["excess_kurtosis"], F2)}'],
    ["VaR 95% (1 day, historical)", num(r["var_95_pct"], F2) + "%"],
    ["CVaR 95% (1 day, historical)", num(r["cvar_95_pct"], F2) + "%"],
    ["VaR 99% / CVaR 99%", f'{num(r["var_99_pct"], F2)}% / {num(r["cvar_99_pct"], F2)}%'],
    ["Average gross exposure", num(e["avg_gross_pct_of_starting_cash"], F0P) + "%"],
    ["Peak gross exposure", num(e["max_gross_pct_of_starting_cash"], F0P) + "%"],
    ["Peak leverage", num(e["max_leverage"], F2) + "x"],
    ["Sessions flat", num(e["sessions_flat"], F0P)],
    ["Maintenance-margin breaches", num(rep["carry"]["margin_call_count"], F0P)],
    ["Intraday round trips (PDT definition)",
     num(rep["carry"].get("day_trade_count", 0), F0P)
     + f' &middot; {num(rep["carry"]["day_trades"], F0P)} session(s)'],
    ["Manufactured dividends paid on shorts",
     money(rep["carry"].get("dividends_in_lieu_paid_usd", 0.0))],
    ["Cash dividends received on longs", money(rep["carry"]["dividends_received_usd"])],
]))}
</div>

{card("Trade statistics", table(["Measure", "Value"], [
    ["Closed round trips", num(t["closed_trades"], F0P)],
    ["Open at the final mark", num(t["open_trades"], F0P)],
    ["Win rate", num(t["win_rate_pct"], F1) + "%"],
    ["Profit factor", pf_cell(t["profit_factor"])],
    ["Average winner", money(t["avg_win_usd"])],
    ["Average loser", money(t["avg_loss_usd"])],
    ["Expectancy per trade", money(t["expectancy_usd_per_trade"])],
    ["Median holding period", num(t["median_sessions_held"], F0P) + " sessions"],
    ["Sessions in market (union, no double counting)",
     num(t.get("sessions_in_market"), F0P) + " sessions"],
    ["Longest win / loss streak", f'{t["longest_win_streak"]} / {t["longest_loss_streak"]}'],
    ["Best trade", _trade_cell(t["best_trade"])],
    ["Worst trade", _trade_cell(t["worst_trade"])],
])) + hold_note}

{card("Declared failure modes: what fired, and what this run cannot test",
      failure_mode_block(n))}

{card("The strategy as declared before the season", f'''
<p><strong>Thesis.</strong> {ESC(spec.get("thesis", ""))}</p>
<p><strong>Why return-seeking.</strong> {ESC(spec.get("why_return_seeking", ""))}</p>
<div class="two-col"><div><h4>Entry</h4><ul>
{"".join(f"<li>{ESC(x)}</li>" for x in spec.get("entry_rules", []))}</ul></div>
<div><h4>Exit</h4><ul>
{"".join(f"<li>{ESC(x)}</li>" for x in spec.get("exit_rules", []))}</ul></div></div>
<p class="muted small">Sizing: {ESC(spec.get("sizing", ""))} · Leverage:
{ESC(spec.get("leverage", ""))} · Cadence: {ESC(spec.get("cadence", ""))} ·
Horizon: {ESC(spec.get("horizon", ""))}</p>
''')}
"""
    return page(username, body, "participants/index.html", depth=1)


def _trade_cell(tr: Optional[dict]) -> str:
    if not tr:
        return '<span class="na">none</span>'
    return (f'{ESC(tr["symbol"])} {ESC(tr["direction"])} '
            f'{ESC(str(tr["entry_date"]))} → {ESC(str(tr["exit_date"]))}: '
            f'{money(tr["net_pnl"])}')


def build_market(d: SiteData) -> str:
    m, f = d.market, d.factors
    frows = []
    for key in ("market", "momentum", "reversal", "beta", "low_vol", "liquidity"):
        row = f.get(key, {})
        frows.append([f'<strong>{ESC(key.upper())}</strong>',
                      ESC(row.get("description", "")),
                      signed(row.get("cumulative_return_pct")),
                      num(row.get("mean_daily_bps"), F1S),
                      num(row.get("annualised_vol_pct"), F1),
                      num(row.get("sharpe"), F2)])
    factor_chart = bar_chart([(k.upper(), f.get(k, {}).get("cumulative_return_pct", 0.0))
                              for k in ("momentum", "reversal", "beta", "low_vol",
                                        "liquidity")],
                             height=240, y_format=FC0SP,
                             title="Realised long/short factor return over the season (%)")
    idx = d.index_curve()
    idx_chart = line_chart([("S&P 500 scaled to $100,000", idx)],
                           title="The real market path every participant traded on (FRED SP500)",
                           y_format=FD0)
    vix_rows = []
    md = d.market_data
    if md:
        t0 = md.get("first_competition_index", 0)
        dates, vix = md.get("dates", []), md.get("vix", [])
        pts = [(dates[i], vix[i]) for i in range(t0, min(len(dates), len(vix)))]
        vix_chart = line_chart([("VIX close", pts)], height=220,
                               title="Real CBOE VIX close (FRED VIXCLS)",
                               y_format=F0P)
    else:
        vix_chart = ""
    sess_rows = [[ESC(x["date"]), signed(x["spx_return_pct"])]
                 for x in m.get("worst_sessions", [])]
    best_rows = [[ESC(x["date"]), signed(x["spx_return_pct"])]
                 for x in m.get("best_sessions", [])]

    body = f"""
<h1>The market, and the premia that were actually live</h1>
<p class="lede">Participants did not trade a synthetic random walk. They traded a
replay whose market factor is the <strong>real daily S&amp;P 500 close series
from FRED</strong> and whose volatility regime is the <strong>real daily VIX
close from FRED</strong>, over
{m.get('window', {}).get('sessions', 0)} sessions from
{ESC(str(m.get('window', {}).get('start')))} to
{ESC(str(m.get('window', {}).get('end')))}. Single-name daily paths are
simulated from those real factors plus declared scenario parameters, and SPY and
AAPL are anchored to real observed prices at both ends.</p>
{card("The real index path", idx_chart + vix_chart)}
{card("Season statistics", _market_summary_html(d))}
<div class="two-col">
{card("Worst index sessions", table(["Date", "Return"], sess_rows))}
{card("Best index sessions", table(["Date", "Return"], best_rows))}
</div>

<h2 id="factors">Realised style premia</h2>
<p>Each factor below is a dollar-neutral long/short tercile portfolio built from
the same replay the participants traded. They are the yardstick every
post-mortem uses: a strategy's narrative is checked against the premium it was
built to harvest, so &ldquo;the signal worked&rdquo; and &ldquo;the market
carried it&rdquo; can be told apart.</p>
{card("Factor panel", factor_chart + table(["Factor", "Construction", "Return", "Mean daily (bp)", "Ann. vol", "Sharpe"], frows))}
<div class="callout warn"><strong>IR-16 · read this before quoting factor
numbers.</strong> The cross-sectional drifts in this replay are
<em>declared scenario parameters</em> (see <code>sim/universe.py</code> and
<a href="data.html">data provenance</a>). A realised factor premium measured
here describes <em>this competition</em>, not the real market. The real,
long-horizon evidence for each premium is cited separately on the
<a href="strategies.html">strategy pages</a> with its verification status.</div>

<h2 id="robustness">Robustness: the same season, {len(d.scenarios)} times</h2>
<p>One calendar path is one draw from the idiosyncratic distribution. Every
scenario below replays the identical real index and VIX path and re-draws only
the idiosyncratic returns and the venue noise.</p>
{card("Robustness panel", _robustness_table(d))}
"""
    return page("Market & factors", body, "market.html")


def build_methodology(d: SiteData) -> str:
    cfg = config.CompetitionConfig()
    costs = cfg.costs
    liq = cfg.liquidity
    imp = cfg.impact
    mm = cfg.market_maker
    mar = cfg.margin
    # Every fee is a dated schedule; render all of it, including the earliest
    # documented rate, and say out loud where the documentation starts.
    sec31 = " · ".join(
        f"${rate:,.2f} per $1,000,000 of sell notional from {eff}"
        for eff, rate in config.SEC31_PER_MILLION
    ) or "not applicable in this window"
    taf = " · ".join(
        f"${rate:.6f} per share from {eff}"
        for eff, rate in config.FINRA_TAF_PER_SHARE
    )
    taf_cap = " · ".join(
        f"${cap:,.2f} per trade from {eff}"
        for eff, cap in config.FINRA_TAF_MAX_PER_TRADE
    )
    coverage = config.fee_coverage_start()
    _s0 = f"""

<h1>Methodology</h1>
<p class="lede">How a session is simulated, what is real, what is modelled, and
which choices were judgement calls. Every SIM CHOICE below is also written into
the source with the reason and, where one exists, the citation.</p>
"""
    _s1 = f"""
<h2>1 · Competition design, reverse-engineered from three real contests</h2>
{table(["Rule", "This competition", "Where it comes from"], [
    ["Starting capital", f"${cfg.starting_cash:,.0f} paper",
     'TradingView <a href="https://www.tradingview.com/the-leap/december-2025/rules/" rel="noopener" target="_blank">The Leap</a> presets accounts to $100,000 virtual'],
    ["Duration", "1 year (251 sessions)", "Project requirement; The Leap runs are calendar-bounded contests"],
    ["Ranking metric", "Total return % (fully realised)",
     'The Leap ranks on realised P&amp;L; <a href="https://www.trade-ideas.com/stock-trading-competition/" rel="noopener" target="_blank">Trade Ideas PM Challenge</a> ranks on Total Profit'],
    ["End-of-period positions", "Force-liquidated on the final session, costed through the venue",
     "The Leap: all open positions are closed at the end of the competition"],
    ["Leaderboard columns", "Rank, user, total profit, closed trades, account value, average profit/trade",
     "Trade Ideas PM Challenge schema"],
    ["Order-rate limits", "Not applicable (one decision point per session)",
     "The Leap bans &gt;60 orders/minute; a daily-bar engine cannot generate that"],
    ["Data delay", "None in the simulation; the real contests quote delayed data",
     'Trade Ideas states "Data delayed by 15 minutes"; CandleCharts hosts through TradingView'],
    ["Instrument whitelist", f"{len(universe.build_universe())} instruments (3 index ETFs, 14 single names)",
     "The Leap fixes an instrument whitelist with a maximum number of positions"],
    ["Leverage", f"{mar.max_gross_leverage:.1f}x gross cap, {mar.initial_margin:.0%} initial, {mar.maintenance_margin:.0%} maintenance",
     "The Leap allows 1:1 on stocks; this project widens it to 2x gross as a SIM CHOICE so return-seeking strategies can express leverage"],
])}
"""
    _s2 = f"""
<h2>2 · The market each participant trades</h2>
<ul>
<li><strong>Real factors.</strong> The market return series is FRED
<code>SP500</code>; the volatility regime is FRED <code>VIXCLS</code>. Both are
stored verbatim under <code>data/real/fred/</code>.</li>
<li><strong>Real anchors.</strong> SPY is derived from the real index level
through a ratio fitted on 13 real Yahoo monthly bars (max absolute error
{num((d.market_data.get("provenance") or {}).get("spy_ratio_max_abs_pct_error_vs_real_monthly_closes"), F3)}%);
AAPL starts and ends on real observed closes and pays four real dividends.</li>
<li><strong>Simulated single-name paths.</strong> Each name's daily log return is
<code>beta × market log return + idiosyncratic shock</code>, where the shock
scales with the real VIX relative to its window mean and is drawn from a
fat-tail mixture (4% of draws scaled ×2.6, renormalised to unit variance) after
<a href="https://doi.org/10.1080/713665679" rel="noopener" target="_blank">Cont
(2001)</a>.</li>
<li><strong>Intraday shape.</strong> Each daily bar is expanded into
{len(_interval_marks())} intervals that visit the open, high, low and close,
with a U-shaped volume profile after
<a href="https://doi.org/10.1093/rfs/1.1.3" rel="noopener" target="_blank">Admati
&amp; Pfleiderer (1988)</a>. Overnight gap share
{cfg_overnight_share()} of daily variance; wick size
{cfg_wick()} × daily sigma.</li>
<li><strong>Calendar.</strong> Sessions and closures come from the real FRED
series' own gaps, cross-checked against the Nasdaq holiday schedule; early
closes (13:00 ET) are modelled as shorter sessions with proportionally lower
volume.</li>
</ul>
"""
    _s3 = f"""
<h2>3 · Liquidity, quoting and market making</h2>
<ul>
<li><strong>Quoted spread.</strong> A whole number of Rule 612 minimum
increments, because that is what actually binds for liquid US equities:
<code>ticks = clamp(round({liq.spread_k_ticks} × sqrt(sigma_daily × sqrt(vix_factor) /
(tick / price))), min, max)</code> with per-liquidity-tier bounds
{json.dumps(liq.min_spread_ticks)} … {json.dumps(liq.max_spread_ticks)} and an
absolute cap in basis points. Result at the season open: 1 tick on SPY, NVDA,
AAPL, JPM, RIVN; 2 ticks on TSLA and CVNA.</li>
<li><strong>Depth.</strong> A {liq.book_levels}-level ladder, displayed size
{liq.touch_size_round_lots:.0f} round lots at the touch growing
×{liq.depth_growth:.2f} per level, with round lots taken from the tiered Rule
600(b)(93) definition.</li>
<li><strong>Market makers.</strong> {mm.num_makers} competing dealers per
instrument per session quote around an Avellaneda-Stoikov reservation price, so
inventory skews the ladder away from flow
(<a href="https://doi.org/10.1080/14697680701381228" rel="noopener" target="_blank">Avellaneda
&amp; Stoikov 2008</a>). Passive orders earn a
${costs.maker_rebate_per_share:.4f}/share rebate; aggressive orders pay
${costs.taker_fee_per_share:.4f}/share, at the Rule 610 access-fee cap.</li>
<li><strong>Non-displayed liquidity.</strong> Quantity beyond the displayed
ladder fills at the touch and is priced by the impact model rather than by
walking an artificially deep book, because roughly 45% of consolidated US equity
volume trades off-exchange
(<a href="https://otctransparency.finra.org/" rel="noopener" target="_blank">FINRA
OTC Transparency</a>, cited but not fetched here).</li>
<li><strong>Book integrity.</strong> The displayed book may never cross; a
defensive uncross step enforces it, as Rule 611 trade-through protection
implies.</li>
</ul>
"""
    _s4 = f"""
<h2>4 · Execution</h2>
<ul>
<li><strong>Order types.</strong> Market, limit (marketable or resting, with a
queue-position fill model) and stop (triggered off the intraday path, then
executed as a market order from the trigger interval).</li>
<li><strong>Slicing.</strong> Capacity-driven, not time-driven: an order trades
at the touch and only spans further intervals when its size exceeds what one
interval can absorb inside the
{liq.max_participation:.0%} participation limit. An earlier version spread every
market order across the whole session (a VWAP algorithm), which handed every
participant a free intraday average price; measured on one participant it was
worth −$1,857 of phantom &ldquo;drift benefit&rdquo; on $614k of paper
notional, and it was removed.</li>
<li><strong>Impact.</strong> Square-root law,
<code>{imp.coefficient} × sigma_daily × sqrt(shares / ADV)</code>, split
{imp.permanent_share:.0%} permanent / {1 - imp.permanent_share:.0%} temporary, with
{imp.impact_decay:.0%} of the permanent component decaying by the next session
(<a href="https://doi.org/10.21314/JOR.2001.041" rel="noopener" target="_blank">Almgren
&amp; Chriss 2001</a>; empirical calibration from Almgren, Thum, Hauptmann &amp;
Li 2005, <em>Risk</em> 18(7):58-62 - see IR-26).</li>
<li><strong>Fees are dated schedules, not constants.</strong> SEC §31 on sells:
{sec31}, per the
<a href="https://www.federalregister.gov/documents/2026-03-04/2026-04233/order-making-fiscal-year-2026-annual-adjustments-to-transaction-fee-rates" rel="noopener" target="_blank">FY2026
annual adjustment order</a>. FINRA Trade Activity Fee on sells: {taf}, capped at
{taf_cap} - rate and cap from a
<span class="tag tag-secondary">secondary source</span>, see IR-05. The earliest
date any of these schedules is documented for is <code>{coverage}</code>;
<code>config.validate_fee_coverage()</code> refuses to run a season that opens
before it, so no trade is ever costed at a rate this project could not cite. Commission
${costs.commission_per_order:.2f} per order and
${costs.commission_per_share:.4f} per share by default. Exchange access fees
are capped at ${config.ACCESS_FEE_CAP_PER_SHARE:.4f} per share under Rule 610.</li>
<li><strong>Short selling.</strong> Requires a locate: names flagged unborrowable
are rejected outright, and borrowable names accrue a daily fee at their declared
annual rate (Regulation SHO Rule 203(b)).</li>
<li><strong>Pre-trade risk.</strong> Every order - including ones a strategy
built by hand - is screened by the broker before it reaches the venue. Orders
that would breach the {mar.max_gross_leverage:.1f}x gross cap are resized down to
the largest permissible quantity, or rejected if not even one share fits. Orders
that strictly reduce a position are never blocked, so a book that drifts above
the cap through price movement can still be trimmed.</li>
</ul>
"""
    _s5 = f"""
<h2>5 · Accounting</h2>
<ul>
<li>Average-cost basis per instrument; realised P&amp;L on reduction, unrealised
on the mark; cash dividends on ex-dates; borrow fees daily on shorts.</li>
<li>Maintenance margin tested at every close. A breach is liquidated at the
<em>next</em> open (a daily-bar engine cannot liquidate intraday), and the event
is written to memory.</li>
<li>Day trades counted for pattern-day-trader visibility
(${mar.pdt_equity_threshold:,.0f} threshold), reported but not enforced: these
are paper accounts.</li>
</ul>
"""
    _s6 = f"""
<h2>6 · Analysis</h2>
<ul>
<li>Returns, CAGR, max drawdown (with peak/trough/recovery dates and sessions
underwater), Sharpe and Sortino
(<a href="https://doi.org/10.1086/260062" rel="noopener" target="_blank">Sharpe
1966</a>,
<a href="https://doi.org/10.2469/faj.v50.6.48" rel="noopener" target="_blank">Sortino
&amp; Price 1994</a>), Calmar, historical VaR/CVaR at 95% and 99%
(<a href="https://doi.org/10.1111/1467-9965.00068" rel="noopener" target="_blank">Artzner
et al. 1999</a>), skew and excess kurtosis.</li>
<li>Beta, alpha, R² and information ratio from a daily-return OLS on the real
index return, aligned session-for-session.</li>
<li>Trade reconstruction by average-cost round trip, including positions that
flip through zero (an earlier version dropped the flipped leg entirely and lost
$20,169 of one participant's P&amp;L from the attribution).</li>
<li>Implementation shortfall decomposed into spread, depth, impact, fees,
intraday drift and missed trades
(<a href="https://doi.org/10.2469/faj.v44.5.28" rel="noopener" target="_blank">Perold
1988</a>). The components are mutually exclusive by construction: for every
fill, spread + depth + impact + drift equals the signed price difference against
the decision mid, so the total cannot double count.</li>
<li>Rule-based post-mortems. Each sentence in a narrative fires only on a
measured quantity; the strategy's declared priors are used afterwards to
<em>label</em> the causes the numbers already identified, never to produce
them.</li>
</ul>
"""
    _s7 = f"""
<h2>7 · Determinism and memory</h2>
<ul>
<li>Every random draw is keyed by an explicit string (seed, participant, symbol,
date, order attributes) - never by wall-clock time or hash ordering. Two runs
with the same seed produce byte-identical memory.</li>
<li>Each participant trades against an <strong>independent replica</strong> of
the venue, so one participant's impact and dealer inventory cannot change
another's fills and the leaderboard does not depend on processing order. The
trade-off - participants' combined flow never clears one shared book - is
<a href="limitations.html">limitation L-04</a>.</li>
<li>Every order, fill, rejection, quote snapshot, daily mark, position snapshot,
carry payment and margin event is written to
<code>memory/runs/&lt;run_id&gt;/</code> as gzip-compressed JSON Lines, with a
SHA-256 checksum per file and the git commit plus per-module source hashes in the
run manifest. <code>python3 -m sim.cli verify</code> re-hashes the lot.</li>
</ul>
"""
    _s8 = f"""
<h2>8 · Reproducing everything on this site</h2>
<pre><code>python3 -m sim.cli run                 # 6 scenarios, writes memory/ (~11s)
python3 -m sim.cli leaderboard         # ranked table
python3 -m sim.cli report @BetaChaser_3xProxy --full
python3 -m sim.cli compare             # robustness panel across scenarios
python3 -m sim.cli query fills --symbol NVDA --limit 20
python3 -m sim.cli verify              # checksums + data audits
python3 -m sim.cli sources             # the verified-source register
python3 scripts/build_site.py          # regenerate this site into docs/
python3 -m unittest discover -s tests -v</code></pre>

"""
    body = "\n\n".join([_s0, _s1, _s2, _s3, _s4, _s5, _s6, _s7, _s8])
    return page("Methodology", body, "methodology.html")


def _interval_marks() -> List[int]:
    return list(range(13))


def cfg_overnight_share() -> str:
    from sim import marketdata
    return f"{marketdata.OVERNIGHT_VARIANCE_SHARE:.0%}"


def cfg_wick() -> str:
    from sim import marketdata
    return f"{marketdata.WICK_COEFF:.2f}"


def build_data(d: SiteData) -> str:
    md = d.market_data
    prov = (md.get("provenance") or {})
    rows = []
    for sym, inst in sorted((md.get("instruments") or {}).items()):
        p = inst.get("provenance", {})
        rows.append([
            f"<strong>{ESC(sym)}</strong>", ESC(inst.get("name", "")),
            ESC(inst.get("sector", "")), ESC(inst.get("asset_type", "")),
            ESC(inst.get("liquidity_tier", "")),
            num(inst.get("beta"), F2),
            num(100.0 * (inst.get("sigma_idio_annual") or 0), F1) + "%",
            num(100.0 * (inst.get("alpha_annual") or 0), F1S) + "%",
            num(inst.get("adv_shares"), F0),
            _prov_cell(p),
        ])
    audit = prov.get("monthly_range_audit_spy", {}) or {}
    arows = [[ESC(k), num(v["real_pct"], F2) + "%",
              num(v["simulated_pct"], F2) + "%",
              signed(v["diff_pct_points"])]
             for k, v in sorted((audit.get("months") or {}).items())]
    body = f"""
<h1>Data provenance</h1>
<p class="lede">Three categories of number appear in this project: <strong>real
published observations</strong>, <strong>simulations of them</strong>, and
<strong>declared scenario parameters</strong>. This page says which is which, per
field, and links to the file where each real observation is stored.</p>

<h2>Real data held in this repository</h2>
{table(["File", "Series", "Window", "Rows", "Publisher / URL"], [
    ['<code>data/real/fred/SP500_2025-09-17_2026-09-16.csv</code>',
     "S&amp;P 500 daily close", "2025-09-17 → 2026-09-16",
     str(_count_csv("data/real/fred/SP500_2025-09-17_2026-09-16.csv")),
     '<a href="https://fred.stlouisfed.org/series/SP500" rel="noopener" target="_blank">FRED (Federal Reserve Bank of St. Louis)</a>'],
    ['<code>data/real/fred/VIXCLS_2025-09-17_2026-09-16.csv</code>',
     "CBOE VIX daily close", "2025-09-17 → 2026-09-16",
     str(_count_csv("data/real/fred/VIXCLS_2025-09-17_2026-09-16.csv")),
     '<a href="https://fred.stlouisfed.org/series/VIXCLS" rel="noopener" target="_blank">FRED / CBOE</a>'],
    ['<code>data/real/yahoo/SPY_monthly_1y.json</code>',
     "SPY monthly OHLCV (13 bars) + meta", "2025-09 → 2026-09", "13",
     '<a href="https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1mo&amp;range=1y" rel="noopener" target="_blank">Yahoo Finance chart API v8</a>'],
    ['<code>data/real/yahoo/AAPL_snapshot_2026-09-17.json</code>',
     "AAPL meta, 5 daily bars, 4 dividend ex-dates", "as fetched 2026-09-17", "5 + 4",
     '<a href="https://query1.finance.yahoo.com/v8/finance/chart/AAPL" rel="noopener" target="_blank">Yahoo Finance chart API v8</a>'],
])}
<p class="muted small">Both FRED files were downloaded as
<code>fredgraph.csv</code> exports and stored verbatim. Gaps in the SP500 series
are market closures, not missing data: the ten blank dates were cross-checked
against the
<a href="https://www.nasdaq.com/market-activity/stock-market-holiday-schedule" rel="noopener" target="_blank">Nasdaq
holiday schedule</a> and all ten match a published closure.</p>

<h2>Universe, field by field</h2>
<div class="table-scroll">
{table(["Symbol", "Name", "Sector", "Type", "Liquidity tier", "Beta",
        "Idiosyncratic vol (ann.)", "Declared alpha (ann.)", "ADV (shares)",
        "Provenance by field"], rows)}
</div>
<p class="muted small"><strong>Declared alpha</strong> is the excess log return
over beta × market that this scenario assigns to the name. It is a design input,
not a forecast and not a vendor statistic. Beta and idiosyncratic volatility are
likewise declared, chosen to span a realistic cross-section. Only prices marked
<span class="badge badge-ok">real</span> came from an external observation.</p>

<h2>Calibration audit: simulated vs real SPY monthly range</h2>
<p>The one place a simulated quantity can be checked directly against real data
is the monthly high-low range of SPY, because 13 real monthly bars were
captured. The intraday-range parameters were tuned on this audit.</p>
{table(["Month", "Real (high−low)/close", "Simulated", "Difference"], arows)}
<p>Mean absolute difference <strong>{num(audit.get("mean_abs_diff_pp"), F3)}
percentage points</strong> over {num(audit.get("n_months"), F0P)} months
(worst {num(audit.get("max_abs_diff_pp"), F3)} pp). The tension behind
that number - a grid search preferred a smaller overnight variance share than
the published evidence supports - is documented as
<a href="irregularities.html">IR-06</a>.</p>

<h2>What the replay generator produced</h2>
{table(["Diagnostic", "Value"], [
    ["Sessions (competition + warm-up)", f"{len(md.get('dates', []))} "
     f"({prov.get('warmup_sessions_simulated', 0)} warm-up)"],
    ["Real S&amp;P 500 return over the window", signed(prov.get("spx_total_return_pct_real"))],
    ["Real S&amp;P 500 annualised log volatility", num(prov.get("spx_annualised_vol_real"), F4)],
    ["Real VIX mean / max", f"{num(prov.get('vix_mean_real'), F2)} / {num(prov.get('vix_max_real'), F2)}"],
    ["Fitted SPY/index ratio", num(prov.get("spy_index_ratio_fitted"), F5)],
    ["Max absolute error of that ratio vs real monthly closes",
     num(prov.get("spy_ratio_max_abs_pct_error_vs_real_monthly_closes"), F4) + "%"],
    ["VIX observations dropped (fell on a closure)",
     date_list(prov.get("vix_observations_dropped_on_closed_dates"), " (IR-01)")],
    ["Market closures detected from series gaps",
     date_list(prov.get("full_closures_detected"))],
    ["Early closes modelled", date_list(prov.get("early_closes"))],
    ["Seed / config fingerprint", f"{ESC(str(prov.get('seed')))} / <code>{ESC(str(prov.get('config_fingerprint')))}</code>"],
    ["Single-name daily OHLCV", ESC(str(prov.get("single_name_daily_ohlcv")))],
])}

<h2>Live-data adapters (the path to genuinely real-time prices)</h2>
<p>Season 1 is a replay, but the code ships working adapters so a future season
can run on live data. Each was attempted or documented as follows; none of the
key-based vendors could be reached from the build environment.</p>
{table(["Provider", "Real-time capable", "API key env var", "Executed here", "Docs", "Notes"],
       [[f'<strong>{ESC(p["name"])}</strong>',
         "yes" if p.get("realtime_capable") else "no (end-of-day)",
         f'<code>{ESC(p.get("key_env") or "-")}</code>',
         badge("succeeded", "ok") if p.get("succeeded_in_this_sandbox")
         else (badge("refused", "neg") if p.get("attempted_in_this_sandbox")
               else badge("not attempted", "muted")),
         f'<a href="{ESC(p.get("docs_url", "#"))}" rel="noopener" target="_blank">docs</a>',
         ESC(p.get("notes", ""))]
        for p in _providers()])}

<h2>Memory layout</h2>
<p>Every run writes a self-describing, checksummed bundle. Nothing is discarded
after the leaderboard is produced.</p>
<pre><code>memory/
  manifest.json                     store-level index of runs
  runs/&lt;run_id&gt;/
    manifest.json                   config, seed, git commit, per-module
                                    source hashes, SHA-256 per file
    market_data.json                the exact bars / SPX / VIX traded on
    participants.json               every strategy spec and username
    market_report.json              what the market did
    factor_report.json              realised style premia
    leaderboard.json                ranked table
    irregularities.json             IR-xx flags raised by this run
    reports/&lt;username&gt;.json         full analytics + post-mortem per participant
    events/orders.jsonl.gz          every order submitted
    events/fills.jsonl.gz           every fill, including partials
    events/rejections.jsonl.gz      every rejected/expired order with the reason
    events/equity.jsonl.gz          daily equity, exposure, leverage, return
    events/positions.jsonl.gz       daily position snapshots
    events/carry.jsonl.gz           dividends and borrow fees
    events/margin.jsonl.gz          maintenance breaches, forced liquidations
    events/quotes.jsonl.gz          opening touch quote per symbol per session
</code></pre>
<p>Primary-scenario event counts: {_event_counts(d)}.</p>
"""
    return page("Data provenance", body, "data.html")


def _event_counts(d: SiteData) -> str:
    parts = []
    for s in memory.EVENT_LOGS:
        parts.append(f"{s} {d.store.stream(d.run_id, s).count():,}")
    return " · ".join(parts)


def _count_csv(rel: str) -> int:
    path = os.path.join(REPO_ROOT, rel)
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def _providers() -> List[dict]:
    from sim import marketdata
    return marketdata.provider_catalogue()


def _prov_cell(p: Dict[str, str]) -> str:
    if not p:
        return '<span class="na">-</span>'
    out = []
    for field, status in sorted(p.items()):
        kind = "ok" if status == "real" else ("warn" if "scenario" in status
                                              else "muted")
        out.append(f'<div class="prov"><code>{ESC(field)}</code> '
                   f'{badge(status.split(" ")[0][:22], kind)}</div>')
    return "".join(out)


def build_sources(d: SiteData) -> str:
    rows = config.all_verified_sources()
    for p in _providers():
        rows.append({
            "claim": f"Live-data adapter for {p['name']}: "
                     f"{'real-time capable' if p.get('realtime_capable') else 'end-of-day only'}. "
                     f"{p.get('notes', '')}",
            "url": p.get("docs_url", ""), "publisher": p["name"],
            "status": "ADAPTER-DOCS"})
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    body_rows = []
    for i, r in enumerate(rows, 1):
        body_rows.append([
            str(i), ESC(r.get("publisher", "")), ESC(r.get("claim", "")),
            f'<a href="{ESC(r.get("url", "#"))}" rel="noopener noreferrer" '
            f'target="_blank">{ESC(r.get("url", ""))}</a>',
            status_badge(r.get("status", ""))])
    body = f"""
<h1>Source register</h1>
<p class="lede">Every external claim this project relies on, with the URL to
check it and an honest statement of how - or whether - it was verified inside the
build environment. Nothing here is marked verified that was not actually
retrieved.</p>
<section class="kpis">
{"".join(f'<div class="kpi"><div class="kpi-label">{ESC(k)}</div><div class="kpi-value">{v}</div></div>' for k, v in sorted(counts.items()))}
</section>
<h2>What each status means</h2>
{table(["Status", "Meaning"], [
    [status_badge("FETCHED-VERIFIED"), "Retrieved in this environment <em>and</em> saved verbatim under <code>data/real/</code>. Strongest category."],
    [status_badge("FETCHED"), "Retrieved in this environment; the content was used but not saved as a data file."],
    [status_badge("FETCHED-VIA-SEARCH"), "Reached only as text inside search results, because the page itself would not load here. Weaker: treat as corroborating, not primary."],
    [status_badge("SECONDARY"), "Only a secondary source could be reached. The primary document is cited but was <strong>not</strong> fetched - flagged as an irregularity where it matters (IR-05)."],
    [status_badge("KNOWN-NOT-FETCHED"), "Cited from the literature or from the regulation's canonical URL. <strong>Not retrieved in this environment.</strong> Verify manually before relying on it."],
    [status_badge("ADAPTER-DOCS"), "Vendor API documentation for a shipped live-data adapter that could not be exercised here."],
])}
<h2>The register ({len(rows)} rows)</h2>
<div class="table-scroll">
{table(["#", "Publisher", "Claim", "URL", "Status"], body_rows)}
</div>
<div class="callout warn"><strong>Environment note.</strong> The sandbox this was
built in could reach <code>pypi.org</code>, <code>files.pythonhosted.org</code>,
<code>api.github.com</code>, <code>github.com</code> and
<code>codeload.github.com</code> directly. Every other URL above was reached
through the page-fetch tool, or through search-result text where fetching failed.
Several market-data hosts refused the connection outright, which is why Season 1
is a replay of real anchor data rather than a live feed - see
<a href="limitations.html">L-01</a>.</div>
"""
    return page("Sources", body, "sources.html")


def build_irregularities(d: SiteData) -> str:
    static = _load_static_irregularities()
    rows = []
    for ir in static:
        links = "".join(f'<li><a href="{ESC(u)}" rel="noopener noreferrer" '
                        f'target="_blank">{ESC(u)}</a></li>'
                        for u in ir.get("links", []))
        rows.append([
            f'<strong>{ESC(ir["id"])}</strong>',
            badge(ir.get("severity", "?"), _sev_kind(ir.get("severity", ""))),
            ESC(ir.get("topic", "")),
            ESC(ir.get("detail", "")) + (f"<ul class='links'>{links}</ul>" if links else "")
            + (f'<p class="muted small"><strong>Resolution:</strong> {ESC(ir["resolution"])}</p>'
               if ir.get("resolution") else ""),
        ])
    runtime_rows = [[badge(ir.get("id", ""), "info"),
                     badge(ir.get("severity", ""), _sev_kind(ir.get("severity", ""))),
                     ESC(ir.get("symbol") or "-"), ESC(ir["message"])]
                    for ir in d.irregularities]
    other_runs = []
    for sc in d.scenarios:
        items = d.store.load(sc["run_id"], "irregularities.json") or []
        other_runs.append([ESC(sc["run_id"]), str(sc.get("seed")), str(len(items))])
    body = f"""
<h1>Irregularities</h1>
<p class="lede">Everything found during the build that did not behave the way a
naive reading of the sources would predict - data quirks, regulation in
transition, sources that could not be verified, and model choices where two
defensible answers disagreed. Each one is recorded rather than quietly resolved,
because a simulation that hides its own problems cannot be audited.</p>
<h2>Static findings ({len(static)})</h2>
<div class="table-scroll">
{table(["ID", "Severity", "Topic", "Finding, evidence and resolution"], rows)}
</div>
<h2>Raised while running the primary scenario ({len(d.irregularities)})</h2>
{table(["ID", "Severity", "Symbol", "Message"], runtime_rows) if runtime_rows else "<p>None.</p>"}
<h2>All scenarios</h2>
{table(["Run", "Seed", "Irregularities raised"], other_runs)}
<h2>IR code index</h2>
{table(["Code", "Raised by", "Meaning"], [
    ["IR-01 … IR-09", "research / data gathering", "Found before the simulation existed: data, regulation and source-verification issues."],
    ["IR-10", "<code>sim/engine.py</code>", "A declared dividend ex-date fell on a non-session; the dividend was paid on the next session instead of being dropped."],
    ["IR-11", "<code>sim/engine.py</code>", "The execution engine raised on an order (should never happen)."],
    ["IR-12", "<code>sim/engine.py</code>", "A strategy raised inside <code>on_day</code>; the participant skipped that session and the traceback is stored in memory."],
    ["IR-13", "<code>sim/engine.py</code>", "A participant's equity reached zero; the account was closed."],
    ["IR-14", "<code>sim/engine.py</code>", "Orders rejected by the broker-side pre-trade margin check."],
    ["IR-15", "<code>sim/engine.py</code>", "Orders resized by the broker-side pre-trade margin check."],
    ["IR-16", "<code>sim/analytics.py</code>", "Factor premia quoted in narratives are measured on a replay whose cross-sectional drifts are declared scenario parameters, so they describe this competition rather than the real market."],
])}
"""
    return page("Irregularities", body, "irregularities.html")


def _sev_kind(sev: str) -> str:
    return {"high": "neg", "medium": "warn", "low": "muted"}.get(sev, "info")


def _load_static_irregularities() -> List[dict]:
    path = os.path.join(REPO_ROOT, "research", "IRREGULARITIES.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return []


def build_limitations(d: SiteData) -> str:
    items = _load_limitations()
    cards = "".join(
        f'<article class="lim" id="{ESC(x["id"])}"><h3>{ESC(x["id"])} · '
        f'{ESC(x["title"])} {badge(x["severity"], _sev_kind(x["severity"]))}</h3>'
        f'<p>{ESC(x["detail"])}</p>'
        f'<p class="muted small"><strong>What would fix it:</strong> '
        f'{ESC(x["fix"])}</p></article>' for x in items)
    remaining = _load_remaining_work()
    rw = [[badge(x["priority"], {"P0": "neg", "P1": "warn"}.get(x["priority"], "info")),
           f'<strong>{ESC(x["title"])}</strong><br>'
           f'<span class="muted small">{ESC(x["detail"])}</span>',
           f'<code>{ESC(x["where"])}</code>',
           ESC(x["effort"])] for x in remaining]
    body = f"""
<h1>Limitations and remaining work</h1>
<p class="lede">What this project cannot do, why, and what would have to change.
These are not caveats added for form: several of them materially bound how far
any result on this site can be interpreted, and one of them (L-01) is the reason
Season 1 is a replay at all.</p>
<h2>Limitations ({len(items)})</h2>
{cards}
<h2>Remaining work, in priority order</h2>
<div class="table-scroll">
{table(["Priority", "Item", "Where it lands", "Rough effort"], rw)}
</div>
<h2>What success would require</h2>
<ol>
<li><strong>A real feed.</strong> Everything downstream of L-01 - real-time
prices, real spreads, real depth, real short interest, a real earnings calendar -
is blocked on a market-data vendor key and a host the runtime can reach. The
adapters are written and tested against their documented response shapes; what is
missing is network access and credentials.</li>
<li><strong>Intraday granularity.</strong> Three participants (gap-and-go,
overnight carry, market making) are structurally mis-measured by a daily-bar
engine, and their pages say so. Minute bars would make them meaningful.</li>
<li><strong>More seasons.</strong> One year is one draw. The robustness panel is
a partial answer; the real answer is many independent years, which needs the real
feed above or a longer historical archive.</li>
<li><strong>Human participants.</strong> This site tracks simulated strategies.
The competitions it was reverse-engineered from track people, which needs
accounts, authentication and an order-entry UI - none of which is in scope
here.</li>
</ol>
"""
    return page("Limitations", body, "limitations.html")


def _load_limitations() -> List[dict]:
    path = os.path.join(REPO_ROOT, "research", "LIMITATIONS.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return []


def _load_remaining_work() -> List[dict]:
    path = os.path.join(REPO_ROOT, "research", "REMAINING_WORK.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return []


def build_participants_index(d: SiteData) -> str:
    rows = []
    for r in d.leaderboard:
        rep = d.reports.get(r["username"], {})
        rows.append([
            f'<a href="{_slug(r["username"])}.html">{ESC(r["username"])}</a>',
            ESC(r["archetype"]), signed(r["total_return_pct"]),
            signed(r["max_drawdown_pct"]), num(r["sharpe"], F2),
            num(r["closed_trades"], F0P),
            ESC(rep.get("narrative", {}).get("verdict", ""))])
    body = f"""
<h1>Participants</h1>
<p class="lede">One page per competitor: metrics, charts, per-symbol
attribution, cost decomposition, scenario robustness, and the generated
post-mortem that answers <em>why it worked or why it did not</em>.</p>
{table(["Username", "Archetype", "Return", "Max DD", "Sharpe", "Trades", "Verdict"], rows)}
"""
    return page("Participants", body, "participants/index.html", depth=1)


# ==========================================================================
# Assets
# ==========================================================================

CSS = """
:root{
  --bg:#0b1020;--panel:#121a33;--panel2:#0f1730;--ink:#e8ecf8;--muted:#93a0c2;
  --line:#22305a;--accent:#5b8cff;--ok:#22c55e;--neg:#ef4444;--warn:#f59e0b;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em}
code{background:#0a1128;padding:1px 5px;border-radius:4px;color:#c8d6ff}
pre{background:#080e20;border:1px solid var(--line);border-radius:8px;padding:14px;overflow:auto}
.site-header{background:linear-gradient(180deg,#0d1430,#0b1020);border-bottom:1px solid var(--line);
 padding:18px 0 0;position:sticky;top:0;z-index:20;backdrop-filter:blur(6px)}
.brand{font-size:20px;font-weight:700;letter-spacing:-.2px;color:#fff}
.brand-sub{color:var(--muted);margin-left:12px;font-size:13px}
.nav{display:flex;flex-wrap:wrap;gap:4px;margin-top:12px}
.nav a{padding:8px 12px;border-radius:8px 8px 0 0;color:var(--muted);font-size:14px;font-weight:500}
.nav a:hover{background:#141d3d;color:#fff;text-decoration:none}
.nav a.active{background:var(--panel);color:#fff;box-shadow:inset 0 -2px 0 var(--accent)}
main{padding:26px 20px 60px}
h1{font-size:30px;line-height:1.25;margin:6px 0 14px;letter-spacing:-.4px}
h2{font-size:21px;margin:34px 0 10px;letter-spacing:-.2px}
h3{font-size:17px;margin:20px 0 8px}
h4{font-size:14px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin:14px 0 6px}
.lede{font-size:16.5px;color:#cfd8f2;max-width:900px}
.muted{color:var(--muted)}
.small{font-size:13px}
.na{color:var(--muted)}
.pos{color:var(--ok);font-variant-numeric:tabular-nums}
.neg{color:var(--neg);font-variant-numeric:tabular-nums}
.zero{color:var(--muted)}
.hero{padding:14px 0 6px;border-bottom:1px solid var(--line);margin-bottom:22px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:18px 0}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi-label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.07em}
.kpi-value{font-size:24px;font-weight:700;margin:2px 0;letter-spacing:-.5px}
.kpi-sub{color:var(--muted);font-size:12.5px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
 padding:18px 20px;margin:18px 0}
.card>h2{margin-top:0}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:900px){.two-col{grid-template-columns:1fr}}
table.data{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
table.data th,table.data td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
table.data thead th{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em;
 border-bottom:1px solid #2c3c6e;position:sticky;top:0;background:var(--panel)}
table.data tbody tr:hover{background:#16204400}
table.data tbody tr:nth-child(even){background:#0f1730}
table.compact th{width:44%;color:var(--muted);font-weight:600}
.table-scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
.table-scroll table.data{min-width:820px}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;
 font-weight:600;letter-spacing:.02em;border:1px solid transparent;white-space:nowrap}
.badge-ok{background:#0d2a1a;color:#4ade80;border-color:#1d4d33}
.badge-neg{background:#2d1214;color:#fca5a5;border-color:#5c2226}
.badge-warn{background:#2c2109;color:#fcd34d;border-color:#5a4415}
.badge-info{background:#101f3d;color:#93c5fd;border-color:#23406f}
.badge-muted{background:#161d33;color:#93a0c2;border-color:#253054}
.chart{width:100%;height:auto;background:var(--panel2);border:1px solid var(--line);
 border-radius:10px;padding:6px;margin:8px 0}
.chart-title{fill:#cfd8f2;font-size:13px;font-weight:600}
.grid{stroke:#1d2a4d;stroke-width:1}
.grid-v{stroke:#16203c;stroke-width:1}
.baseline{stroke:#3b4c80;stroke-width:1;stroke-dasharray:4 3}
.tick{fill:var(--muted);font-size:11px}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:4px 0 10px;font-size:13px;color:var(--muted)}
.legend-item i{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
.axis-label{color:var(--muted);font-size:12px}
.spark{width:120px;height:28px;vertical-align:middle}
.strategy{background:var(--panel);border:1px solid var(--line);border-radius:12px;
 padding:16px 20px;margin:16px 0}
.strategy header{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;align-items:baseline}
.strategy h3{margin:0}
.strategy-meta{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:14px}
details{margin:10px 0;border-top:1px solid var(--line);padding-top:8px}
summary{cursor:pointer;color:var(--muted);font-size:13.5px;font-weight:600}
ul.refs li{margin-bottom:8px}
ul.links{margin:6px 0 0;padding-left:18px}
.callout{border-left:3px solid var(--warn);background:#1a1608;padding:12px 16px;
 border-radius:0 8px 8px 0;margin:16px 0}
.callout.warn{border-color:var(--warn);background:#1c1607}
.prose-block{border-top:1px solid var(--line);padding:10px 0}
.prose-block:first-child{border-top:0}
.prose-block h3{margin:0 0 4px;font-size:13px;text-transform:uppercase;
 letter-spacing:.07em;color:var(--accent)}
.prose-block p{margin:0}
.lim{background:var(--panel);border:1px solid var(--line);border-radius:10px;
 padding:14px 18px;margin:12px 0}
.lim h3{margin:0 0 6px;font-size:16px}
.prov{margin:1px 0;font-size:12px}
.crumbs{color:var(--muted);font-size:13px;margin-bottom:8px}
.verdict{font-size:12.5px;color:var(--muted)}
.more{margin-top:10px}
.questions li{margin-bottom:10px}
.site-footer{border-top:1px solid var(--line);background:#080e20;padding:22px 0 40px;
 color:var(--muted);font-size:13.5px;margin-top:30px}
"""

JS = """
// Minimal progressive enhancement: sortable tables and section collapse.
(function () {
  document.querySelectorAll('table.data').forEach(function (tbl) {
    var heads = tbl.querySelectorAll('thead th');
    if (heads.length < 2) return;
    heads.forEach(function (th, idx) {
      th.style.cursor = 'pointer';
      th.title = 'Click to sort';
      th.addEventListener('click', function () {
        var body = tbl.tBodies[0];
        if (!body) return;
        var rows = Array.prototype.slice.call(body.rows);
        var asc = th.dataset.asc !== 'true';
        th.dataset.asc = asc;
        rows.sort(function (a, b) {
          var x = (a.cells[idx] ? a.cells[idx].textContent : '').trim();
          var y = (b.cells[idx] ? b.cells[idx].textContent : '').trim();
          var nx = parseFloat(x.replace(/[^0-9.\\-]/g, ''));
          var ny = parseFloat(y.replace(/[^0-9.\\-]/g, ''));
          var both = !isNaN(nx) && !isNaN(ny);
          var r = both ? nx - ny : x.localeCompare(y);
          return asc ? r : -r;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      });
    });
  });
})();
"""


def _slug(username: str) -> str:
    return username.lstrip("@").replace("/", "_").replace(".", "_")


# ==========================================================================
# Main
# ==========================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--memory-root", default=os.path.join(REPO_ROOT, "memory"))
    ap.add_argument("--out", default=os.path.join(REPO_ROOT, "docs"))
    ap.add_argument("--run", default="")
    args = ap.parse_args(argv)

    d = SiteData(args.memory_root, args.run)
    out = args.out
    os.makedirs(os.path.join(out, "assets"), exist_ok=True)
    os.makedirs(os.path.join(out, "participants"), exist_ok=True)
    written: List[str] = []

    def write(rel: str, content: str) -> None:
        path = os.path.join(out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(rel)

    write("index.html", build_index(d))
    write("leaderboard.html", build_leaderboard(d))
    write("strategies.html", build_strategies(d))
    write("market.html", build_market(d))
    write("methodology.html", build_methodology(d))
    write("data.html", build_data(d))
    write("sources.html", build_sources(d))
    write("irregularities.html", build_irregularities(d))
    write("limitations.html", build_limitations(d))
    write("participants/index.html", build_participants_index(d))
    # Only usernames on *this* season's leaderboard get a page. The memory store
    # holds every season's reports, and writing one page per report would publish
    # Season 2's participants inside Season 1's site (observed and fixed).
    board = {row["username"] for row in d.leaderboard}
    for username in [u for u in d.reports if u in board]:
        write(f"participants/{_slug(username)}.html", build_participant(d, username))
    write("assets/site.css", CSS)
    write("assets/site.js", JS)
    write(".nojekyll", "")

    # Prune stale participant pages. A page set is only trustworthy if it
    # *contains* exactly the pages of the run it was built from: a renamed or
    # re-scoped participant would otherwise leave its old page published under a
    # URL that no longer appears in any index (observed once, with Season 2
    # pages left behind in Season 1's directory).
    keep = {"index.html"} | {f"{_slug(u)}.html" for u in board}
    people_dir = os.path.join(out, "participants")
    if os.path.isdir(people_dir):
        for name in sorted(os.listdir(people_dir)):
            if name.endswith(".html") and name not in keep:
                os.remove(os.path.join(people_dir, name))
                print(f"  pruned stale participant page {name}")

    # Machine-readable copies of the headline data, for anyone who wants to
    # re-analyse without parsing HTML.
    write("assets/data/leaderboard.json", json.dumps(d.leaderboard, indent=1))
    write("assets/data/market.json", json.dumps(d.market, indent=1))
    write("assets/data/factors.json", json.dumps(d.factors, indent=1))
    write("assets/data/robustness.json", json.dumps(d.panel, indent=1))
    write("assets/data/participants.json", json.dumps(d.participants, indent=1))
    write("assets/data/manifest.json", json.dumps({
        k: v for k, v in d.manifest.items() if k != "files"}, indent=1))

    print(f"site built: {len(written)} files in {out}")
    print(f"  primary run: {d.run_id}")
    print(f"  participants: {len(d.reports)} · scenarios: {len(d.scenarios)}")
    total = sum(os.path.getsize(os.path.join(out, w)) for w in written)
    print(f"  total size: {total / 1024:.0f} KB")
    print(f"  open {os.path.join(out, 'index.html')} or publish {out} with GitHub Pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
