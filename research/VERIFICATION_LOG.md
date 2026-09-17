# Verification log

Every external claim in this project was checked against a source, and this file
records **what was checked, when, from where, what came back, and what was
corrected as a result**. It exists because the brief asked for line-by-line
verification from official sources with links a human can re-check, and because
"the code says so" is not a source.

Statuses use the vocabulary defined in `sim/config.py`:

| Status | Meaning |
|---|---|
| `FETCHED-VERIFIED` | retrieved in this environment **and** stored verbatim under `data/real/` |
| `FETCHED` | retrieved in this environment; content used, not stored |
| `FETCHED-VIA-SEARCH` | reached only as text inside search results (weaker - the page itself was not fetched) |
| `SECONDARY` | only a secondary source was reached; the primary was not retrievable |
| `KNOWN-NOT-FETCHED` | cited from the literature; **not** retrieved here - verify manually before relying on it |
| `ADAPTER-DOCS` | vendor documentation for a live-data adapter (not a market fact) |

Environment: all checks below were made on **2026-09-17** from a sandbox whose
direct network allowlist was `pypi.org`, `files.pythonhosted.org`,
`api.github.com`, `github.com`, `codeload.github.com`. Everything else was
reached through the page-fetch tool or through search-result text. Several hosts
refused the connection outright; those refusals are recorded rather than worked
around, and each one is reflected in `research/IRREGULARITIES.json`.

---

## 1. Real market data (stored, checksummed, used as the market factor)

| What | Source | Result | Status |
|---|---|---|---|
| S&P 500 daily closes, 2025-09-17 → 2026-09-16 | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500&cosd=2025-09-17&coed=2026-09-16` | 261 rows, 10 blank (closures). Stored verbatim at `data/real/fred/SP500_2025-09-17_2026-09-16.csv`. Window return **+14.415%**, annualised log vol **0.1290** | `FETCHED-VERIFIED` |
| CBOE VIX daily closes, same window | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS&cosd=2025-09-17&coed=2026-09-16` | 261 rows. Mean **18.157**, max **31.05 on 2026-03-27**. Stored at `data/real/fred/VIXCLS_2025-09-17_2026-09-16.csv` | `FETCHED-VERIFIED` |
| SPY monthly OHLCV, 1 year | `https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1mo&range=1y` | 13 monthly bars. Used to fit the SPY/index ratio (**10.02785**, max error **0.1451%** vs real monthly closes) and to audit the simulated intraday range. Stored at `data/real/yahoo/SPY_monthly_1y.json` | `FETCHED-VERIFIED` |
| AAPL snapshot (price anchors + dividends) | `https://query1.finance.yahoo.com/v8/finance/chart/AAPL` | Start anchor **236.70**, end anchor **332.41**, four dividend ex-dates (0.26, 0.26, 0.27, 0.27). Stored at `data/real/yahoo/AAPL_snapshot_2026-09-17.json` | `FETCHED-VERIFIED` |
| Yahoo "real-time" quote endpoint | `https://query1.finance.yahoo.com/v7/finance/quote` | **HTTP 401 Unauthorized** - needs a crumb paired with a cookie. Flagged as **IR-02** | failed, flagged |
| Stooq end-of-day CSV | `https://stooq.com/q/d/l/?s=aapl.us&i=d` | **"Access denied"** - host refused the client. Flagged in the provider catalogue and **IR-03** | failed, flagged |
| Bulk daily OHLCV for all 17 names | several hosts | Not retrievable inside the environment's fetch budget. **No bars were invented**: 15 of 17 names are explicitly labelled simulations anchored to the real index factor, the real VIX regime and declared scenario parameters. Flagged as **IR-03** and **L-01** | not obtained, flagged |

Cross-check on the FRED gaps: the ten blank SP500 dates were compared against
the published Nasdaq holiday schedule
(`https://www.nasdaq.com/market-activity/stock-market-holiday-schedule`). All ten
match a published full closure (2025-11-27, 2025-12-25, 2026-01-01, 2026-01-19,
2026-02-16, 2026-04-03, 2026-05-25, 2026-06-19, 2026-07-03, 2026-09-07), and the
two early closes (2025-11-28, 2025-12-24, 13:00 ET) are modelled. VIXCLS
nevertheless prints on seven of those dates, because the VIX is computed from
SPX **option** prices and CBOE conventions do not line up one-for-one with the
cash-index calendar; those seven observations are dropped and counted. Flagged as
**IR-01**.

