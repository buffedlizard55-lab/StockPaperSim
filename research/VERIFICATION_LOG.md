# Verification log

Every external claim in this project was checked against a source, and this file
records **what was checked, when, from where, what came back, and what was
corrected as a result**. It exists because the brief asked for line-by-line
verification from official sources with links a human can re-check, and because
"the code says so" is not a source.

Statuses use the vocabulary defined in `sim/config.py`:

| Status | Meaning |
|---|---|
| `FETCHED-VERIFIED` | retrieved in this environment **and** stored under `data/real/` - the download itself for market data, a dated verbatim excerpt for a page (since 2026-09-17, IR-33: a citation with no stored artefact goes stale in silence) |
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
| FINRA Trading Activity Fee (sells) | **$0.000166/share** to 2025-12-31, **$0.000195/share** from 2026-01-01; per-trade cap **$8.30** then **$9.79** | FINRA primary fee-adjustment schedule (`https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule`), fetched 2026-09-18; general TAF guidance confirms the fee is assessed on sales | `FETCHED` - **IR-05 resolved** |

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

**What the re-run cost, measured (IR-29).** Measured twice, because the first
measurement was wrong - see §4c. With `PYTHONHASHSEED` pinned to 0 on both sides
so that the tick snapping is the *only* difference between the two runs, a change
of at most half a tick produced:

| Participant | off-grid | on-grid | Δ pp | closed trades |
|---|---|---|---|---|
| `@OverreactionFade_LT` | +40.58% | +73.46% | **+32.88** | 67 → 70 |
| `@OneBigBet_Concentra` | +20.63% | +48.94% | **+28.30** | 36 → 55 |
| `@MeanRev_Z2Sigma` | +65.48% | +74.85% | +9.37 | 53 → 53 |
| `@IlliquidRocket_Degen` | +23.15% | +15.80% | −7.35 | 16 → 13 |
| `@SqueezeHunter_TF` | +3.80% | −1.85% | −5.65 | 32 → 30 |
| `@DriftRider_PEAD` | +15.91% | +21.03% | +5.12 | 41 → 40 |
| `@KitchenSink_AllIn` | +9.52% | +5.19% | −4.33 | 357 → 354 |
| `@BetaChaser_3xProxy` (winner) | +108.31% | +108.39% | +0.08 | 66 → 66 |

Ten of the twenty participants were unchanged to within 0.05pp, ten of twenty
changed rank, and the mean absolute shift was **4.67pp**. One published verdict
flipped: `@SqueezeHunter_TF` went from "made money but lagged the index" to "lost
money". The mechanism is discrete: whether a resting limit order is marketable
depends on which side of the touch it sits, so a fractional shift flips fills on
and off, and because every strategy is path dependent one flipped fill compounds
for the rest of the year. Rising trade counts are the tell - these are different
trades, not the same trades at slightly different prices. That is recorded as
**IR-29** rather than smoothed over, because it means a single-path ranking is
not a skill ordering; the six-scenario panel and the sensitivity harness in
`research/REMAINING_WORK.json` are the answer to it. `tests/fixtures.py` now pins
the published headline in one place, with a change history, so the next engine
change shows up as one explicit diff.

## 4c. The published season was not reproducible (**IR-30**, high severity)

The CI gate added in this same pass failed on its first run, and it was right to.
Re-running the primary seed in a fresh process produced a **different leaderboard**
from the one committed to `memory/` and published on the site.

Three strategies - `@DriftRider_PEAD`, `@SqueezeHunter_TF`,
`@OverreactionFade_LT` - built their held-position collection as a **set**
comprehension and then looped over it to build their exit list:

```python
held = {s for s in ctx.symbols if ctx.position(s)}   # a set
for s in held:                                       # salted order
    ...
    exits.append(s)
```

CPython salts string hashing per process (`PYTHONHASHSEED`), so set iteration
order differs between interpreters. Exit order determines the order sell orders
are submitted, which determines the cash and margin available to the buy orders
later in the same session, which determines which entries fill - and because
every strategy is path dependent, that difference compounds for a year. Measured
with the pre-fix code, same seed, same config, same machine:

| `PYTHONHASHSEED` | `@OverreactionFade_LT` return | closed trades |
|---|---|---|
| 0 | +73.4587% | 70 |
| 1 | +73.5867% | 70 |
| 2 | **+39.8994%** | 71 |
| 3 | +73.4587% | 70 |

The committed Season 1 had been written under whichever salt that process
happened to get. The in-process determinism test that had been passing all along
**could not catch this by construction**: everything inside one interpreter
shares one hash seed.

Fix: all three loops now iterate `ctx.symbols`, the canonical universe order, and
use the set only for membership. Verified identical across `PYTHONHASHSEED`
0, 1, 2, 3 and 7 (leaderboard SHA-256 `04ff3a17fd441347…` in all five), and a
single-seed run now produces a leaderboard identical to the same seed inside the
six-scenario run - which also cleared an earlier suspicion of cross-scenario
contamination. That suspicion was this bug, not scenario ordering.

Two tests hold the line, both checked against a deliberately reintroduced copy of
the bug:

* `test_the_season_is_reproducible_across_processes` runs the short season in two
  subprocesses with different hash seeds and requires identical leaderboards.
* `test_no_strategy_iterates_a_set` is an AST sweep over `sim/strategies.py` that
  fails on any `for` loop over a name bound to a set comprehension or set
  literal. Dicts are insertion-ordered and remain safe to iterate; the sweep does
  not flag them.

**Why this entry is high severity and the others are not.** IR-28 published
quotes no exchange could display, which is a fidelity defect. IR-30 broke the
project's central claim - that the memory is an audit trail someone can re-run -
and it was invisible locally. Every number in §4b of this log was re-measured
after the fix, with hash seeds pinned, and the first (confounded) version of
IR-29 is preserved in the register text rather than quietly replaced, because
"we published a plausible number that turned out to be an artefact" is exactly
the failure mode this log exists to record.

## 4d. Link health of the register itself (**IR-33**, found 2026-09-17)

The register is the deliverable a reviewer actually clicks, so every URL in
`sim/config.py`'s verified-source register and every `links[]` entry in
`research/IRREGULARITIES.json` was re-opened in this pass. Two official URLs
returned 404 while the substance of the claims they carried was correct:

