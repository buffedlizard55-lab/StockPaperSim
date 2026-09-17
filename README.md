# StockPaperSim

A one-year paper-trading stock competition between **20 return-seeking strategy
personas**, run on a replay of the **real S&P 500 and VIX path**, with a real
venue model (depth, spreads, market making, dated tick size, dated fees), a full
audit trail, and a published GitHub Pages site.

**Live site:** <https://buffedlizard55-lab.github.io/StockPaperSim/>

> **Read this first.** Season 1 is a **replay of real anchor data through a
> calibrated simulated venue**, not a live feed. Two of the seventeen price
> series are real observations (SPY via Yahoo Finance, and the index factor and
> VIX regime via FRED); the other fifteen are explicitly labelled simulations
> built from those real factors plus declared parameters. No bar was invented
> silently, no result here is investment advice, and nothing on the site should
> be read as evidence about a strategy's real future performance. The site says
> this on every page, and [`research/IRREGULARITIES.json`](research/IRREGULARITIES.json)
> carries the 29 flags this project raised against itself.

---

## Season 1 result

Window **2025-09-17 → 2026-09-16** (251 sessions), starting capital **$100,000**
each, ranked on total return. Benchmark: the real S&P 500 returned **+14.41%**
over the same window (FRED `SP500`); **13 of 20 participants beat it**.

| # | Participant | Return | Max DD | Sharpe | Beta | Trades | Cost | Verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | `@BetaChaser_3xProxy` | **+108.4%** | −43.5% | 1.40 | 3.92 | 66 | 0.24% | beat the market |
| 2 | `@SectorRotator_AlphaX` | +100.9% | −32.2% | 1.91 | 2.44 | 25 | 0.05% | beat the market |
| 3 | `@MeanRev_Z2Sigma` | +74.8% | −7.0% | 3.50 | 0.51 | 53 | 0.24% | beat the market |
| 4 | `@MomentumMax_12x1` | +51.2% | −45.0% | 1.07 | 3.25 | 9 | 0.03% | beat the market |
| 5 | `@OneBigBet_Concentra` | +48.9% | −22.1% | 1.22 | 0.55 | 55 | 1.14% | beat the market |
| 6 | `@BuyHold_MaxBeta` | +45.3% | −32.0% | 0.89 | 3.17 | 3 | 0.11% | beat the market |
| 7 | `@OverreactionFade_LT` | +39.9% | −14.0% | 1.58 | 0.48 | 71 | 1.18% | beat the market |
| 8 | `@TrendSurfer_GoldenX` | +30.1% | −26.9% | 0.97 | 2.23 | 55 | 0.04% | beat the market |
| 9 | `@VIXRegime_Timer` | +28.0% | −14.7% | 1.44 | 1.34 | 35 | 0.06% | beat the market |
| 10 | `@DriftRider_PEAD` | +21.0% | −14.8% | 0.92 | 0.23 | 40 | 0.35% | beat the market |
| 11 | `@DonchianBreakout_20` | +19.0% | −19.3% | 0.69 | 1.57 | 8 | 0.05% | roughly matched the market |
| 12 | `@Contrarian_DeepValue` | +18.9% | −51.2% | 0.62 | 2.78 | 3 | 0.02% | roughly matched the market |
| 13 | `@IlliquidRocket_Degen` | +15.8% | −45.0% | 0.59 | 1.02 | 13 | 1.87% | roughly matched the market |
| 14 | `@OvernightCarry_NO` | +14.1% | −10.3% | 0.74 | 1.05 | 244 | 1.29% | roughly matched the market |
| 15 | `@KitchenSink_AllIn` | +5.2% | −30.5% | 0.31 | 1.56 | 354 | 0.39% | made money but lagged the index |
| 16 | `@PairsArb_ZScore2` | +1.0% | −0.4% | 1.01 | −0.01 | 3 | 0.01% | made money but lagged the index |
| 17 | `@SqueezeHunter_TF` | −1.8% | −10.8% | −0.09 | 0.13 | 30 | 0.48% | lost money |
| 18 | `@VolCarry_LowHigh` | −30.0% | −48.2% | −0.50 | −1.31 | 36 | 0.06% | lost money |
| 19 | `@SpreadHarvester_MM` | −39.1% | −57.4% | −0.50 | −3.07 | 528 | −0.08% | lost money |
| 20 | `@GapAndGo_YOLO` | **−86.6%** | −86.6% | −6.75 | 0.65 | 180 | 1.15% | lost money |

