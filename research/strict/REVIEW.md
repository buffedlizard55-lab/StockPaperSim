# Three-pass review — 2026-09-19

## Scope and conclusion

This checkout already contained roughly 35,000 Python lines across the engine
and scripts, legacy seasons, published data and several distinct books. It was
not an empty website. The initial 540-test suite passed. Passing those tests did
**not** make its fills official, its modelled depth observed, or its date-only
intents independently timestamped.

This release is an evidence-first desk and tested execution/storage foundation,
**not a completed live official-price stock competition**. No source available
in this checkout establishes all of official execution pricing, intraday
bid/ask size, pre-trade submission time and securities-settlement dates. Zero
strict fills is the correct result. We have not claimed to independently verify
every line of the entire legacy repository or every remote dataset.

The new files and changed paths were reviewed directly. The generated audit
checks **every one of the latest forward book's 27 fills**, all 35 intents (8
still outstanding), all 18 trip records, and inventories every legacy run
manifest. It checks forward stream custody, records each fill's price-file
checksum match/mismatch, and excludes the whole legacy execution model from
strict ranking. See `docs/desk/snapshot.json` for actual rows, not prose counts.
Existing independent accounting auditors were also run; their passing results
are arithmetic/custody evidence, not market-data certification.

## Pass 1 — implement and verify

- Reviewed MasterSite and rendered excerpts from every named project that had
  a corresponding page. Checked all 42 public repository names through the
  official GitHub API; no repository named CEO appeared. That does not rule out
  a feature with that name inside another project.
- Read the public TradingView Leap, Trade Ideas and Candlecharts contest pages.
  Borrowed information architecture (usernames, orders, open/closed P&L,
  leaderboard, journals). Did not inspect or copy a private matching algorithm.
- Registered 51 explicit hypotheses: 18 signal-code prototypes across five
  arithmetic families and 33 design-only variants. All have unique usernames,
  entry, exit, sizing, parameters, failure modes and source links. They are
  unproven designs, not 51 independent statistical discoveries or profitable
  strategies. No new official backtest has been run.
- Added a separate responsive GitHub Pages desk; root URL now opens it. Legacy
  results remain linked and are not silently rewritten or re-ranked.
- Implemented a SQLite cash-account paper ledger: append-only events, hash
  chain, order IDs, partial fills, cancellations, shared displayed capacity,
  FIFO cost, declared fees, simulation settlement, fresh bid marks and exports.
- Added official-source/receipt/permission gates. Production feed registry is
  empty. The fixtures are clearly synthetic and stay in tests.

## Pass 2 — bugs, assumptions and edge cases

Found and fixed:

1. New site section invalidated hard-coded legacy navigation/page counts.
   Updated section-specific assertions; retained link, markup and rebuild checks.
2. Search matched incidental `fda` characters inside SHA-256 strings. Gave cards
   explicit searchable fields excluding evidence hashes; browser regression
   now returns the five actual FDA variants.
3. Separate SQLite writers could race when appending an order/hash link. All
   appends now run under `BEGIN IMMEDIATE`; execution updates are atomic.
4. Repeated quote IDs and multi-user fills could exaggerate capacity. The
   shared cap debits all participants. New snapshot IDs alone no longer count
   as replenished liquidity at the same price within the session. This is a
   conservative prototype, not full order-book reconstruction.
5. Blocked evidence checks were not retained in the journal. Valid-clock gate
   failures now append a BLOCKED event; malformed/rewound clocks are rejected.
6. Missing/stale marks could be mistaken for measured performance. Equity and
   return are unknown without valid fresh marks; no-trade accounts are unranked.
7. CSV usernames could be interpreted as formulas in spreadsheets. Export
   escapes formula-leading strings; JSON retains the original identifiers.
8. Legacy browser simulator called hard-coded example quotes “Live.” Prominent
   copy now labels scenario prices/manual input and browser-local storage; the
   calculator cannot submit strict trades.
9. Legacy Form 4 prose overstated code P. SEC Instruction 8 says “open market
   or private purchase,” and covers derivative as well as non-derivative
   securities. Corrected the register; new design requires footnote review and
   non-derivative identification. A filing transaction price is not a new quote.
10. Technical prototype arithmetic now uses Decimal rather than converting
    validated finite inputs to potentially overflowing floats.
11. Final pass: malformed/unreadable receipts now fail with logged evidence
    errors, and mark updates are serialized so out-of-order quotes cannot
    overwrite a newer valuation. Added regression tests for both.

## Pass 3 — requirements reconciliation

