"""Run memory: the append-only store that makes every season replayable.

Requirement this module exists to satisfy: *all* competition data is stored as
memory for future testing, analysis and evaluation.  Concretely that means
nothing is discarded after the leaderboard is produced - every order, every
fill, every rejection, every daily mark, every position snapshot and every
carry payment is written to disk in a self-describing, versioned layout that
can be re-opened years later without the code that produced it.

Layout::

    memory/
      manifest.json                     # store-level index of runs + schema version
      runs/<run_id>/
        manifest.json                   # config, seed, git sha, code hashes, file checksums
        market_data.json                # the exact OHLCV / SPX / VIX series traded on
        participants.json               # strategy specs + usernames + rosters
        market_report.json              # what the market did (real factors)
        factor_report.json              # realised factor premiums
        leaderboard.json                # ranked table + robustness panel
        irregularities.json             # IR-xx flags raised during this run
        reports/<username>.json         # full per-participant analytics + narrative
        events/orders.jsonl.gz          # every order submitted
        events/fills.jsonl.gz           # every fill (incl. partials/expiries)
        events/rejections.jsonl.gz      # every rejected/expired order + reason
        events/equity.jsonl.gz          # daily equity/margin rows
        events/positions.jsonl.gz       # daily position snapshots
        events/carry.jsonl.gz           # dividends and borrow fees
        events/margin.jsonl.gz          # maintenance breaches + forced liquidations
        events/quotes.jsonl.gz          # opening touch quote per symbol per session

Event logs are gzip-compressed JSON Lines: append-only, streaming-readable,
and small enough to keep in git.  ``MemoryStore.query_*`` decompresses on the
fly and filters lazily, so a 100k-fill season can be queried without loading
it all into RAM.

Integrity: every written file gets a SHA-256 in the run manifest, and the
manifest itself records the git commit and the hash of each ``sim/*.py`` file.
A future analysis can therefore prove which code produced which numbers.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "1.0.0"
DEFAULT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "memory")

EVENT_LOGS = ("orders", "fills", "rejections", "equity", "positions",
              "carry", "margin", "quotes")


# ==========================================================================
# Helpers
# ==========================================================================

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_state(repo_root: str) -> dict:
    """Best-effort git provenance. Never raises: memory must always be writable."""
    def run(*args: str) -> Optional[str]:
        try:
            out = subprocess.run(["git", *args], cwd=repo_root, capture_output=True,
                                 text=True, timeout=10)
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None
    return {
        "commit": run("rev-parse", "HEAD"),
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": (run("status", "--porcelain") or "") != "",
        "remote": run("config", "--get", "remote.origin.url"),
    }


def _code_hashes(repo_root: str) -> Dict[str, str]:
    sim_dir = os.path.join(repo_root, "sim")
    out: Dict[str, str] = {}
    if not os.path.isdir(sim_dir):
        return out
    for name in sorted(os.listdir(sim_dir)):
        if name.endswith(".py"):
            path = os.path.join(sim_dir, name)
            out[f"sim/{name}"] = _sha256(path)
    return out


def _jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses / tuples / sets into plain JSON types."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "to_row") and callable(obj.to_row):
        return _jsonable(obj.to_row())
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        return _jsonable(vars(obj))
    if isinstance(obj, Decimal):
        return _jsonable(float(obj))
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None                      # JSON has no NaN/Inf
        return obj
    return obj


# ==========================================================================
# Writers
# ==========================================================================

class EventLogWriter:
    """Buffered, gzip-compressed JSON Lines writer for one event stream."""

    def __init__(self, path: str, compress: bool = True) -> None:
        self.path = path + (".gz" if compress else "")
        self.compress = compress
        self.rows = 0
        self.bytes = 0
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # NB: this used to be a single conditional expression inside one lambda,
        # which parsed as `lambda p: (gzip.open(...) if compress else lambda p:
        # open(...))` - so the uncompressed branch handed back a *function*
        # instead of a file handle. Kept as two explicit branches.
        if compress:
            def opener(p: str):
                return gzip.open(p, "wt", encoding="utf-8", compresslevel=6)
        else:
            def opener(p: str):
                return open(p, "w", encoding="utf-8")
        self._fh = opener(self.path)

    def write(self, row: dict) -> None:
        line = json.dumps(_jsonable(row), separators=(",", ":"), sort_keys=False)
        self._fh.write(line + "\n")
        self.rows += 1
        self.bytes += len(line) + 1

    def write_many(self, rows: Iterable[dict]) -> None:
        for r in rows:
            self.write(r)

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "EventLogWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class RunWriter:
    """Streams one competition run into ``memory/runs/<run_id>/``."""

    def __init__(self, run_id: str, root: str = DEFAULT_ROOT,
                 compress: bool = True) -> None:
        self.run_id = run_id
        self.root = root
        self.dir = os.path.join(root, "runs", run_id)
        self.events_dir = os.path.join(self.dir, "events")
        self.reports_dir = os.path.join(self.dir, "reports")
        os.makedirs(self.events_dir, exist_ok=True)
        os.makedirs(self.reports_dir, exist_ok=True)
        self.compress = compress
        self._logs: Dict[str, EventLogWriter] = {}
        self._checksums: Dict[str, Any] = {}
        self.created_utc = _utcnow()

    # -- event streams ----------------------------------------------------
    def log(self, stream: str) -> EventLogWriter:
        if stream not in EVENT_LOGS:
            raise ValueError(f"unknown event stream {stream!r}")
        if stream not in self._logs:
            self._logs[stream] = EventLogWriter(
                os.path.join(self.events_dir, f"{stream}.jsonl"), self.compress)
        return self._logs[stream]

    def append(self, stream: str, row: dict) -> None:
        self.log(stream).write(row)

    def extend(self, stream: str, rows: Iterable[dict]) -> None:
        self.log(stream).write_many(rows)

    # -- documents --------------------------------------------------------
    def write_json(self, name: str, payload: Any, subdir: str = "") -> str:
        target = os.path.join(self.dir, subdir, name) if subdir else \
            os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(_jsonable(payload), fh, indent=1, sort_keys=False)
            fh.write("\n")
        rel = os.path.relpath(target, self.dir)
        self._checksums[rel] = {"sha256": _sha256(target),
                                "bytes": os.path.getsize(target), "kind": "json"}
        return target

    def write_report(self, username: str, report: dict) -> str:
        safe = username.lstrip("@").replace("/", "_")
        return self.write_json(f"{safe}.json", report, subdir="reports")

    # -- finalisation -----------------------------------------------------
    def close(self) -> None:
        for log in self._logs.values():
            log.close()
        for name, log in sorted(self._logs.items()):
            rel = os.path.relpath(log.path, self.dir)
            self._checksums[rel] = {
                "sha256": _sha256(log.path), "bytes": os.path.getsize(log.path),
                "kind": "jsonl.gz" if self.compress else "jsonl", "rows": log.rows,
            }
        self._logs.clear()

    def finalise(self, manifest_payload: dict) -> dict:
        """Close streams, checksum everything, write the run + store manifests."""
        self.close()
        repo_root = os.path.dirname(self.root)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "created_utc": self.created_utc,
            "finalised_utc": _utcnow(),
            "code": {"git": _git_state(repo_root),
                     "python_module_hashes": _code_hashes(repo_root)},
            "files": self._checksums,
        }
        manifest.update(manifest_payload)
        self.write_json("manifest.json", manifest)
        _update_store_manifest(self.root, manifest)
        return manifest


def _update_store_manifest(root: str, run_manifest: dict) -> None:
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "manifest.json")
    store: Dict[str, Any] = {"schema_version": SCHEMA_VERSION, "runs": []}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                store = json.load(fh)
        except Exception:
            pass
    entry = {
        "run_id": run_manifest["run_id"],
        "created_utc": run_manifest.get("created_utc"),
        "seed": run_manifest.get("seed"),
        "competition": run_manifest.get("competition", {}),
        "participants": run_manifest.get("participant_count"),
        "sessions": run_manifest.get("session_count"),
        "winner": run_manifest.get("winner"),
        "files": len(run_manifest.get("files", {})),
    }
    runs = [r for r in store.get("runs", []) if r.get("run_id") != entry["run_id"]]
    runs.append(entry)
    runs.sort(key=lambda r: r.get("created_utc") or "")
    store["runs"] = runs
    store["latest_run_id"] = entry["run_id"]
    store["updated_utc"] = _utcnow()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=1)
        fh.write("\n")


# ==========================================================================
# Readers / query API
# ==========================================================================

def _read_rows(path: str) -> Iterator[dict]:
    if not os.path.exists(path):
        return
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


@dataclass
class Query:
    """Lazy, SINGLE-PASS filter over an event stream.

    ``rows`` is an iterator, so each terminal operation (``list``, ``count``,
    ``sum``, ``sorted_by``, ``select``) consumes it. Ask ``MemoryStore`` for a
    fresh query for a second pass: a full season of fills (~38k rows across six
    scenarios) must not be materialised in RAM just to be filtered twice.
    """
    rows: Iterator[dict]

    def where(self, **kwargs) -> "Query":
        def keep(row: dict) -> bool:
            for k, v in kwargs.items():
                if isinstance(v, (list, tuple, set)):
                    if row.get(k) not in v:
                        return False
                elif callable(v):
                    if not v(row.get(k)):
                        return False
                elif row.get(k) != v:
                    return False
            return True
        return Query(r for r in self.rows if keep(r))

    def between(self, field: str, lo: Optional[str] = None,
                hi: Optional[str] = None) -> "Query":
        return Query(r for r in self.rows
                     if (lo is None or str(r.get(field, "")) >= lo)
                     and (hi is None or str(r.get(field, "")) <= hi))

    def select(self, *fields: str) -> Iterator[dict]:
        for r in self.rows:
            yield {f: r.get(f) for f in fields}

    def sorted_by(self, field: str, reverse: bool = False) -> List[dict]:
        return sorted(self.rows, key=lambda r: r.get(field, 0), reverse=reverse)

    def list(self) -> List[dict]:
        return list(self.rows)

    def count(self) -> int:
        return sum(1 for _ in self.rows)

    def sum(self, field: str) -> float:
        return sum(float(r.get(field) or 0.0) for r in self.rows)


class MemoryStore:
    """Read side of the memory: open runs, query streams, build panels."""

    def __init__(self, root: str = DEFAULT_ROOT) -> None:
        self.root = root

    # -- index ------------------------------------------------------------
    def runs(self) -> List[dict]:
        path = os.path.join(self.root, "manifest.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("runs", [])

    def latest_run_id(self) -> Optional[str]:
        path = os.path.join(self.root, "manifest.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("latest_run_id")

    def run_dir(self, run_id: str) -> str:
        return os.path.join(self.root, "runs", run_id)

    def load(self, run_id: str, name: str) -> Any:
        path = os.path.join(self.run_dir(run_id), name)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def report(self, run_id: str, username: str) -> Any:
        safe = username.lstrip("@").replace("/", "_")
        return self.load(run_id, f"reports/{safe}.json")

    def reports(self, run_id: str) -> List[dict]:
        d = os.path.join(self.run_dir(run_id), "reports")
        if not os.path.isdir(d):
            return []
        out = []
        for name in sorted(os.listdir(d)):
            if name.endswith(".json"):
                with open(os.path.join(d, name), encoding="utf-8") as fh:
                    out.append(json.load(fh))
        return out

    def stream(self, run_id: str, name: str) -> Query:
        base = os.path.join(self.run_dir(run_id), "events", f"{name}.jsonl")
        for cand in (base + ".gz", base):
            if os.path.exists(cand):
                return Query(_read_rows(cand))
        return Query(iter(()))

    def query_fills(self, run_id: str, username: Optional[str] = None,
                    symbol: Optional[str] = None, side: Optional[str] = None,
                    start: Optional[str] = None, end: Optional[str] = None) -> Query:
        q = self.stream(run_id, "fills")
        if username:
            q = q.where(participant=username)
        if symbol:
            q = q.where(symbol=symbol)
        if side:
            q = q.where(side=side)
        return q.between("date", start, end)

    def query_orders(self, run_id: str, **kw) -> Query:
        q = self.stream(run_id, "orders")
        return q.where(**kw) if kw else q

    # -- panels for future analysis ---------------------------------------
    def equity_panel(self, run_id: str) -> Dict[str, Dict[str, float]]:
        """{date: {username: equity}} - the panel future backtests need."""
        panel: Dict[str, Dict[str, float]] = {}
        for row in self.stream(run_id, "equity").rows:
            panel.setdefault(row["date"], {})[row["participant"]] = row["equity"]
        return panel

    def returns_panel(self, run_id: str) -> Tuple[List[str], List[str],
                                                   Dict[str, List[float]]]:
        """Dates, usernames and the aligned daily-return matrix."""
        panel = self.equity_panel(run_id)
        dates = sorted(panel)
        users = sorted({u for d in panel.values() for u in d})
        series = {u: [] for u in users}
        prev = {u: None for u in users}
        for d in dates[1:]:
            for u in users:
                eq = panel[d].get(u)
                p = prev[u]
                series[u].append(eq / p - 1.0 if (eq is not None and p) else 0.0)
                prev[u] = eq if eq is not None else p
        return dates[1:], users, series

    def correlation_matrix(self, run_id: str) -> Dict[str, Dict[str, float]]:
        _, users, series = self.returns_panel(run_id)
        out: Dict[str, Dict[str, float]] = {}
        for a in users:
            out[a] = {}
            for b in users:
                out[a][b] = round(_corr(series[a], series[b]), 4)
        return out

    def fill_stats(self, run_id: str) -> dict:
        n = tot = 0
        by_status: Dict[str, int] = {}
        by_symbol: Dict[str, float] = {}
        for f in self.stream(run_id, "fills").rows:
            n += 1
            tot += f.get("total_cost", 0.0) or 0.0
            by_status[f.get("status", "?")] = by_status.get(f.get("status", "?"), 0) + 1
            by_symbol[f["symbol"]] = by_symbol.get(f["symbol"], 0.0) + \
                (f.get("notional", 0.0) or 0.0)
        return {"fills": n, "total_execution_cost_usd": round(tot, 2),
                "by_status": by_status,
                "notional_by_symbol_usd": {k: round(v, 2) for k, v in
                                           sorted(by_symbol.items(),
                                                  key=lambda kv: -kv[1])}}

    def export_csv(self, run_id: str, stream_name: str, dest: str,
                   columns: Optional[Sequence[str]] = None) -> int:
        """Flatten one event stream to CSV for spreadsheet / R / pandas use."""
        rows = self.stream(run_id, stream_name).list()
        if not rows:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write("")
            return 0
        cols = list(columns) if columns else sorted({k for r in rows for k in r})
        import csv
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                w.writerow([_flat(r.get(c)) for c in cols])
        return len(rows)

    def verify(self, run_id: str) -> dict:
        """Re-hash every file against the run manifest. Detects silent rot."""
        man = self.load(run_id, "manifest.json") or {}
        files = man.get("files", {})
        bad, missing, ok = [], [], 0
        for rel, meta in files.items():
            path = os.path.join(self.run_dir(run_id), rel)
            if not os.path.exists(path):
                missing.append(rel)
                continue
            if _sha256(path) == meta.get("sha256"):
                ok += 1
            else:
                bad.append(rel)
        return {"run_id": run_id, "files_checked": len(files), "ok": ok,
                "mismatched": bad, "missing": missing,
                "intact": not bad and not missing}


def _flat(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"))
    return v


def _corr(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    xs, ys = a[:n], b[:n]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if not sxx or not syy:
        return 0.0
    return sxy / math.sqrt(sxx * syy)