---

## 2. Regulation: the rules the venue model has to obey

### Rule 612 - minimum pricing increment (tick size)

The current codified text was fetched from the eCFR on 2026-09-17
(title 17 "up to date as of 9/15/2026"):
`https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.612`

What 17 CFR 242.612 actually says:

* `(b)(2)(i)` **$0.01** where the Time Weighted Average Quoted Spread (TWAQS)
  for the Evaluation Period was **greater than $0.015**;
* `(b)(2)(ii)` **$0.005** where the TWAQS was **equal to or less than $0.015**;
* `(b)(3)` **$0.0001** for quotations and orders priced **below $1.00**;
* `(b)(1)` increments become operative on the **first business day of May**
  (January-March evaluation) and the **first business day of November**
  (July-September evaluation);
* source note: **89 FR 81774, Oct. 8, 2024**.

So the amended half-penny tier **is** in the CFR. It is nevertheless **not in
force**, and that was verified rather than assumed:

| Date | Action | Source |
|---|---|---|
| 2024-09-18 | Amendments adopted (Release 34-101070); compliance date for Rules 612/610 and the round-lot definition set at the first business day of November 2025 | `https://www.sec.gov/newsroom/press-releases/2024-137`, `https://www.sec.gov/files/34-101070-fact-sheet.pdf` |
| 2024-10-08 | Adopting release published, 89 FR 81774 | `https://www.federalregister.gov/documents/2024-10-08/2024-21867/regulation-nms-minimum-pricing-increments-access-fees-and-transparency-of-better-priced-orders`, `https://www.govinfo.gov/content/pkg/FR-2024-10-08/pdf/2024-21867.pdf` |
| 2024-12-12 | **Partial stay** of the amendments to Rules 600(b)(89)(i)(F), 610 and 612 pending judicial review (Release 34-101899) | recorded in the recitals of Release 34-105656 |
| 2025-10 | D.C. Circuit **denies** the petitions for review | recorded in the recitals of Release 34-105656 |
| 2025-10-31 | **Temporary exemptive relief** from the amended compliance dates until the first business day of **November 2026** | recorded in Release 34-105656 and in `https://www.sec.gov/files/rules/exorders/2026/34-105058.pdf` |
| 2026-06-11 | **Release 34-105656** extends that relief for Rules 600(b)(89)(i)(F), 610(c) and 612 until the first business day of **November 2027** | `https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf`, published 2026-06-15 as `https://www.federalregister.gov/documents/2026-06-15/2026-11997/order-granting-temporary-exemptive-relief-pursuant-to-section-36a1-of-the-securities-exchange-act-of`, plus `https://www.sec.gov/newsroom/speeches-statements/atkins-statement-minimum-pricing-increments-access-fee-caps-061126` |

**Consequence for this project.** The entire Season 1 window (2025-09-17 →
2026-09-16) sits inside the exemption period, so the operative grid is
**$0.01 / $0.0001** and the operative Rule 610(c) access-fee cap is
**$0.003 per share**. That is what `config.minimum_tick()` and
`config.ACCESS_FEE_CAP_PER_SHARE` implement. **IR-04** was rewritten from
"genuinely uncertain" to "resolved from primary sources" and downgraded from
medium to low as a result.

Two things the relief did **not** cover, and which are therefore modelled as
live: the **tiered round-lot definition** (Rule 600(b)(93): 100 shares ≤ $250,
40 to $1,000, 10 to $10,000, 1 above) went live on **2025-11-03**, and odd-lot
dissemination is scheduled for **2026-05-01**.

Also flagged: on 2026-06-11 the Commission **proposed rescinding Rule 611** (the
order protection / trade-through rule). Season 1 is unaffected - Rule 611 was in
force for every session - but a future season would need the venue model
re-derived. Recorded as **IR-25**.

### Fees

| Fee | Value used | Source | Status |
|---|---|---|---|
| SEC Section 31 (sells) | **$0.00 per $1,000,000** from 2025-09-01; **$20.60 per $1,000,000** from 2026-04-04 | FY2026 annual adjustment order, `https://www.federalregister.gov/documents/2026-03-04/2026-04233/order-making-fiscal-year-2026-annual-adjustments-to-transaction-fee-rates`; corroborated by Nasdaq `https://www.nasdaqtrader.com/MicroNews.aspx?id=OTA2026-14` | `FETCHED` |
| FINRA Trading Activity Fee (sells) | **$0.000166/share** to 2025-12-31, **$0.000195/share** from 2026-01-01; per-trade cap **$8.30** then **$9.79** | Broker fee schedules only (`https://help.revolut.com/help/wealth/order-execution-fees-and-limits/trading-regulatory-fees/`). FINRA's own Schedule A to the By-Laws was **not retrievable** from this environment | `SECONDARY` - flagged as **IR-05** |