| Cited | Status today | Replacement, fetched and read |
|---|---|---|
| `finra.org/rules-guidance/rulebooks/finra-rules/7541` (Trade Activity Fee rate) | **404** - there is no Rule 7541 | [`finra.org/rules-guidance/guidance/trading-activity-fee`](https://www.finra.org/rules-guidance/guidance/trading-activity-fee), which defers rates to Section 1 of Schedule A |
| `finra.org/investors/learn-to-invest/types-investments/margin-investing/pattern-day-trader` | **404** - the investor-education section was restructured | [`finra.org/rules-guidance/notices/21-13`](https://www.finra.org/rules-guidance/notices/21-13) for the counting rule and [`/investors/insights/frequent-intraday-trading`](https://www.finra.org/investors/insights/frequent-intraday-trading) for the plain-language version |
| `finra.org/rules-guidance/rulebooks/finra-rules/4337` (used in a first draft of IR-31) | **404** - the securities-lending rule is 4330 | [`finra.org/rules-guidance/rulebooks/finra-rules/4330`](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4330), paragraph (b)(2)(B)(ii)(g) |

Fetched live in this pass and excerpted to `data/real/regulatory/`, with the
quotation that carries the weight recorded in the file:

| Source | What it settles |
|---|---|
| [FINRA Rule 4330](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4330) | a borrower of customer securities must disclose "payments deemed cash-in-lieu of dividend paid on securities while on loan" - the obligation IR-31 implements |
| [FINRA Rule 4210](https://www.finra.org/rules-guidance/rulebooks/finra-rules/4210) | the margin rule body; (f)(8)(B) is the day-trading section the venue's counter implements |
| [FINRA Regulatory Notice 21-13](https://www.finra.org/rules-guidance/notices/21-13) | the day-trade count, with six worked examples, quoted verbatim in `data/real/regulatory/finra-regulatory-notice-21-13.txt` and asserted one by one in `tests/test_portfolio.py::TestFinraDayTradeExamples` |
| [IRS Publication 550](https://www.irs.gov/publications/p550) | a short seller "may have to remit to the lender payments in lieu of the dividends distributed while you maintain your short position" - corroboration from the tax side that the short **pays** |
| [Federal Register API, document 2026-04233](https://www.federalregister.gov/api/v1/documents/2026-04233.json) | FY2026 Section 31 order: citation `91 FR 10643`, published `2026-03-04`, and **`effective_on` empty** - the date is in the prose, not the structured fields |

The Section 31 effective date was re-checked because a broker fee page disagrees
with it: the FY2025 advisory states the old rate runs "until 60 calendar days
after legislation is enacted that sets the amount of the Commission's fiscal
year 2026 appropriation", the FY2026 appropriation was signed 2026-02-03, and
2026-02-03 + 60 calendar days = **2026-04-04**, which is the date
`config.SEC31_PER_MILLION` uses. One vendor page says 04/02/2026; the order and
the arithmetic agree with each other, so the repo is right and the vendor is
approximating. `docs/sources.html` now renders 61 rows (55 source, 6 provider).

## 4e. Two accounting errors and one fidelity error, found by reading, not by testing (**IR-31**, **IR-32**, **IR-34**)

All three were found in a pass that read `sim/portfolio.py`, `sim/engine.py` and
`sim/strategies.py` line by line against the rules they claim to implement. None
of them made a test red - which is the point worth recording, because it means
this project's test suite, as strong as it is on identities, does not detect
semantic errors that leave the arithmetic self-consistent.

| Defect | What was wrong | Effect on the published season | Guard added |
|---|---|---|---|
| **IR-31** dividend entitlement | ex-date dividends were paid on the position left *after* the day's trades | 4 payments totalling $635.91 went to positions opened on the ex-date itself; 18 of 202 payment events fell on a day the symbol was also traded | `test_dividend_entitlement_is_the_position_carried_into_the_ex_date`, `test_dividend_events_match_the_entitlement_recorded_at_the_open` |
| **IR-31** payment in lieu | `pay_dividend()` returned `0.0` for any non-positive quantity, so shorts never paid the dividend they owe | $1,852.31 of manufactured dividends now charged; shorts had been collecting a free tailwind equal to roughly the yield, because `div_drag` removes that yield from the drift | `test_short_position_owes_a_manufactured_dividend`, plus the ledger-closure test now sums every bucket in `analytics.DECOMPOSITION_BUCKETS` |
| **IR-32** day-trade counter | `_day_trade_closes()` was a stub returning `True`, so *any* second fill of a session counted | zero P&L effect (proved: with the dividend fix reverted and this fix kept, the leaderboard reproduced the pre-fix committed bytes exactly), but `@OvernightCarry_NO` - a strategy that never round-trips intraday - was published with 235 "day trades" in 251 sessions | the six Notice 21-13 examples, asserted verbatim |
| **IR-34** fidelity | the venue could only execute at the opening bell, so a documented "market-on-close" exit was unexpressible and silently became a next-open exit | `@OvernightCarry_NO` +14.11% → **+8.97%**, `@GapAndGo_YOLO` −86.62% → **−91.75%**; GapAndGo's dividends received went from $6.95 to exactly **$0.00**, which is what a book that is genuinely flat by the close must earn | `test_at_close_orders_are_worked_at_the_final_interval`, `test_gap_and_go_exits_later_in_the_same_session_it_entered`, `test_only_the_close_of_session_strategies_use_the_ticket` |

The corrections were **not** applied by editing the published table. Each fix was
followed by a full re-run of all six scenarios, a rebuild of `docs/`, and a
re-derivation of the README table from `memory/runs/.../leaderboard.json` by
script - `tests/test_readme_claims.py` now compares that table cell by cell
against the memory it came from, and checks the README's own counts (test total,
register size, three research file sizes) against the files, because three
earlier passes had each left a hand-typed number stale.

Every one of these fixes was verified to be a real guard by reverting it in place
and watching the corresponding tests fail, then restoring from a backup copy.
That is the only way to know a new test tests something.

## 5. What could **not** be verified here


These are not omissions to paper over; each is flagged in
`research/IRREGULARITIES.json` and `research/LIMITATIONS.json` and shown on the
site.

1. **An eligible official individual-security price set** - the Nasdaq endpoint
   was retrieved through page-fetch and an Actions adapter is implemented, but no
   official files are committed here and Nasdaq's public-site terms do not establish
   redistribution permission. The strict gate reports `NOT ELIGIBLE` rather than
   treating Yahoo agreement as proof.
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

## 4f. The register's own measured figures were re-derived, and two were wrong

This pass did not only audit the code and the citations: every dollar and
percentage point quoted in `research/IRREGULARITIES.json` was recomputed from the
event streams of the season it describes, which is the only way a measured claim
stays checkable. Three did not survive.

| Claim as written | What the data says | Disposition |
|---|---|---|
| IR-31: "4 dividend payments totalling **$635.91** went to positions opened on the ex-date" | **not reproducible** under any definition tried (first trade of the day: 1 event, $14.46; flat at the prior close: 3 events, $92.02). The number apparently came from an intermediate diagnostic whose filter was never recorded | Replaced by the derivation now in the entry - 18 of 202 events mispriced against the entitlement carried into the ex-date, 11 overpayments $1,198.01, 7 underpayments $962.48, $2,160.49 gross, **-$235.53 net**, 7 of 20 accounts - stated with its method so a reviewer can re-run it |
| IR-31: dividends moved "$11,299.61 → **$11,662.74**", net **$1,489.18** | those are the figures for the run **before** IR-34 re-timed two books. The published season is $11,299.61 → **$11,600.83** received, **$1,852.31** charged in lieu, net **$1,551.09** | Both kept, with the attribution spelled out ($1,489.18 of it is this fix alone), because silently re-labelling a stale number as current is how a register becomes fiction |
| IR-34: "no rank changed", and GapAndGo's dividends "$**6.95** → $0.00" | 6 of 20 ranks moved (4-7 and 11-12; three of them purely because *other* accounts changed), and GapAndGo's pre-fix figure on the originally released season is **$80.46**, not $6.95, which was the intermediate run | Corrected in place |

The pattern is worth naming, because it is not a code problem. Every one of these
was true of a run that existed at the moment it was written and stopped being true
when a later fix re-ran the season. A measured claim in a register is a claim
about a specific artefact, so the register entries now name the season they were
measured on, and `tests/test_readme_claims.py` exists so that the human-readable
restatement of those numbers in `README.md` cannot drift from the memory again.

## 4g. The published site's own provenance line, checked after it went live (**IR-35**)

PR #3 merged, CI was green, and the live Pages deployment was read as a human
would read it. Its footer said `git commit 71b3d9062d2c` next to a season that
commit could not have produced: the memory in the repository had been generated
from a working tree holding the then-uncommitted IR-31/IR-32/IR-34 fixes. The
manifest recorded `code.git.dirty = true` and a SHA-256 for every `sim/` module,
so the data was honest; nothing a reader sees said so. Checked out literally,
that commit reproduces the *pre-fix* season, and GapAndGo's published **−91.75%**
would have looked fabricated next to its **−86.57%**.

Three fixes followed, then one verification:

| Change | File |
|---|---|
| The footer renders all three states of `dirty` and names the module hashes as the fingerprint when a commit cannot be trusted | `scripts/build_site.py` (`provenance_sentence`) |
| Provenance is read from the module's own tree, not the parent of the memory root - a scratch `--memory-root` used to resolve to `/tmp` and find nothing | `sim/memory.py` (`source_root`) |
| `dirty` is `True`/`False`/`None`; a failed git call no longer reads as a clean tree | `sim/memory.py` (`_git_state`) |
| Four tests, one of which compares `docs/index.html`'s footer against the committed manifest. It was red before the season was regenerated, which is what makes it a test and not a description | `tests/test_site_builder.py`, `tests/test_memory.py` |

Verification: all six scenarios were re-run from the clean committed tree into a
scratch root. `leaderboard.json` and `market_report.json` came out **byte-identical**
to the published files, so the memory now committed names a commit that contains
the code which ran, and the reproducibility claim was exercised on the published
artefact rather than on a copy. One residual limit is stated instead of fixed: a
squash merge means `main` never contains the PR-head commit a manifest names, so
the durable provenance of a run is its per-module hashes, not the commit string.

## 4h. A second sweep: the register's runtime counts were stale too (**IR-13**, **IR-14**, **IR-15**)

§4f re-derived the dollar figures in the register. Reading the deployed
`docs/irregularities.html` afterwards exposed the same failure in the entries that
quantify *runtime* events, which nobody had recomputed because those numbers are
not in the leaderboard and no test compared them with the run:

| Entry | Register said | The published run says | Corrected |
|---|---|---|---|
| IR-13 (a participant blowing up) | `@GapAndGo_YOLO finished at -86.67%` | **−91.75%** | the number had been overtaken by two re-runs |
| IR-14 (pre-trade rejects) | `@SpreadHarvester_MM had 131 such rejections` | **67**, and **115** across the three participants that hit the cap | the season total is now quoted beside the peak |
| IR-15 (size clips) | `195 clips for @SpreadHarvester_MM and 44 for @KitchenSink_AllIn` | **193** and **44**, **259** in total | ditto |
| L-14 (memory scale) | `about 38,000 event rows and 4.3 MB` | **36,597** rows across 8 streams, **4.08 MB** | exact, since it is cheap to be |
| IR-23 (beta/alpha) | `alpha +44.9%/yr` | **+44.08%/yr** | corrected |
| IR-21, IR-22, IR-29 | measured deltas quoted as if current | those were measured on the memory at commit 71b3d90 | each now **names the artefact it was measured on**, because a measured claim about a regenerable run is only meaningful with its run attached |

`tests/test_sources_register.py::TestRuntimeRegisterClaims` closes this class: it
re-aggregates the published run's own `irregularities.json` (the engine stores one
aggregated row per participant and per normalised reason, with the participant
embedded in the message) and requires IR-14 and IR-15 to state that peak and that
total, and requires IR-13 to quote the leaderboard's actual worst return. It is
the guard that would have caught all five stale numbers on the day they went
stale.

What this pass could *not* verify was also recorded rather than dropped: the
pre-fix figures quoted in IR-31 are now recoverable only through git
(`git show 71b3d90:memory/runs/season1-primary-seed20260917/events/carry.jsonl.gz`),
which is written into the entry so a reviewer is not left trying to re-run a
season from a commit whose memory no longer exists.

## 6. Audits run on the simulation itself




These are internal consistency checks, all reproduced by the test suite
(`python3 -m unittest discover -s tests`) and by `python3 -m sim.cli verify`.

| Audit | Result |
|---|---|
| Fitted SPY/index ratio vs real SPY monthly closes | ratio **10.02785**, max absolute error **0.1451%** |
| Simulated vs real SPY monthly high-low range | mean absolute difference **0.503 pp**, worst **0.993 pp**, over 11 months |
| P&L ledger closure per participant | `final_equity - starting_cash - net_pnl_usd` is **exactly $0.00** for all 20 published reports (`scripts/independent_audit.py`); the `tests/test_memory.py` fixture season closes to **$0.42** over 20 participants, worst **$0.10** |
| Final positions | every account **flat** at the season end (forced liquidation costed through the venue) |
| Executed prices vs the Rule 612 tick grid | on the **published season**: **3,078 / 3,078** fills and **8,534 / 8,534** displayed bid/ask levels are exactly on-grid. The smaller fixture season in `tests/test_memory.py` (2,985 fills) checks the same property on a run whose bytes the test regenerates, so both are quoted to avoid a reader mistaking one for the other |
| Decision prices vs executed prices | the decision mark stays unrounded, so the tick cost is charged rather than handed back (`test_the_decision_price_is_deliberately_off_grid`) |
| Memory checksums | every file in every run manifest verified; a deliberately corrupted stream is detected |
| Determinism, in-process | the same seed reproduces the same leaderboard bit for bit; a different seed changes ranks |
| Determinism, **across processes** | the leaderboard hash is identical under `PYTHONHASHSEED` 0, 1, 2, 3 and 7, and CI re-runs the primary season into a scratch memory root and diffs it against the committed one (IR-30) |
| Order-of-iteration audit | AST sweep: no `for` loop in `sim/strategies.py` iterates a set |
| Site freshness | `docs/` is byte-identical to a fresh `build-site` from the committed memory (CI fails if it drifts), and the published config fingerprint equals `CompetitionConfig.fingerprint()` |
| Look-ahead | strategies see only `t-1` and earlier data when deciding (`tests/test_strategies.py`) |
| **Independent audit** | `scripts/independent_audit.py` re-derives every published number from the raw event streams with code that never imports `sim`: 763 checks on the primary season (cash roll-forward, equity identity, fee components per fill, dividend and borrow ledger, tick grid, round-trip counts, win/loss and profit factor, Sharpe, Sortino, max drawdown, beta, leaderboard ranks). **763 / 763 pass.** Proven to bite: inflating one report's return by 5.0 pp and inventing three trades produced 2 failures; deleting one dividend carry row produced a cash drift of $72.02 on 2025-11-20 plus a ledger mismatch; nudging a fill price by 37 hundredths of a cent produced a Rule 612 grid violation |
| README self-description | `tests/test_readme_claims.py` recomputes the suite's test count, the register size and the three research-file counts, and re-reads all 20 leaderboard rows against `memory/runs/season1-primary-seed20260917/leaderboard.json` |

## 7. Official price eligibility pass (2026-09-18)

The official Nasdaq historical endpoint was fetched through the supported page-fetch
path with ISO dates. A representative two-year AAPL request returned `totalRecords:
503` rows with date, open, high, low, close and volume; the same endpoint pattern was
previously observed for SPY, XOM, JPM, GLD and FLUT. The direct local `urllib` path
still failed with a TLS EOF, so the adapter is run by the GitHub Actions collector,
not by pretending the development network succeeded.

The implementation is in `scripts/collect_real_data.py`. For each tradable symbol it
writes the normalized Nasdaq file, the exact raw historical response, a raw SHA-256,
request URL, HTTP status, retrieval timestamp, symbol/date range, and an official
Nasdaq dividend response/status. `sim/eligibility.py` independently checks those
fields, matches the raw checksum to `collection_manifest.json`, validates OHLCV
relationships and volume, requires every FRED session in the requested window, and
requires an explicit accepted redistribution status.

The audit was run against the committed checkout on 2026-09-18:
`python3 -m sim.cli price-audit --symbols AAPL,SPY --start 2024-09-16 --end 2026-09-16`.
It returned non-zero with `MISSING_FILE` for both symbols because no official Nasdaq
files have been downloaded into this checkout. This is intentional fail-closed
behavior. Even after collection, the adapter records `NOT_AUTHORIZED_BY_TERMS` until
Nasdaq redistribution permission is verified; that status cannot authorize a
competition. The existing Yahoo files remain reproducible `SECONDARY` research data
and are not silently replaced.

---

## Live Book session (2026-09-18 → 2026-09-19)

The Live Book asked a question the earlier sessions had not: whether a strategy can
place a trade for a session that has not happened yet and have it settled against a
verified bar later. Answering it needed a new set of official, free, publicly
available series (an index leg for the Nasdaq and the NYSE, and a financing rate),
a calendar that is honest about which sessions are observations and which are
projections, and a trading clock that cannot reach into the future. Everything
retrieved in that session is recorded below, in the order it was checked.

### Real market data retrieved (stored, checksummed)

| # | Series | Publisher (as FRED states it) | URL | Result | Stored as |
|---|---|---|---|---|---|
| 1 | `NASDAQCOM` — NASDAQ Composite, daily close | Nasdaq, Inc. (Release: Nasdaq Daily Index Data) | <https://fred.stlouisfed.org/graph/fredgraph.csv?id=NASDAQCOM&cosd=2024-09-16&coed=2026-09-17> | 200, 524 rows, 503 valued, 2024-09-16 → 2026-09-17 | `data/real/fred/NASDAQCOM_2024-09-16_2026-09-17.csv` |
| 2 | `DJIA` — Dow Jones Industrial Average, daily close | S&P Dow Jones Indices LLC (Release: Dow Jones Averages) | <https://fred.stlouisfed.org/graph/fredgraph.csv?id=DJIA&cosd=2024-09-16&coed=2026-09-17> | 200, 524 rows, 503 valued, 2024-09-16 → 2026-09-17 | `data/real/fred/DJIA_2024-09-16_2026-09-17.csv` |
| 3 | `SOFR` — secured overnight financing rate | Federal Reserve Bank of New York | <https://fred.stlouisfed.org/graph/fredgraph.csv?id=SOFR&cosd=2024-09-16&coed=2026-09-17> | 200, 524 rows, 500 valued, 2024-09-16 → 2026-09-17 | `data/real/fred/SOFR_2024-09-16_2026-09-17.csv` |
| 4 | `SP500` re-fetch (window refresh) | S&P Dow Jones Indices LLC | <https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500&cosd=2026-08-01&coed=2026-09-17> | 200, 34 rows; **identical to the committed file** on all 26 overlapping valued dates, including 2026-09-17 = 7637.76 | already committed |

**The three new files are transcribed, so they were verified mechanically rather
than by eye.** `scripts/verify_official_extracts.py` re-reads each file and checks
that the header matches the registered series, that every date is ISO and strictly
increasing with no weekend rows, that every value is a finite positive decimal, that
the *valued* date set agrees with the runner-collected `SP500` file that has been
in the repository since an earlier session, and that the stored bytes hash to the
recorded SHA-256. It writes `data/real/fred/AGENT_FETCH_VERIFICATION.json` as the
artefact and exits non-zero on any disagreement. The run found two genuine
disagreements with the equity calendar, both investigated and both recorded as
IR-52 rather than smoothed over:

* SOFR has **no** observation on 2024-10-14, 2024-11-11, 2025-10-13 and 2025-11-11
  — Columbus Day and Veterans Day, when the bond market is closed and the NYSE is
  not.
* SOFR **does** carry an observation on 2025-01-09 (4.30), when the NYSE and Nasdaq
  were closed for the National Day of Mourning for President Jimmy Carter while the
  bond market traded a shortened session.
  * <https://ir.nasdaq.com/news-releases/news-release-details/nasdaq-announces-closure-its-us-markets-honor-national-day-0> — Nasdaq closed all U.S. equities and options markets on Thursday, January 9, 2025.
  * <https://www.nasdaq.com/press-release/new-york-stock-exchange-will-close-markets-january-9-honor-passing-former-president> — NYSE Group closed all equity and options markets the same day.
  * The money market ran a shortened session with a 2:00 p.m. ET close on SIFMA's recommendation, which is why a published SOFR observation exists for a day the equity market was shut.

### Independent cross-check on the financing rate

The same two dates were read from a **different official publisher's endpoint**, so
the agreement is between two publishers rather than between a file and itself:

* <https://markets.newyorkfed.org/api/rates/secured/sofr/last/10.json> → HTTP 200,
  `percentRate` **3.85** for `effectiveDate` 2026-09-17 and **3.62** for 2026-09-16.
* `data/real/fred/SOFR_2024-09-16_2026-09-17.csv` prints **3.85** for 2026-09-17 and
  **3.62** for 2026-09-16.
* `tests/test_live.py::TestOfficialFeed::test_cross_checked_sofr_values_match_the_publisher_api`
  asserts both pairs, so the cross-check cannot rot silently.
* The same API response shows a 23 bp one-day jump into quarter end (3.62 → 3.85),
  which is a settlement effect and not a policy signal; the `@SOFRPivot_Rider`
  participant page names that trap in its declared failure modes rather than
  presenting the jump as an easing/tightening signal.

### The forward session projection (publisher-verified)

Sessions after the last collected date are not observations, so they are projected
from the publisher's own calendar and labelled `PROJECTED` everywhere they appear.

* <https://www.nasdaqtrader.com/trader.aspx?id=calendar> — "U.S. Equity and Options
  Markets Holiday Schedule 2026". Read in-session. For the remainder of 2026 it
  lists **2026-11-26** (Thanksgiving, closed), **2026-11-27** (early close,
  1:00 p.m.), **2026-12-24** (early close, 1:00 p.m.) and **2026-12-25** (Christmas,
  closed).
* <https://www.nasdaq.com/market-activity/stock-market-holiday-schedule> — the
  consumer page of the same schedule; it agrees row for row.
* **No 2027 dates are projected.** The 2027 schedule was not read from the
  publisher in this session, and guessing it would be exactly the invention the
  module refuses to do. `PROJECTION_LIMIT = "2026-12-31"`.

### Defects found by verification rather than by a failing test

Three defects in the new code were found by reading what the book published, and all
three are now regressions in `tests/test_live.py`:

1. **Trades aimed at market holidays** (IR-49): 34 intents targeted 2025-12-25,
   2026-01-01, 2026-04-03 and six other closures because the horizon projected
   weekdays inside the collected window. Fixed by taking the horizon from the
   collected session list and expiring any intent aimed at a known closure.
2. **Maintenance calls recorded and ignored** (IR-50): `maintenance_breach` returned
   `None` for non-positive equity, so `@CrowdFade_Live` finished the rehearsal at
   −118.20% with a permanently negative balance. Fixed by liquidating a breached
   book at the next session's open; the same run now ends at −45.90%.
3. **`RealMarketData` is undefined** (IR-51): an annotation that only survives
   because `from __future__ import annotations` never evaluates it.
4. **The new tests rewrote committed memory.** `tests/test_live.py` called the
   rehearsal with the default memory root, so running the suite regenerated
   `memory/live/*` — which would make the published-site CI job's "rebuild docs/
   and diff it" gate fail on every push, and did make the committed
   `docs/assets/data/live.json` stale the first time. Every live test now writes
   into its own temporary root and only *reads* the committed one, and
   `test_the_test_suite_does_not_rewrite_the_committed_live_memory` asserts that
   the published payload still matches the memory it was rendered from.

### Sources checked and found *not* usable in this session

| Attempt | URL | Result |
|---|---|---|
| Cboe delayed-quote JSON for `_VIX` | <https://cdn.cboe.com/api/global/delayed_quotes/indices/_VIX.json> | `AccessDenied` from the CDN for this client; the official VIX close is therefore read from FRED's republication of it, and the publisher stated there is Cboe Global Markets |
| FRED series `GOLDPMGBD228NLBM` (LBMA gold PM fix) | <https://fred.stlouisfed.org/series/GOLDPMGBD228NLBM> | "page not found" — the series is discontinued, confirming the note already in `sim/realdata.py`. The gold participant therefore states that its signal is the rate pair and its instrument is GLD, not a gold benchmark |
| FRED series `NYSECOM` (NYSE Composite) | <https://fred.stlouisfed.org/graph/fredgraph.csv?id=NYSECOM&cosd=2026-09-10&coed=2026-09-17> | "page not found" — FRED does not carry that id, so the NYSE leg of the brief is implemented against the Dow Jones Industrial Average, which FRED does carry and which is stated as the NYSE-listed blue-chip leg on the page |
| SEC XBRL frames endpoint | <https://data.sec.gov/api/xbrl/frames/us-gaap/EarningsPerShareDiluted/USD/CY2025Q4I.json> | `NoSuchKey` — the frame does not exist for that tag and period, so the compact fundamentals path was not used and is left for the next session |
| FRED multi-series CSV with a window | <https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500,VIXCLS&cosd=2026-09-10&coed=2026-09-17> | Returned the full history of both series from 1990-01-02 in 22 pages, **ignoring `cosd`/`coed`**; a single `id` honours the window. Recorded as IR-53 |
| Dispatching the collection workflow | `gh workflow run "Collect real market data" --ref arena/01a0b62b-stockpapersim` | HTTP **403 Resource not accessible by integration** (workflow id 361020102). The token can push and open pull requests but has no `actions:write`, so the runner-based collection — which is the only way to reach EDGAR from this project — could not be started. Recorded as IR-54 and as a P0 item in `REMAINING_WORK.json` |

### FINRA's official daily short-sale volume file (verified, not yet collected)

<https://cdn.finra.org/equity/regsho/daily/CNMSshvol20260917.txt> returned HTTP 200
with a pipe-delimited body `Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market`.
The 2026-09-17 rows read, verbatim:

```
20260917|AAPL|8061052.786287|14583|13766217.054549|B,Q,N
20260917|A|716675.685028|6|927492.863747|B,Q,N
```

This is the only **official, free** source of daily *total* volume per symbol that
this project has found, which makes it the right anchor for the participation and
liquidity model instead of a secondary file's volume column. The whole file covers
roughly 13,000 symbols in about 69 page-sized chunks, so it is a job for the
runner's collector, filtered to the traded universe before storage — recorded as a
P1 item rather than half-collected here.

## Official Auction Book session (2026-09-18 → 2026-09-19)

This session added the lane the brief's first requirement names: **simulated settled
trades built from real verified official pricing, dates and liquidity**. It is the
first book on this site that may only execute on a number a publisher printed, so
the log records the sources one by one and then the defects that reading its tape
found.

### The two official publishers, and what each one gave

| Source | URL | What was verified |
| --- | --- | --- |
| TreasuryDirect auction results | <https://www.treasurydirect.gov/TA_WS/securities/auctioned> | Returned JSON rows for completed auctions with `pricePer100`, `highDiscountRate`, `highYield`, `highInvestmentRate`, `offeringAmount`, `totalAccepted`, `totalTendered`, `competitiveAccepted`, `nonCompetitiveAccepted`, `primaryDealerAccepted`, `indirectBidderAccepted`, `directBidderAccepted`, `somaAccepted`, `bidToCoverRatio`, `allocationPercentage`, `minimumToIssue`, `multiplesToIssue`, `maximumNonCompetitiveAward`, `reopening`, `tips`, `auctionDate`, `issueDate`, `maturityDate` |
| TreasuryDirect results by date range | <https://www.treasurydirect.gov/TA_WS/securities/search> | The same fields for every auction in a window; this is what backs the season (471 auctions in the last-45-day window, 2,000 in the Fiscal Data table) |
| Fiscal Data API | <https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query> | A second official publication of the same events with its own schema and field names. The collector compares **471 shared auctions on 11 fields — 4,251 comparisons, 0 differences**, written to `data/real/crosschecks/treasury_crosscheck.json` |
| Treasury par yield curve | <https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/> | Official constant-maturity par yields, fetched per year (2024, 2025, 2026) and used to price the secondary leg by the formula printed on `docs/official/method.html` |
| Federal Reserve H.15 | <https://www.federalreserve.gov/releases/h15/> | The release the bill secondary-market rates (`DTB4WK`, `DTB3`, `DTB6`) and the TIPS real yields (`DFII5`, `DFII10`, `DFII30`) come from; the FRED copies are the collected form, and `DTB*` was added in this session so a bill's secondary mark has an official quote instead of a curve-derived one |
| 31 CFR Part 356 | <https://www.ecfr.gov/current/title-31/subtitle-B/chapter-II/subchapter-A/part-356> | The auction rules the primary leg follows: non-competitive bidding, the maximum award, award at the single published price, and the price/yield formulas |
| SEC insider data sets | <https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets> | **Attempted and refused**: HTTP 403 at both documented path layouts for all eight quarters requested. Recorded in `data/real/collection_manifest.json` and registered as L-27 |

### Arithmetic checked against the published numbers

* **1,485 published bill prices** reproduce from their own published discount rate
  by `100 (1 − d t/360)` with **0 mismatches**, to 1e-4 of a cent.
* The **investment-rate** check is partial and stays partial: 1,106 of 1,415
  published rates match the 365-day convention (78.163%) and the mismatches imply a
  1.0026–1.0028 day-count factor. The code reports the agreement rate and the
  implied factors rather than switching conventions to force a match.
* A 4-week bill auctioned 2026-09-17 (`912797VM6`) was re-priced by hand from its
  published 3.820% discount rate and 28 days: formula price 99.702889, published
  price 99.702889.

### The publication the verification produced

| Item | Value |
| --- | --- |
| Window | 2025-09-17 → 2026-09-16, 250 official sessions |
| Participants | 14 strategies, one username each, $100,000 each |
| Settled round trips | 160 from 243 fills and 2,814 intents |
| Verification | **PASS** — 7,947 checks, 0 failures, largest equity residual $0.036 |
| Official executed notional | 100% of this book's closed-trade notional (primary = published price; the secondary leg is labelled OFFICIAL-DERIVED) |
| Irregularities in the run | **none** (look-ahead, target-not-forward, plan errors and ruin events all empty) |

### Defects this session found, and how

All three were found by re-reading one account's tape line by line, not by a
failing test, and all three are registered:

1. **IR-55 — the netting test was inverted.** `apply_fill()` closed lots of its own
   direction, so a buy made the net long fall *and* the cash fall. A 2s10s
   flattener went from +$134,817 to −$103,429 in one session and was wound up at
   zero. The run's own verification passed while the engine was wrong, which is why
   the fix added an account-level assertion (equity re-derived from fills and carry
   rows) rather than only a regression test.
2. **IR-56 — opposing lots accumulated in one CUSIP.** Exposure, financing and the
   leverage cap were summed over lots rather than over the net position, so a rule
   that traded both directions in one security was charged twice and allowed to add
   size its real position did not justify.
3. **IR-57 — a secondary trade was booked in a security that had not been issued.**
   The price came from the par curve and the security was days from issuance, so the
   tape carried a plausible, entirely invented number. The venue now refuses
   when-issued trades with the issue date in the settlement note.

### What the lane cannot do yet, stated on the site rather than in a footnote

US equities and ETFs have no official, redistributable price in this repository
(IR-58), so the equity books keep their SECONDARY label and the coverage number.
The official lane answers that part of the brief with the instrument family where a
publisher *does* print the price of every trade: US Treasury auctions. The
limitations that remain are registered as L-27 to L-35, and the next session's
queue is in `REMAINING_WORK.json`, headed by the SEC 403.

## Official Auction Book, second pass (2026-09-19): the reasons a rule stood aside

Pass 1 built the lane and its audit; this pass went looking for the failure mode a
green verification cannot see — a rule that does nothing and looks patient. The
tape was read account by account, the collection run that landed during the pass
was merged first (it brought the three DFII real-yield series and the Treasury
tapes), and the book was re-run and re-audited afterwards.

| | |
|---|---|
| Run | `memory/official/official-rehearsal-seed20260918` — 250 official sessions, 2025-09-17 → 2026-09-16, 14 participants, $100,000 each |
| Verification | **PASS** — 8,045 checks, 0 failures, largest equity residual $0.035155 |
| Independent audit | **PASS** — 1,864 checks, 0 failures, report at `memory/official/ledger/independent_audit.json` |
| Activity | 2,898 intents, 302 fills, 189 settled round trips |
| Winner | `@FrontEndRollDown_13W` **+4.42%**, carrying two short bills; the inflation rule is last but one at **−59.77%** |
| Median | **−39.07%** |

The numbers in that table are the state **after** the collection run described
below landed, which is the state this branch publishes. Mid-pass, before that run,
the same table read: 2,662 intents, 300 fills, 189 round trips, winner
`@TIPSBreakeven_Rider` **+5.58%** with zero orders, median **−24.20%**, 7,573
verification checks. Both states are recorded because the difference between them
is the point of the pass.

### The defect this pass found (IR-61)

The inflation rule compared a **ten-year** nominal breakeven with a **five-year**
realised inflation rate, while buying whichever TIPS had auctioned most recently —
a security with 29 years left. Three horizons were being called one breakeven.
Nothing failed when it was wrong, because the rule's other outcome was *also* no
orders: renaming the realised-inflation helper made the planning call raise
`AttributeError`, the venue's per-rule guard turned that into a private
`PLAN-ERROR` flag, and the standings looked identical either way.

Fixed by reading both legs at the security's own remaining maturity, and by making
the venue keep what the rule says when it decides not to trade.

### What the fix changed about the published record

| | |
|---|---|
| `notes.jsonl.gz` | new stream beside the other six: `{session, participant, note}`, 723 rows this run |
| Participant pages | a **Why it stood aside** card, reasons ranked by how often they recurred |
| `buy_at_auction` | a bid for an auction that has not priced yet is sized at the newest published price of the same type and term, and the rationale records that this was a sizing input (L-37) — the executed price is unchanged: the auction's own published number |
| `OfficialRates._find` | picks the **longest** collected window of a series instead of the first name that sorts (the CPI index now has more than one file requested) |
| Collector | `FRED_SERIES_WINDOWS["CPIAUCSL"] = "1990-01-01"` — a five-year realised rate cannot come from one year of observations, and narrowing the rule's window to fit the file would have been the wrong repair |
| CI | the independent audit now writes its report into the committed memory and fails if the committed copy changes, so the check count the README quotes is re-derived, not typed |

### The same class of defect, one layer up: the README's own paragraph

The official section of `README.md` was written at the end of pass 1 quoting that
run's numbers. Merging the collection run and fixing IR-61 replaced every one of
them — winner, return, trip count and the verification count all moved — and
nothing failed, because no test read that section. `tests/test_readme_claims.py`
now re-derives the paragraph from `memory/official/…/leaderboard.json`, the
manifest and the committed audit report, including the requirement that a winner
with zero trades is described as cash interest rather than as a strategy result.

### What changed when the wider CPI file landed (same pass, later)

The push that carried the collector change fired the temporary collection
workflow, and the runner returned `CPIAUCSL_1990-01-01_2026-09-17.csv` — 440
monthly observations, 1990-01-01 to 2026-08-01. The book was re-run on that file:

* the inflation rule stopped standing aside and **bought** — a 30-year TIPS,
  360,400 face at 98.655276 on 2025-09-18 and a further 28,700 face at 101.054399
  on 2025-11-28, both at the venue's official real-yield mark (OFFICIAL-DERIVED),
  on the way to a **−59.77%** return;
* the winner changed a second time, to `@FrontEndRollDown_13W` at **+4.42%**, which
  holds two short bills — its return is bill carry, not a duration call;
* the median fell from −24.20% to **−39.07%**, and the verification count rose from
  7,573 to **8,045** because there are now open positions to re-price every
  session.

**A second instance of the same defect class, on the same page.** With the rule
holding a marked-down position and no closed trips, the post-mortem still said
*"No trade closed inside the window. The cash return is the official SOFR credited
on the balance, not a strategy result."* — the generator equated "no round trips"
with "no position". The narrative and the participant page now mark the open
positions from the venue's own marks stream and report the number: two positions,
**−$43,801.28** unrealised at the final session's official mark, plus $16,528.92 of
financing. This is the same shape of error as IR-61 — a sentence that is true of a
different account — and it is why the README's official paragraph is now checked
by a test that re-derives it from the run.

---

## 2026-09-18 — Forward Book Settlement & US Equities Simulator Verification

### Forward Book Settlement on Session 2026-09-17
* **Verified Price Inputs (FRED & Daily Snapshots)**:
  - FRED `SP500`: 7,637.76 (prior: 7,551.81; daily return: +1.1384%).
  - FRED `NASDAQCOM`: 26,418.30 (prior: 25,978.42; daily return: +1.6932%).
  - FRED `DJIA`: 51,778.04 (prior: 51,461.90; daily return: +0.6141%).
  - FRED `VIXCLS`: 15.44 (prior: 17.71; −2.27 pts).
  - FRED `SOFR`: 3.85% (prior: 3.62%).
  - Daily Treasury Par Yield Curve: 1M 4.09%, 3M 4.12%, 6M 4.22%, 1Y 4.18%, 2Y 4.21%, 5Y 4.49%, 10Y 4.94%, 30Y 5.25%.
  - Verified `AAPL` snapshot bar on 2026-09-17: open $334.76, high $336.47, low $330.19, close $336.19, volume 18,616,475 shares.
* **Execution Verification**:
  - The 16 pending forward intents planned on 2026-09-16 targeting 2026-09-17 have been executed through `LiveBook.settle('2026-09-17')`.
  - All orders crossed spreads under Rule 612 ($0.01 tick), walked displayed depth books, absorbed Almgren-Chriss market impact, and paid statutory SEC Section 31 fees ($20.60/million), FINRA TAF ($0.000195/share), FINRA ORF, and exchange fees.
  - 157 live book verification assertions passed with zero defects.
* **Registered Irregularities & Limitations**:
  - Added `IR-62` and `L-39`: Autonomous real intraday capture lane commits while interactive stream monitoring is blocked.
  - Added `IR-63` and `L-38`: TradingView Strategy Report plan-gated and blocked from unauthenticated export.
  - Updated `L-26`: Forward book simulated settled trades with real verified pricing, dates, and liquidity.
  - Added 4 verified primary sources to `config.all_verified_sources()` (total 64 sources).


---

## 2026-09-19 — Venue Administration & Forward Pilot Pass

### Live verifications made this session (agent page tools; sandbox has no direct egress)

* **SEC Section 31 rate** — confirmed against FINRA Information Notice
  20260317: $0.00 per $1,000,000 → **$20.60 per $1,000,000 effective
  2026-04-04**. Matches `SEC31_PER_MILLION`.
  <https://www.finra.org/rules-guidance/notices/information-notice-20260317>
* **FINRA Trading Activity Fee (2026)** — confirmed against the FINRA
  fee-adjustment schedule: **$0.000195 per share on sales, maximum $9.79 per
  trade**. Caught and fixed a wrong "minimum $0.01" clause in the register
  claim text (that minimum applies to security-futures round turns only,
  IR-72); found FINRA's By-Laws Section 1 rule-text page lagging its own dated
  schedule ($0.000166/$8.30 shown) — IR-74.
  <https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule>
* **NYSE 2026 holidays + early closes** — read from the exchange's own page
  (Good Friday 2026-04-03; Independence Day observed 2026-07-03; early closes
  1:00pm ET 2026-11-27 and 2026-12-24). Embedded with check date into
  `sim/venue_admin.py`. <https://www.nyse.com/trade/hours-calendars>
* **Federal Reserve settlement holidays 2026** — read from FRBservices; the
  old `/resources/holidays` path now 404s (IR-73), live path is
  `/about/holiday-schedules`. Saturday-holiday rule recorded (Fed open
  2026-07-03 even though markets close). <https://www.frbservices.org/about/holiday-schedules>
* **Nasdaq Trader halts RSS document** — live document read; RSS 2.0 with
  per-item CDATA tables; fixed column order recorded in
  `data/real/regulatory/nasdaq-trader-halts-feed-structure.txt` and implemented
  1:1 by `sim/venue_admin.parse_halts_feed`. <https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts>
* **Equity feed re-review** — decision table with usage-rights status for
  FRED, Treasury/FiscalData, Nasdaq public endpoints, SIP, Yahoo, Stooq,
  Alpha Vantage/Polygon/Finnhub/Tiingo (excluded by the no-freemium-keys rule),
  IEX Cloud (discontinued), Kalshi: `research/EQUITY_FEED_REVIEW.md`.

### What shipped in this pass

* `sim/venue_admin.py` — trading calendar (NYSE), settlement calendar
  (FRBservices, T+1), official halts parser, sourced CorporateActionTable,
  `regulator_fees()` (SEC 31 + FINRA TAF arithmetic with dated schedules).
* `sim/equity_pilot.py` — the durable forward pilot: calendar-checked,
  timestamped order submission into the strict hash-chained journal, gate
  refusals journaled (`NO_APPROVED_OFFICIAL_FEED`), market-making gate closed
  for lack of queue evidence, per-run reconciliation. Seeded rehearsal run
  archived at `memory/pilot/run-2026-09-21/` (8 orders, 0 fills, flagged
  SEEDED_REHEARSAL).
* `.github/workflows/equity-pilot.yml` — weekday scheduler committing to the
  `pilot/scheduled` branch and opening a PR (the repo's
  no-unreviewed-scheduled-commits policy is preserved).
* `scripts/build_site_pilot.py` — `docs/pilot/` section: overview, upcoming
  intents (PLAN ONLY), order blotter with refusals, method (incl. corrected
  fee documentation), sources with manual-review links.
* Registry: `nasdaq-halts` source + 4 new grounded designs
  (`halt-resumption-momo`, `holiday-drift`, `settlement-gap`,
  `corp-action-standdown`) — 55 hypotheses total, each with explicit blockers.
* 34 new tests (`test_venue_admin`, `test_equity_pilot`, `test_site_pilot`);
  full suite green at 612.

---

## 2026-09-19 (third pass, same day) — roster 19, insider bulk, slate clocks, injury archive

* **Nasdaq legal terms re-read directly** (page-fetch tool, agent network):
  <https://www.nasdaq.com/legal>, agreement "Last Updated: May 11, 2026".
  Section 2 requires users not to "access or use the Service, or any process,
  whether automated or manual, to capture data or content from the Service or
  circumvent any mechanisms for preventing the unauthorized reproduction or
  distribution of the Service for any reason" — verbatim quote kept above.
  Status: `FETCHED`. The equity-feed BLOCKED conclusion is re-confirmed by
  current text, not memory.
* **Stooq terms re-checked**: `stooq.com/term.php` returns "The page you
  requested does not exist"; the homepage and help-centre footers now point at
  <https://stooq.com/terms.html>, which did not render usable terms text
  through the fetch tool. Status: `FETCHED` (footer) / no grant located.
  Stooq stays unused as a source.
* **SportsPred repository layout checked through the official GitHub API**
  (`api.github.com/repos/buffedlizard55-lab/SportsPred/git/trees`): the project
  is a collector-driven multi-sport prediction site with per-sport workflow
  files and client-side data plumbing; no single stable predictions JSON was
  identified in the time budget, so `@SportsPred_Forward` stays a declared
  forward-only probe rather than snapshotting a guessed URL. Status: `FETCHED`.
* **ESPN scoreboard row shape re-derived from the committed files** (not from
  the API): 285/285 NFL rows and 282/282 NCAAF rows are `STATUS_FINAL` with
  both scores present (`data/real/sports/*.jsonl`), which is what the new
  slate clocks count. The NBA file is empty in this checkout — the ESPN NBA
  weeks configuration postdates the last sports collection — so
  `@NBA_Slate_Attention` reports DATA-MISSING until the next collection run.
* **SEC insider bulk reader**: the signal builder now normalises the quarterly
  data-set rows (`symbol`/`transaction_code`/`owners[].title`) into the same
  shape as the per-filing walk (`ticker`/`code`/`title`) and merges the two
  de-duplicated on (accession, date, ticker, code, shares, price). The
  trailing buy/sell ratio was made genuinely trailing-30d (it was a whole-file
  constant before; no published number depended on it because no insider file
  had ever landed). Covered by `tests/test_masterfeed_sports.py`.
* **Season 2 re-derived** with the 19-persona roster: 976 fills, 480 round
  trips, net round-trip P&L $52,994.38 on $23.9m notional, ledger digest
  `35b5aa7d…`; independent audits 3,393 + 1,222 checks, 0 failures; full
  suite 625 tests green.