**What actually caused these returns** (each participant page carries the full
post-mortem, generated from that participant's own numbers):

* The winner did not pick stocks well. `@BetaChaser_3xProxy` ran **beta 3.92**
  into a market that rose 14.4%, and the report splits it: **+56.5pp from beta**
  and **+51.9pp residual** (annualised alpha +44.1%, R² 0.50). It also took a
  **−43.5% drawdown** to get there. `@BuyHold_MaxBeta` is the control
  experiment: same idea, three trades, +45.3%.
* `@GapAndGo_YOLO` lost 86.6% not because the gap signal was wrong but because
  it chased gaps with full size into a **1.15%-of-capital execution cost** and
  180 round trips; the venue model charges spread, impact and fees, and that is
  where the account went.
* `@SpreadHarvester_MM` is the market maker. It **earned the rebate** (negative
  cost, −0.08%) and still lost 39.1%, because quoting both sides of a trending,
  fat-tailed tape accumulates inventory it cannot unwind at the mid - adverse
  selection, modelled after Glosten-Milgrom. It is the most instructive failure
  in the season.
* `@MeanRev_Z2Sigma` is the best risk-adjusted result in the season (**Sharpe
  3.50, max DD −7.0%**) and third on return: mean reversion in a range-bound
  year with a cheap venue (0.24% of capital).
* `@PairsArb_ZScore2` is nearly market-neutral (beta −0.01) and made +1.0%: the
  spread legs offset, and the season had no dislocation big enough to pay.

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
                    52-row verified-source register
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
scripts/        build_site.py (the GitHub Pages generator), check_purity.py
tests/          327 tests - engine, venue, memory, site, registers, published docs
data/real/      verbatim FRED and Yahoo downloads, with checksums
memory/         the audit trail: one directory per run, gzipped event streams,
                per-file SHA-256 manifest, per-participant reports
docs/           the published site (GitHub Pages serves this directory)
research/       VERIFICATION_LOG.md, COMPETITION_SITES.md,
                IRREGULARITIES.json (27), LIMITATIONS.json (16),
                REMAINING_WORK.json (15)
```

## Run it

```bash
python3 -m sim.cli run                  # all six scenarios (~25 s), writes memory/
python3 -m sim.cli leaderboard          # ranked table with the benchmark
python3 -m sim.cli report --user @GapAndGo_YOLO     # full post-mortem
python3 -m sim.cli verify               # checksum every run + data audits
python3 -m sim.cli build-site           # regenerate docs/
python3 -m unittest discover -s tests   # 327 tests
```

Reproducibility is enforced, not claimed: the same seed and config reproduce the
committed leaderboard **bit for bit**, every memory file is SHA-256 checksummed
in its run manifest, and a deliberately corrupted stream is detected
(`tests/test_memory.py`). CI re-runs the primary season and diffs the rebuilt
site against `docs/` byte for byte.

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
* [`docs/sources.html`](https://buffedlizard55-lab.github.io/StockPaperSim/sources.html)
  — the 58-row register, every URL clickable, every row carrying an honesty
  status (`FETCHED-VERIFIED`, `FETCHED`, `FETCHED-VIA-SEARCH`, `SECONDARY`,
  `KNOWN-NOT-FETCHED`, `ADAPTER-DOCS`). Nothing is marked verified that was not
  actually retrieved here.
* [`research/COMPETITION_SITES.md`](research/COMPETITION_SITES.md) — the
  reverse-engineering of [TradingView The Leap](https://www.tradingview.com/the-leap/december-2025/rules/),
  [Trade Ideas PM Challenge](https://www.trade-ideas.com/stock-trading-competition/)
  and [CandleCharts Showdown](https://specials.candlecharts.com/contest/): which
  rule was copied, which was widened as a declared SIM CHOICE, and which is
  honestly marked *not applicable*.
* [`docs/irregularities.html`](https://buffedlizard55-lab.github.io/StockPaperSim/irregularities.html)
  — all 29 flags, including the ones raised against this project's own modelling
  choices.
* [`docs/limitations.html`](https://buffedlizard55-lab.github.io/StockPaperSim/limitations.html)
  — 16 limitations, 16 items of remaining work in priority order, and what
  success would require.

## Known limits (the short version)

1. **No real intraday market data.** The consolidated tape is not reachable from
   this environment; Yahoo's quote endpoint needs a crumb (401), Stooq refused
   the connection, and no bulk daily OHLCV for all 17 names was retrievable. So
   the venue is calibrated, not observed. Six live-data adapters
   (Alpaca, Polygon, Finnhub, Tiingo, Yahoo, Stooq) ship in `sim/marketdata.py`
   and are
   the path to a genuinely real-time season — they could not be exercised here.
2. **One season, six scenarios, one year.** That is not a statistically
   significant sample of anything, and the site says so.
3. **Declared priors are not measured premia.** Factor returns, borrow rates,
   earnings surprises and 12 of 17 dividend schedules are declared parameters,
   not observations (IR-07 to IR-09, IR-16).
4. **Impact exponent.** The square-root law is the vendor convention; the largest
   empirical study of real orders found a 3/5 power law instead, which would
   charge ~26% less impact at 10% of ADV. Documented as IR-26, not silently
   absorbed.