Both are **dated schedules**, not constants (`config.SEC31_PER_MILLION`,
`config.FINRA_TAF_PER_SHARE`, `config.FINRA_TAF_MAX_PER_TRADE`), because a
one-year competition straddles two rate changes. `config.fee_coverage_start()`
returns the earliest date any schedule is documented for (**2025-09-01**) and
`config.validate_fee_coverage()` refuses to run a season that opens before it,
so no trade can ever be costed at a rate this project cannot cite.

The per-trade **cap** was a scalar until this pass; it changed with the rate on
2026-01-01, so applying the 2026 cap to a 2025 sale would have overstated the
fee on very large orders. It is a schedule now. (For Season 1 the cap never
binds: the largest pre-2026 sell was 13,472 shares, and the cap starts to bind
above 50,000 shares.)

### Other rules relied on

* Rule 610 access-fee cap and Rule 611 trade-through protection:
  `https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.610`,
  `.../section-242.611` (Reg NMS is **17 CFR Part 242** - two citations in this
  repository previously said Part 240 and were corrected).
* Regulation SHO Rule 203(b) locate requirement for short sales:
  `https://www.ecfr.gov/current/title-17/chapter-II/part-242/section-242.203`,
  `https://www.sec.gov/regulation-sho`.
* Pattern day trader / $25,000 minimum equity: 17 CFR 240.3b-1 (**Part 240** is
  correct for this one) and
  `https://www.finra.org/investors/learn-to-invest/types-investments/margin-investing/pattern-day-trader`.
* Reg T initial margin of 50%: `https://www.federalreserve.gov/supervisionreg/regtcg.htm`.
* Rule 605 execution-quality reporting: `.../part-242/section-242.605`.
* GICS sector labels: `https://www.spglobal.com/spdji/en/index-family/equity/gics/`.

---

## 3. The three paper-trading competitions that were reverse-engineered

Full notes, including the leaderboard schema copied from each and what was
deliberately **not** copied, are in `research/COMPETITION_SITES.md`.

| Site | Fetched | What was taken from it |
|---|---|---|
| TradingView **The Leap** (December 2025 rules) | yes | $100,000 virtual starting capital; ranking on realised P&L; **open positions auto-closed at period end**; leaderboard refreshed at most hourly; minimum 3 active days; ban above 60 orders/minute; fixed instrument whitelist with a maximum number of positions; 1:1 leverage on stocks; $1 commission; accounts deleted 30 days after the end |
| **Trade Ideas** PM Challenge | yes | The leaderboard **schema**: Rank, User, Total Profit, Open Profit, Close Profit, Total Trades, Open Trades, Closed Trades, Account Value, Avg Profit/Trade - and its "**data delayed by 15 minutes**" notice, which is why this project states its own data latency instead of implying real time |
| **CandleCharts** Showdown | yes | $50,000 paper account, hosted through TradingView Community Competitions, prizes for the top three - i.e. a fixed-horizon contest on a paper account, ranked on profit |

---

## 4. Citation audit (what was wrong, and how it was fixed)

Every DOI and URL in `sim/` and `scripts/` was checked against the paper or
document it was attached to. Six defects were found and fixed; all six are
recorded as **IR-27** and are now enforced by
`tests/test_sources_register.py`.

