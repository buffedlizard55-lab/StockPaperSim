# Official equity quote/trade feed review — 2026-09-19

**Question from the brief:** obtain an approved, official quote/trade feed with
documented usage rights, using only publicly available free sources; no
freemium or trial API keys.

**How each candidate was checked:** the publisher's own terms/documentation
pages were read during this session (through the agent page-fetch tools; the
sandbox has no direct egress, so live URL checks happen in CI and on page
re-review). Retrieval is never treated as permission: a fetchable URL answers
"can a runner read it", not "may this repository republish it". That
distinction is why `research/strict/registry.json::approved_feeds` is empty.

**Re-verification, later the same day (second pass):**

* **Nasdaq** — the legal page was re-read directly at
  <https://www.nasdaq.com/legal> (agreement text "Last Updated: May 11, 2026").
  Section 2 (*Your Responsibilities to Use the Services*) requires users, among
  other things, to "Not access or use the Service, or any process, whether
  automated or manual, to capture data or content from the Service or
  circumvent any mechanisms for preventing the unauthorized reproduction or
  distribution of the Service for any reason." That is a direct, current-text
  prohibition of automated capture, and it re-confirms the BLOCKED status
  below. The page also carries a binding-arbitration notice (Section 20).
* **Stooq** — the previously-recorded terms URL (`stooq.com/term.php`) no
  longer resolves ("The page you requested does not exist"). The footer of
  both the homepage and the help centre now points at
  <https://stooq.com/terms.html>; that page did not render usable terms text
  through the fetch tool on 2026-09-19, so **no redistribution grant could be
  located**, and the SECONDARY / UNVERIFIED-TERMS status below is unchanged.
  Stooq remains unused as a source in this repository.

## Decision table

| Candidate | Official? | Key-free free tier? | Documented usage rights found? | Status / use |
|---|---|---|---|---|
| U.S. Treasury auction results + par yield curve (TreasuryDirect / FiscalData API) | Yes (US Treasury) | Yes | Yes — public fiscal data on an official .gov API | **APPROVED-FOR-USE** — this is the feed behind the Official Auction Book (`memory/official/`), currently the only book on the site printed on publisher prices ([api docs](https://fiscaldata.treasury.gov/api-documentation/)) |
| FRED daily index series (SP500, DJIA, NASDAQCOM, VIXCLS, SOFR) | Yes (Federal Reserve Bank of St. Louis, official Fed publication; data from named providers) | Yes | Yes — FRED asks for citation; used here as anchors, never as tradable securities | **APPROVED-FOR-ANCHORING** — indexes are not orderable instruments ([FRED SP500](https://fred.stlouisfed.org/series/SP500)) |
| NY Fed markets API (SOFR/secured rates) | Yes | Yes | Yes | **APPROVED-FOR-ANCHORING** (financing leg) ([markets API](https://markets.newyorkfed.org/)) |
| SEC EDGAR (Form 4, 8-K, submissions API) | Yes (SEC) | Yes (fair-access, declared User-Agent) | Yes | **APPROVED-FOR-SIGNALS** — no prices in it, so it can drive intents, not fills ([access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)) |
| openFDA (FDA decisions) | Yes (FDA) | Yes | Yes (public API terms) | **APPROVED-FOR-SIGNALS** ([openFDA](https://open.fda.gov/apis/)) |
| MLB StatsAPI | Yes (league) | Yes | Public scoreboard API; no trading rights needed for signal use | **APPROVED-FOR-SIGNALS** ([schedule endpoint](https://statsapi.mlb.com/api/v1/schedule)) |
| api.weather.gov / NCEI (weather) | Yes (NOAA/NWS) | Yes | Yes | **APPROVED-FOR-SIGNALS** |
| Nasdaq public historical/quotes endpoints | Yes (exchange operator) | Yes | **No** — fetched 2026-09-18: the site's legal terms prohibit unauthorized capture/reproduction | **BLOCKED: NOT_AUTHORIZED_BY_TERMS** — collector records research candidates only ([Nasdaq legal](https://www.nasdaq.com/legal)) |
| NYSE market-data products | Yes | No | Paid licensing | **OUT OF SCOPE** (not free) |
| CTA/UTP SIP consolidated tape | Yes (the official authoritative source) | No | Licensed products | **OUT OF SCOPE** — this is ultimately what is required for an approved NBBO feed ([CTA plan](https://www.ctaplan.com/)) |
| Yahoo Finance daily bars | No (aggregator) | Yes | No redistribution grant established | **SECONDARY research inputs only**, always labelled ([Yahoo chart endpoint](https://query1.finance.yahoo.com/v8/finance/chart/SPY)) |
| Stooq daily CSV | No (aggregator/vendor) | Yes | No machine-redistribution terms located during review | **SECONDARY / UNVERIFIED TERMS** — not used as a source in this repo ([CSV](https://stooq.com/q/d/l/?s=spy.us&i=d)) |
| Alpha Vantage, Polygon, Finnhub, Tiingo, Marketstack | No (vendors) | Freemium **API-key** tiers | Key-gated | **EXCLUDED BY THE BRIEF** — "no freemium or trial API keys" |
| Alpaca market-data | Broker product | Key required (account) | Account ToS | **EXCLUDED BY THE BRIEF** (key-based; repo also logged a docs-404 on its pricing page) |
| IEX Cloud | — | — | — | **DISCONTINUED** (service shut down 2024) — retained as a named dead end so the list is repeatable |
| Kalshi public API | Yes (venue itself) | Yes | Public API | OFFICIAL for prediction-market prices, **not equities**; captured fields came back null (IR-41) |
| FINRA RegSHO daily short volume | Yes (FINRA) | Yes | Yes, public files | **APPROVED-FOR-SIGNALS** (already collected: `data/real/finra/`) |

## Conclusion

1. The honest answer to "obtain an approved official quote/trade feed" for
   **individual equities** remains: none of the free, key-less, documented
   sources satisfies all three tests at once. The SIP tape is official but
   licensed; the free public endpoints lack documented redistribution rights;
   the vendor feeds break the no-freemium-keys rule.
2. The project therefore keeps two lanes:
   * **official execution**: the U.S. Treasury auction book, where the
     publisher itself prints the price (this satisfies "forward book with
     simulated settled trades on real verified official pricing and dates");
   * **strict equity pilot**: timestamped paper orders are journaled and
     reconciled (`memory/pilot/`, scheduled by `equity-pilot.yml`), and fills
     stay at zero until a licensed/redistributable equity feed is recorded.
3. Any equity "return" published before that feed exists would be a research
   composite, and this repository does not present research composites as
   competition results.

## Next concrete step (owner action)

Contact CTA / UTQP or an exchange data-sales desk for a quote on the smallest
official license that permits repository reproduction of end-of-day NBBO
snapshots *with quote sizes*; the licensing checklist is the P0 item in
`research/REMAINING_WORK.json`. Everything else — calendars, halts, fees,
scheduler, hash-chained journal, market-making gate — is already running.