| Requirement | Delivered / actual status |
|---|---|
| Research all named projects | Discovery review and 13 project records with primary-review links; no assumption their outputs are official prices |
| Broad strategy list | 51 versioned hypotheses, 18 implemented signal prototypes; 33 need event/queue adapters |
| Official-price backtests | **Blocked**: no complete eligible price/corporate-action/point-in-time dataset; no fabricated history |
| Forward orders and settled stock book | **Library implemented, live integration blocked**; zero strict orders/fills/settlements |
| Upcoming trades | All 8 legacy outstanding candidates visible as unapproved, not submitted or newly generated recommendations |
| Real-time liquidity / market making | No live feed. Conservative quote-size engine tested; passive fills require queue data and are not invented |
| Entries, exits, P&L and every strategy | Strict FIFO/fees/account queries tested; legacy rows and all run manifest links remain reviewable; no strict P&L claim |
| Highest returns / one-year competitions | $100,000, 2026-09-21 to 2027-09-21 exclusive, total-return ranking; no forced invented liquidation at end |
| Memory and future analysis | Append-only transactional journal + JSONL export library; raw quotes/rights and durable service archive still need integration |
| No manual prices / automatic operation | New desk takes no price input. Scheduled audit is read-only and automatic. Real-time collectors/order scheduler not connected |
| Official links and irregularities | 22 source/discovery records, row blockers, this report; X/Facebook inaccessible discovery is disclosed |
| Clean GitHub Pages site | New responsive desk, searchable cards/journal, CSV/JSON downloads, legacy archive navigation; existing root Pages config preserved |
| Social strategy research | Reddit excerpt and YouTube metadata discovery only; video not watched, X/Facebook no usable first-party post; no authenticated returns imported |

### Verification performed

- Standard-library purity check and JavaScript syntax check.
- Full unittest suite: 578 tests, including 38 strict-ledger/research/desk tests.
  The README count is also enforced by a test.
- Independent official-auction audit: 1,870 checks, zero failures.
- Independent published-season audit: 1,146 checks, zero failures.
- Separate Season 2 data/custody auditor: passed.
- Official extract verification: three files, two cross-checks, zero failures.
- Fresh site rebuild compared recursively with committed `docs/`.
- Headless Chromium: desktop 1440×1000, mobile 390×844; search, status filter,
  reset, empty state, navigation, CSV/JSON responses, no console errors or mobile
  document overflow. Test tooling is optional and not an app dependency.
- Existing Pages configuration read through GitHub API: `main`, repository root,
  status `built`. The merge/deployment outcome is checked separately at release.

### Official facts vs. design assumptions

- SEC's T+1 bulletin describes the standard for covered transactions from May
  28, 2024, subject to exceptions. It is not a daily settlement confirmation.
  [2](https://www.sec.gov/resources-for-investors/investor-alerts-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin)
- Nasdaq TotalView describes depth on the Nasdaq market, including securities
  listed elsewhere. It is not a guarantee of all-venue queue priority or of a
  hypothetical participant's fill.
  [2](https://www.nasdaqtrader.com/Trader.aspx?id=TotalViewNonPro)
- NYSE core hours and listed early closes were checked at
  <https://www.nyse.com/trade/hours-calendars>. The ledger requires explicit
  reviewed trading **and** settlement records; it never guesses from weekdays.
- SEC Form 4 Instruction 8 was checked directly at
  <https://www.sec.gov/files/form4.pdf>.
- Order fees of $0.003/share in the new library are a **declared simulator
  default**, not verified regulatory fees or a broker promise. The static desk
  says this. Official broker/venue fee schedules remain a release prerequisite.
- The five-second quote freshness threshold, long-only cash-account restriction,
  conservative capacity cap and end-of-settlement-day processing rule are
  **design decisions**, not assertions about exchange rules or actual fills.

## Irregularities and next-session priorities

**P0 — official feed and permission:** verify a suitable exchange/SIP agreement,
quote sizes/units/conditions, raw retention and permissible public exports.
Neither a Nasdaq public historical page nor a vendor price cross-check supplies
missing NBBO/depth or redistribution permission. No paid access or permission
was presumed or purchased.

**P0 — independent data ingestion:** implement a provider-specific authenticated
collector and raw-to-normalized validation, point-in-time instrument master,
corporate actions, delistings, halts and separate settlement calendar. The
library's receipt schema is not a provider adapter. Approval records and
`https://` URLs alone do not authenticate caller-supplied facts.

**P0 — live service / attested intents:** persistent worker, scheduler, receipt
store, immutable version pinning, clock synchronization and external timestamp
attestation. Publish a safe snapshot to Pages. GitHub Pages cannot run this
worker or store secret keys. Actions schedules are not a real-time execution
service and their temporary artifacts are not permanent annual memory.

**P1 — reconcile pilot:** connect the 18 prototypes, validate official fees,
reconcile holdings/cash and rejected attempts, then enable strictly labelled
paper performance. Add an independent ledger arithmetic auditor. Preserve the
competition close as unvalued if an eligible exit quote is unavailable.

**P1 — research depth:** build event adapters for the other 33 designs; archive
all tested variants including failures, freeze in/out-of-sample splits and
address multiple comparisons. Sports/weather/FDA-sector mappings remain
hypotheses. Do not claim causality from a single profitable path.

**P1 — microstructure:** replace the conservative cap with licensed order-level
replay, queue priority, cancel/replace latency and corrections. Add borrow and
financing only with evidence. Until then do not advertise a full market maker.

Other review findings: PinePilot's rendered introduction and embedded script
show different mode/version counts; exact replication needs a pinned script.
NBA injury page reports no current official PDF and explicitly uses secondary
layers. GOLD is a jewellery directory, not a gold quote feed. The reviewed
Alpaca documentation URL `/docs/stock-pricing-data` redirected to a 404; it was
not used as evidence or as an activated adapter. Direct HTTPS from this sandbox
failed for the MasterSite request; page tools and GitHub API were used instead.