| # | Where | Defect | Verified fact | Fix |
|---|---|---|---|---|
| 1 | `sim/microstructure.py` | Implementation shortfall attributed to "Perold 1988" with `10.2307/2328616` | `10.2307/2328616` is **Roll (1984)**, "A Simple Implicit Measure of the Effective Bid-Ask Spread in an Efficient Market", *Journal of Finance* 39(4):1127-1139. Perold (1988) is `10.2469/faj.v44.5.28` | Perold now carries the correct DOI; the note in the module docstring records the swap |
| 2 | `sim/config.py` `ImpactConfig` | Cited `10.1093/rfs/1.1.3` as an impact source | That DOI is **Admati & Pfleiderer (1988)** on intraday volume patterns - the source for the U-shaped volume profile in `LiquidityConfig` | Removed from `ImpactConfig`; the framework is now attributed to Almgren & Chriss (2001) |
| 3 | `sim/analytics.py` | CVaR/coherent risk attributed to Artzner et al. with `10.1007/s007800050008` | That DOI is **Föllmer & Leukert (2000)**, "Efficient hedging: cost versus shortfall risk", *Finance and Stochastics* 4:117-146. Artzner, Delbaen, Eber & Heath (1999) is `10.1111/1467-9965.00068` | Corrected |
| 4 | `sim/analytics.py` | Momentum/reversal attributed to Jegadeesh & Titman with `10.1111/j.1540-6261.1993.tb05127.x` | That DOI is **Engle & Ng (1993)**, "Measuring and Testing the Impact of News on Volatility", *Journal of Finance* 48:1749-1778. Jegadeesh & Titman (1993) is `10.1111/j.1540-6261.1993.tb04681.x` | Corrected |
| 5 | four files | Almgren et al. (2005) cited as "Almgren, Thum, Hauptmann & **Pacold**", *Risk* 18(7) pages given as both 57-62 and 105-111, with DOI `10.1088/1469-7688/5/8/005` and a `risk.net` link | The fourth author is **Li**; the article is *Risk* **18(7):58-62**; it is a *Risk* magazine piece, and `10.1088/1469-7688/5/8/005` is a *Quantitative Finance* article. The `risk.net` URL returns "we can't seem to find the page you're looking for" (checked 2026-09-17) and the author-hosted PDF (`courant.nyu.edu`, redirects to `cims.nyu.edu`) returns **HTTP 403** | Author list and pages corrected everywhere; the bogus DOI and the dead link removed; the framework is attributed to **Almgren & Chriss (2001)**, *Journal of Risk* 3(2):21-40, `10.21314/JOR.2001.041` |
| 6 | `sim/strategies.py` | PEAD cited with `10.1016/0165-4101(89)90015-X`; illiquidity premium cited with `10.1016/S1386-4181(02)00011-7`; Ball & Brown cited with `10.2307/2328046` | The JAE DOI does not exist in the 1989 volume listings (the nearest, `...90015-3`, is Bonnier & Bruner on management change in distressed firms); the Elsevier DOI could not be confirmed as Amihud (2002); Ball & Brown (1968), *JAR* 6(2):159-178, is **`10.2307/2490232`** | PEAD now cites Bernard & Thomas (1989) `10.2307/2490899` and Ball & Brown (1968) `10.2307/2490232`; Amihud (2002), *Journal of Financial Markets* 5(1):31-56, is cited with a working copy of the paper at `https://www.cis.upenn.edu/~mkearns/finread/amihud.pdf` |

The audit is now a test, not a memory: `test_no_code_cites_an_unregistered_url`
requires every `http(s)` URL in `sim/` and `scripts/` to appear in the source
register, the provider catalogue, an `academic_basis` entry or a research
register, and `test_no_doi_in_the_code_is_misattributed` requires every known DOI
to sit next to the author it belongs to.

### Model-fidelity finding from the same audit (**IR-26**)

Almgren, Thum, Hauptmann & Li (2005) fitted ~700,000 Citigroup US equity orders
and **rejected the square-root model for temporary impact** in favour of a **3/5
power law**. This project's impact model uses the square-root form (the
convention from Almgren & Chriss 2001 and from every vendor model), which at 10%
of ADV charges about **26% more** impact than the measured 0.6 exponent
(0.316 vs 0.251 sigma-units). The deviation is documented rather than silently
absorbed, and making the exponent a config field is in
`research/REMAINING_WORK.json`.

---

## 4b. Defects found in this project's own code by the same pass

The audit was not only about citations. Checking the venue against the rule it
claims to implement found one real defect, recorded as **IR-28**:

`ExecutionEngine.quote_at()` builds the dealer ladder on the minimum-increment
grid and then re-centres it on the permanently-impacted mid so quotes track the
intraday path. The re-centring added an **arbitrary fraction** to every level, so
the displayed bid and ask came off the grid - the recorded opening quote for SPY
on 2025-09-17 was `bid 655.4003 / ask 655.4103`: a legal one-tick spread at two
prices that 17 CFR 242.612(b) does not permit anyone to display. It affected
8,462 quote levels and 5,902 bid/ask fields on fill rows in the primary run.
Executed fill prices were unaffected (the fill engine rounds separately), which
is why the P&L was never wrong - only the published quotes were.

