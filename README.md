# StockPaperSim

A one-year paper-trading stock competition between **return-seeking strategy
personas**, with a real venue model (depth, spreads, market making, dated tick
size, dated fees), a full audit trail, and a published GitHub Pages site.

* **Season 1** - 20 personas on a replay of the **real S&P 500 and VIX path**.
* **Season 2** - 14 personas on **real collected prices** for 26 instruments
  (daily bars, dividends and splits), trading signals built from the official
  sources the brief named: SEC Form 4 filings, openFDA decisions, MLB StatsAPI,
  NOAA/NCEI station records, FRED yields and gold, and the Kalshi venue API.

**Live site:** <https://buffedlizard55-lab.github.io/StockPaperSim/> →
[`docs/index.html`](docs/index.html)

Pages for this repository is configured to publish the **root** of `main`, not
`/docs`, and changing that needs admin rights (the API returns 403 for this
project's automation). So the root carries an `index.html` that redirects to
`docs/index.html`, plus a root `.nojekyll`. Nothing is duplicated: every page,
stylesheet and JSON file is served from under `docs/`, and all internal links are
relative, so the site works under either base path. Deep links in this README
include `/docs/` for that reason; an admin can drop it by setting
**Settings → Pages → source: `main`, `/docs`** and deleting the two root files.

> **Read this first.** Season 1 is a **replay of real anchor data through a
> calibrated simulated venue**, not a live feed. Two of the seventeen price
> series are real observations (SPY via Yahoo Finance, and the index factor and
> VIX regime via FRED); the other fifteen are explicitly labelled simulations
> built from those real factors plus declared parameters. No bar was invented
> silently, no result here is investment advice, and nothing on the site should
> be read as evidence about a strategy's real future performance. The site says
> this on every page, and [`research/IRREGULARITIES.json`](research/IRREGULARITIES.json)
> carries the 46 flags this project raised against itself.
>
> **Season 2 is different, and better.** It trades only the bars that were
> collected from real publishers, every fill is matched to the daily bar it came
> from (the page prints the file and its SHA-256 next to the slippage), and an
> independent audit re-derives the whole season from those files. What it is
> *not* is a live feed: prices are end-of-day, so the venue is a daily-bar
> simulation with modelled intraday path and costs. Read
> [`docs/season2/index.html`](https://buffedlizard55-lab.github.io/StockPaperSim/docs/season2/index.html)
> for the custody chain and the two stress panels.

---

## Season 1 result

Window **2025-09-17 → 2026-09-16** (251 sessions), starting capital **$100,000**
each, ranked on total return. Benchmark: the real S&P 500 returned **+14.41%**
over the same window (FRED `SP500`); **13 of 20 participants beat it**.

| # | Participant | Return | Max DD | Sharpe | Beta | Trades | Cost | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | `@BetaChaser_3xProxy` | **+108.4%** | −43.5% | 1.40 | 3.92 | 66 | 0.24% | beat the market |
| 2 | `@SectorRotator_AlphaX` | +100.9% | −32.2% | 1.91 | 2.44 | 25 | 0.05% | beat the market |
| 3 | `@MeanRev_Z2Sigma` | +74.3% | −7.0% | 3.36 | 0.54 | 53 | 0.26% | beat the market |
| 4 | `@MomentumMax_12x1` | +51.2% | −45.0% | 1.07 | 3.25 | 9 | 0.03% | beat the market |
| 5 | `@BuyHold_MaxBeta` | +45.3% | −32.0% | 0.89 | 3.17 | 3 | 0.11% | beat the market |
| 6 | `@OverreactionFade_LT` | +42.1% | −12.6% | 1.65 | 0.47 | 71 | 1.19% | beat the market |
| 7 | `@OneBigBet_Concentra` | +40.6% | −22.1% | 1.37 | 0.41 | 50 | 0.62% | beat the market |
| 8 | `@TrendSurfer_GoldenX` | +30.1% | −26.9% | 0.97 | 2.23 | 55 | 0.04% | beat the market |
| 9 | `@VIXRegime_Timer` | +28.4% | −14.7% | 1.46 | 1.34 | 35 | 0.06% | beat the market |
| 10 | `@DriftRider_PEAD` | +20.7% | −14.8% | 0.91 | 0.23 | 40 | 0.35% | beat the market |
| 11 | `@Contrarian_DeepValue` | +18.9% | −51.2% | 0.62 | 2.78 | 3 | 0.02% | roughly matched the market |
| 12 | `@DonchianBreakout_20` | +18.2% | −19.4% | 0.67 | 1.57 | 8 | 0.05% | roughly matched the market |
| 13 | `@IlliquidRocket_Degen` | +15.8% | −45.0% | 0.59 | 1.02 | 13 | 1.87% | roughly matched the market |
| 14 | `@OvernightCarry_NO` | +9.0% | −4.9% | 1.25 | 0.04 | 247 | 1.16% | made money but lagged the index |
| 15 | `@KitchenSink_AllIn` | +9.0% | −31.1% | 0.41 | 1.55 | 367 | 0.38% | made money but lagged the index |
| 16 | `@PairsArb_ZScore2` | +1.0% | −0.4% | 1.01 | −0.01 | 3 | 0.01% | made money but lagged the index |
| 17 | `@SqueezeHunter_TF` | −1.8% | −10.8% | −0.09 | 0.13 | 30 | 0.48% | lost money |
| 18 | `@VolCarry_LowHigh` | −30.0% | −48.2% | −0.50 | −1.31 | 36 | 0.06% | lost money |
| 19 | `@SpreadHarvester_MM` | −34.4% | −53.6% | −0.52 | −1.93 | 537 | −0.07% | lost money |
| 20 | `@GapAndGo_YOLO` | **−91.8%** | −91.9% | −8.09 | 0.82 | 203 | 1.23% | lost money |

> **These numbers were re-derived, not patched.** Season 1 has been republished
> once, after two accounting errors were found in a line-by-line reading of the
> engine rather than by a failing test: dividend entitlement was settled on the
> position left *after* the ex-date's trades, and short positions were never
> charged the manufactured dividend a stock loan requires (IR-31). Two
> strategies also turned out to be unable to write the exit rule they documented,
> because the venue could only execute at the opening bell (IR-34), and the
> pattern-day-trader counter was a stub that returned `True` for any second fill
> (IR-32). Fixing the accounting changed P&L, so the honest response was to
> re-run the season and republish, not to edit the table in prose. The direct
> cash correction was small - dividends received went from $11,299.61 to
> $11,600.83 while $1,852.31 of manufactured dividends began to be charged, a net
> $1,551.09 across 20 accounts, about $78 each - and the individual effects were
> not: `@OverreactionFade_LT` moved 31.3pp because a $2.12 difference on one
> ex-date changed the size of an order a month later and every later trade
> compounded from it. Six of twenty ranks moved. Ranks 1 to 3 held, but the
> middle of the table did not, and three participants changed rank without any
> change in their own return at all, displaced by someone else's correction. That is the same knife-edge IR-29
> measures from the other side, and it is why this README ranks nothing by skill
> and why `scripts/independent_audit.py` exists: 763 checks re-derive every
> published number from the raw event trail using code that never imports the
> engine, and CI runs it on every push.

**What actually caused these returns** (each participant page carries the full
post-mortem, generated from that participant's own numbers):

* The winner did not pick stocks well. `@BetaChaser_3xProxy` ran **beta 3.92** into a market
  that rose 14.4%, and the report splits it: **+56.5pp from beta** and
  **+51.9pp residual** (annualised alpha +44.1%, R² 0.50). It took a
  **−43.5% drawdown** to get there. `@BuyHold_MaxBeta` is the control
  experiment: same idea, three trades, +45.3%.
* `@GapAndGo_YOLO` lost 91.8% not because the gap signal was
  wrong but because it chased gaps with full size into a **1.23%
  of capital in execution cost** over 203 round trips, every one of them opened
  and closed inside a single session (FINRA's counting rule, Notice 21-13). The
  venue charges spread, impact and fees twice a day to a strategy that holds for
  hours, and that is where the account went. It also now receives **$0.00** of
  dividends, against $80.46 before IR-34, which is what a book that is genuinely
  flat by the close must receive.
* `@SpreadHarvester_MM` is the market maker. It **earned the rebate** (negative
  cost, −0.07%) and still lost 34.4%, because quoting both
  sides of a trending, fat-tailed tape accumulates inventory it cannot unwind
  at the mid - adverse selection, modelled after Glosten-Milgrom. It is the most
  instructive failure in the season.

* `@MeanRev_Z2Sigma` is the best risk-adjusted result in the season (**Sharpe
  3.36, max DD −7.0%**) and third on return: mean reversion in
  a range-bound year with a cheap venue (0.26% of capital).


Because one calendar path cannot separate skill from luck, every participant is
replayed across **six scenarios** (the real calendar path plus five synthetic
seeds) over the same real index path. The panel publishes mean / median / best /
worst / standard deviation per participant — `docs/assets/data/robustness.json`,
and the "Scenario robustness" section of each page. It reorders the table: the
winner's return ranges **+82.7% to +407.9%** across the six scenarios (mean
+184.5%, median +141.3%, σ **124pp**), which is the honest way to read a
single-season contest. It beat the index in all six, but a σ of 124pp on a
one-year horizon is the number that should decide how much weight the ranking
gets.

---

## Season 2 result: the same competition on real collected prices

Season 2 is the answer to the brief's first requirement - *trade only on real,
verified, dated prices, and log everything*. Window **2025-09-17 → 2026-09-16**
(251 sessions), $100,000 each, ranked on total return. Benchmark: the real S&P
500 returned **+14.41%** over the same window (FRED `SP500`); **5 of 14
participants beat it** and **5 never traded at all**, which the site reports as
*no trades placed* rather than as a 0.00% performance.

| # | Participant | Return | Max DD | Sharpe | Beta | Trades | Cost | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | `@FDACatalyst_Rider` | **+74.3%** | −18.3% | 1.81 | 1.16 | 46 | 0.82% | beat the market |
| 2 | `@LeapMaxLever_Momentum` | +38.1% | −22.3% | 1.06 | 2.73 | 4 | 0.02% | beat the market |
| 3 | `@GOLD_Trend_GLD` | +32.8% | −36.3% | 0.88 | 0.53 | 43 | 0.21% | beat the market |
| 4 | `@MLB_Attention_Momo` | +23.8% | −28.1% | 0.68 | 0.36 | 105 | 2.59% | beat the market |
| 5 | `@PinePilot_EMA_Cross` | +18.8% | −12.1% | 0.79 | 1.77 | 49 | 0.03% | roughly matched the market |
| 6 | `@FDA_ClusterFade` | +2.8% | −22.3% | 0.25 | −0.16 | 34 | 0.45% | made money but lagged the index |
| 7 | `@InsiderCopycat_Max` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed (no Form 4 file) |
| 8 | `@InsiderCluster_Alpha` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed (no Form 4 file) |
| 9 | `@CEO_CFO_Conviction` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed (no Form 4 file) |
| 10 | `@Kalshi_Attention_Timer` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed (venue volume empty) |
| 11 | `@InjuryFeed_Forward` | +0.0% | 0.0% | 0.00 | 0.00 | 0 | 0.00% | no trades placed (forward-only probe) |
| 12 | `@MLB_Upset_Short` | −9.6% | −11.3% | −0.78 | −0.02 | 22 | 0.59% | lost money |
| 13 | `@YieldCurve_Rotator` | −10.1% | −12.9% | −0.64 | 0.26 | 4 | 0.04% | lost money |
| 14 | `@Weather_ColdSnap_Max` | −11.2% | −40.4% | −0.07 | −0.19 | 49 | 1.36% | lost money |

**What that says, honestly.** The winner is a leveraged biotech bet and the deepest
loser is *the same signal traded the other way*
(`@FDA_ClusterFade`, which fights the approval-count trend) - one real signal, two
opposite implementations, and the difference between first and last. Nothing here
is a claim that FDA approvals predict XBI. The idle five are the other half of the
result: `@InsiderCopycat_Max`, `@InsiderCluster_Alpha` and `@CEO_CFO_Conviction`
need the SEC Form 4 collection (HTTP 403 on the anonymous User-Agent, since fixed
in the collector but not yet re-collected); `@Kalshi_Attention_Timer` reads a
venue payload whose volume fields came back empty; `@InjuryFeed_Forward` is a
declared forward-only probe with no retrievable archive. The site labels each one
*DATA-MISSING* with the URL and the reason, which is the difference between "we
tested it and it did not work" and "we could not test it".

**Verification.** 771 fills and 355 round trips, net round-trip P&L
**$149,756.96** on $20.9m of traded notional, ledger digest `2adbced6…`. The
account is re-derived from the raw fill tape by code that never imports the
engine: max |equity residual| **$0.0072** against a per-account rounding bound of
$2.925 (the tape stores six-decimal prices). Median participation is 0.00013% of a
session's volume and the largest single fill is 0.23% of it. The independent audit
re-reads every published number: **2,591 checks, 0 failures**, plus 1,108 more in
the Season 1 audit. Cost sensitivity is published as a panel, not a footnote:
doubling spreads, impact and fees costs the leader 1.2pp and halving the venue's
depth costs 0.1pp, while `@Weather_ColdSnap_Max` loses 0.8pp to the fee-doubling
alone.

**Data custody.** 26 instruments, 502 sessions (251 warm-up + 251 competition),
58 collected files under `data/real/`, every request logged with status, bytes and
SHA-256 in `data/real/collection_manifest.json`. Season 2's own registers live at
`docs/season2/masterfeed.html` (the 14 MasterSite projects the brief named, each
with source class, mapping strength and whether history was retrievable),
`docs/season2/ledger.html` (every fill with the reference bar, the file and the
participation) and `docs/season2/data.html` (the full custody chain).

**How Season 2 is built and checked.**

```bash
python3 scripts/collect_real_data.py --out data/real   # on a runner with network
python3 -m sim.cli season2 --labels primary,stress-costs2x,stress-thinliquidity
python3 -m sim.cli ledger --run season2-primary-seed20260918 --participant @FDACatalyst_Rider
python3 scripts/independent_audit_season2.py           # 2,591 checks, no project imports
python3 -m sim.cli build-site                          # Season 1 + Season 2 into docs/
```

Three assumption sets run the same real prices through the same participants:
`primary`, `stress-costs2x` (double spreads, double impact, $1/order, double the
per-share taker fee) and `stress-thinliquidity` (half the participation cap and
half the touch size). Real prices are one realisation, so the sensitivity comes
from the venue assumptions rather than from a fabricated seed panel.

---

## What the venue models

| Piece | Implementation | Source |
|---|---|---|
| Tick size | dated Rule 612 grid. **$0.01 / $0.0001** is what was actually in force all season: the 2024 amendments' $0.005 tier is codified but exempted until the first business day of **November 2027** (Release 34-105656) | [17 CFR 242.612](https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612), [34-105656](https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf) |
| Round lots | tiered Rule 600(b)(93) definition, live since 2025-11-03 | same Part 242 |
| Access fees | Rule 610(c) cap **$0.003/share** (the amended $0.001 cap is exempted) | [17 CFR 242.610](https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.610) |
| Regulatory fees | **dated schedules**: SEC §31 $0.00/M → **$20.60/M from 2026-04-04**; FINRA TAF $0.000166/sh → **$0.000195/sh from 2026-01-01** with the per-trade cap dated too ($8.30 → $9.79) | [FY2026 fee order](https://www.federalregister.gov/documents/2026-03-04/2026-04233/order-making-fiscal-year-2026-annual-adjustments-to-transaction-fee-rates) |
| Depth and fill | standing limit-order book per instrument, partial fills, rejects, queue position | `sim/microstructure.py` |
| Market making | Avellaneda-Stoikov reservation price and optimal spread, inventory penalty, maker rebate | [10.1007/s10436-008-0121-7](https://doi.org/10.1007/s10436-008-0121-7) |
| Impact | square-root temporary + permanent impact, Almgren-Chriss framework | [10.21314/JOR.2001.041](https://doi.org/10.21314/JOR.2001.041) — with the 3/5-power-law dissent recorded as [IR-26](research/IRREGULARITIES.json) |
| Intraday shape | each daily bar expanded into intervals visiting O/H/L/C with a U-shaped volume profile | Admati & Pfleiderer (1988) |
| Cost accounting | Perold implementation shortfall per order: spread, depth, impact, fees, intraday drift, missed trade | [10.2469/faj.v44.5.28](https://doi.org/10.2469/faj.v44.5.28) |
| Calendar | 10 real closures and 2 early closes detected from gaps in the FRED series, cross-checked against the Nasdaq holiday schedule | [Nasdaq](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule) |
| Shorting | Reg SHO locate requirement, borrow fee, Reg T 50% initial margin, maintenance margin, margin calls | [17 CFR 242.203](https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.203) |

---

## Layout

```
sim/            the engine - pure standard library, no third-party imports
  config.py         dated fee schedules, tick grid, margin, impact; the
                    55-row verified-source register
  calendar.py       sessions, closures, early closes
  universe.py       the 17-instrument whitelist with real/simulated labels
  marketdata.py     replay generator: real factor + real VIX regime -> bars
  microstructure.py depth book, quoting, market maker, fills, Rule 612 grid
  engine.py         the competition loop, accounts, margin, liquidation
  strategies.py     the 20 participants, each with academic_basis and thesis
  analytics.py      risk, attribution, robustness, post-mortem narratives
  memory.py         append-only checksummed event store
  cli.py            run / leaderboard / report / verify / query / export /
                    irregularities / sources / build-site
scripts/        build_site.py (the GitHub Pages generator), check_purity.py,
                independent_audit.py (re-derives every published number from
                the raw event streams; imports no project code)
tests/          394 tests - engine, venue, memory, site, registers, docs, README
data/real/      verbatim FRED and Yahoo downloads, with checksums
memory/         the audit trail: one directory per run, gzipped event streams,
                per-file SHA-256 manifest, per-participant reports
docs/           the published site (GitHub Pages serves this directory)
research/       VERIFICATION_LOG.md, COMPETITION_SITES.md,
                IRREGULARITIES.json (46), LIMITATIONS.json (22),
                REMAINING_WORK.json (25), MASTER_SITE_SIGNALS.md,
                SOCIAL_STRATEGY_SOURCES.md
```

## Run it

```bash
python3 -m sim.cli run                  # all six scenarios (~25 s), writes memory/
python3 -m sim.cli leaderboard          # ranked table with the benchmark
python3 -m sim.cli report --user @GapAndGo_YOLO     # full post-mortem
python3 -m sim.cli verify               # checksum every run + data audits
python3 -m sim.cli build-site           # regenerate docs/
python3 scripts/independent_audit.py  # re-derive the published numbers from events (763 checks)
python3 -m unittest discover -s tests   # 394 tests
```

Reproducibility is enforced, not claimed: the same seed and config reproduce the
committed leaderboard **bit for bit — in a different process, not just a
different object**, every memory file is SHA-256 checksummed in its run manifest,
and a deliberately corrupted stream is detected (`tests/test_memory.py`). CI
re-runs the primary season in a scratch memory root, diffs it against the
committed leaderboard, and diffs a rebuilt site against `docs/` byte for byte.

That last gate earned its keep immediately: it caught **IR-30**, three strategies
that iterated a *set* of held positions when building their exit list. CPython
salts string hashing per process, so the exit order — and therefore the cash
available to the entries that followed — changed with `PYTHONHASHSEED`. The same
seed, config and machine produced `@OverreactionFade_LT` at **+39.90%** in one
process and **+73.46%** in another. The in-process determinism test that had been
passing all along could not catch it by construction. Fixed by iterating the
canonical universe order, verified identical across five hash seeds, and now
guarded by a cross-process reproducibility test plus an AST sweep that fails on
any `for` loop over a set.

## Verification stance

The brief was to work line by line from official sources, provide links for
manual review, and flag irregularities rather than paper over them.

* [`research/VERIFICATION_LOG.md`](research/VERIFICATION_LOG.md) — what was
  checked, when, from where, what came back, and what was corrected. Includes the
  citation audit that found and fixed **six** wrong or dead references
  (a Perold DOI that was actually Roll 1984; an Artzner DOI that was actually
  Föllmer & Leukert 2000; a Jegadeesh-Titman DOI that was actually Engle & Ng
  1993; an Almgren 2005 author list, page range and DOI; a PEAD DOI from the
  wrong journal; an unverifiable Amihud DOI).
* [`docs/sources.html`](https://buffedlizard55-lab.github.io/StockPaperSim/docs/sources.html)
  — the 61-row register (55 source rows plus 6 provider rows), every URL
  clickable, every row carrying an honesty
  status (`FETCHED-VERIFIED`, `FETCHED`, `FETCHED-VIA-SEARCH`, `SECONDARY`,
  `KNOWN-NOT-FETCHED`, `ADAPTER-DOCS`). Nothing is marked verified that was not
  actually retrieved here.
* [`research/COMPETITION_SITES.md`](research/COMPETITION_SITES.md) — the
  reverse-engineering of [TradingView The Leap](https://www.tradingview.com/the-leap/december-2025/rules/),
  [Trade Ideas PM Challenge](https://www.trade-ideas.com/stock-trading-competition/)
  and [CandleCharts Showdown](https://specials.candlecharts.com/contest/): which
  rule was copied, which was widened as a declared SIM CHOICE, and which is
  honestly marked *not applicable*.
* [`docs/irregularities.html`](https://buffedlizard55-lab.github.io/StockPaperSim/docs/irregularities.html)
  — all 46 flags, including the ones raised against this project's own modelling
  choices.
* [`docs/limitations.html`](https://buffedlizard55-lab.github.io/StockPaperSim/docs/limitations.html)
  — 22 limitations, 25 items of remaining work in priority order, and what
  success would require.

## Known limits (the short version)

1. **No real intraday market data.** The consolidated tape is not reachable from
   this environment; Yahoo's quote endpoint needs a crumb (401), Stooq refused
   the connection, and no bulk daily OHLCV for all 17 names was retrievable. So
   the venue is calibrated, not observed. Six live-data adapters
   (Alpaca, Polygon, Finnhub, Tiingo, Yahoo, Stooq) ship in `sim/marketdata.py`
   and are the path to a genuinely real-time season — they could not be
   exercised here.
2. **One season, six scenarios, one year.** That is not a statistically
   significant sample of anything, and the site says so.
3. **Declared priors are not measured premia.** Factor returns, borrow rates,
   earnings surprises and 12 of 17 dividend schedules are declared parameters,
   not observations (IR-07 to IR-09, IR-16).
4. **Impact exponent.** The square-root law is the vendor convention; the largest
   empirical study of real orders found a 3/5 power law instead, which would
   charge ~26% less impact at 10% of ADV. Documented as IR-26, not silently
   absorbed.
