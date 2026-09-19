# Strict US equities memory

No official quote feed is approved and no strict competition order, fill or
settlement has been recorded. The legacy books are not imported into this book.

`sim.strict_equities.PaperLedger` is the tested storage/execution **library**, not
a running market service. It uses SQLite (transactions, append-only event table,
SHA-256 chain) and can export deterministic JSON Lines. Decimal currency fields
are strings; integer quantities are whole shares. Each fill references the exact
quote, approved receipt hash, order ID, strategy version, source and settlement
calendar. Executions and settlements are always labelled simulated.

Do not commit runtime `.sqlite`, `-wal` or `-shm` files. A future service must
store its journal on durable storage, archive content-addressed exports and
retain their hashes in versioned manifests. Use private/object storage for
licensed market data; publish only what the agreement permits. Retain all
strategy versions and one-year competition namespaces. External timestamp
attestation and backups are needed: a local hash chain alone cannot prove that
an entire history was not rewritten.

The static desk's `snapshot.json` and `trade-audit.csv` currently preserve the
independent exclusion audit of the legacy book and the strategy registry.
The scheduled audit uploads a review artifact; it neither collects quotes nor
places orders. GitHub Actions artifacts expire and are **not** the permanent
competition archive.