The existing unit test missed it because it called `MarketMakerPool.quotes()`
directly, which has always been grid-clean; the defect was one level up, and no
test crossed that boundary. Fix: snap the shift to a whole number of increments
(`round((mid - raw_mid) / tick) * tick`), which keeps the ladder within half a
tick of the impacted mid, leaves the spread at exactly `n_ticks`, and cannot
cross the book. Two tests now hold the line - one on the venue code across every
symbol and three intraday intervals, one on the prices actually **written to
memory** - and the season was re-run and the site rebuilt afterwards, so the
numbers published here are post-fix.

**What the re-run cost, measured (IR-29).** A change of at most half a tick moved
one participant by **+28.3pp** (`@OneBigBet_Concentra`, 20.6% → 48.9%, with
closed trades rising from 36 to 55), moved five others by 4-9pp, changed one
verdict (`@SqueezeHunter_TF` from +3.8% to −1.8%), left eleven of twenty
unchanged to within 0.1pp, and reshuffled **9 of 20 ranks**. The headline moved
from +108.31% to **+108.39%**. The mechanism is discrete: whether a resting limit
order is marketable depends on which side of the touch it sits, so a fractional
shift flips fills on and off, and because every strategy is path dependent one
flipped fill compounds for the rest of the year. Rising trade counts are the
tell - these are different trades, not the same trades at slightly different
prices. That is recorded as **IR-29** rather than smoothed over, because it means
a single-path ranking is not a skill ordering; the six-scenario panel and the
sensitivity harness in `research/REMAINING_WORK.json` are the answer to it.
`tests/fixtures.py` now pins the published headline in one place, with a change
history, so the next engine change shows up as one explicit diff.

## 5. What could **not** be verified here

These are not omissions to paper over; each is flagged in
`research/IRREGULARITIES.json` and `research/LIMITATIONS.json` and shown on the
site.

1. **FINRA Schedule A** (the primary source for the TAF rate and cap) - not
   retrievable; the rate is `SECONDARY` (**IR-05**).
2. **Real intraday quotes, trades and depth** for the 17-name universe - no
   consolidated tape access; the venue is a calibrated simulation (**IR-03**,
   **L-01**).
3. **Real earnings calendars and EPS surprises** - not available offline, so the
   PEAD participant uses a price/volume proxy (**IR-07**).
4. **Real short-borrow rates and locate availability** - declared per instrument
   (**IR-08**).
5. **Dividend schedules** for 12 of the 17 names - declared, not observed
   (**IR-09**); SPY and AAPL dividends are real.
6. **Style-factor premia** - the six factor returns are *declared priors*
   re-simulated on the real index path, not measured from a real cross-section
   (**IR-16**).
7. **The DOI for Almgren & Chriss (2001)** (`10.21314/JOR.2001.041`) came from a
   reference list; resolving it from this environment returned an HTTP 500, so
   that row is `KNOWN-NOT-FETCHED` rather than `FETCHED`.

---

## 6. Audits run on the simulation itself

These are internal consistency checks, all reproduced by the test suite
(`python3 -m unittest discover -s tests`) and by `python3 -m sim.cli verify`.

| Audit | Result |
|---|---|
| Fitted SPY/index ratio vs real SPY monthly closes | ratio **10.02785**, max absolute error **0.1451%** |
| Simulated vs real SPY monthly high-low range | mean absolute difference **0.503 pp**, worst **0.993 pp**, over 11 months |
| P&L ledger closure per participant | residual **≤ $0.10**, total **$0.42** across 20 participants |
| Final positions | every account **flat** at the season end (forced liquidation costed through the venue) |
| Executed prices vs the Rule 612 tick grid | **2,984 / 2,984** fills exactly on-grid, and **all 8,534 displayed bid/ask levels** in the quote stream on-grid (`tests/test_memory.py`) |
| Decision prices vs executed prices | the decision mark stays unrounded, so the tick cost is charged rather than handed back (`test_the_decision_price_is_deliberately_off_grid`) |
| Memory checksums | every file in every run manifest verified; a deliberately corrupted stream is detected |
| Determinism | the same seed reproduces the same leaderboard bit for bit (CI re-runs the primary season into a scratch memory root and diffs it); a different seed changes ranks |
| Site freshness | `docs/` is byte-identical to a fresh `build-site` from the committed memory (CI fails if it drifts), and the published config fingerprint equals `CompetitionConfig.fingerprint()` |
| Look-ahead | strategies see only `t-1` and earlier data when deciding (`tests/test_strategies.py`) |
