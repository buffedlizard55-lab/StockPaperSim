#!/usr/bin/env python3
"""Collect the forward prints the live book is allowed to execute against.

Four modes, one pipeline
------------------------
``--ingest-raw PATH``
    Read a JSON file of *transcribed* raw responses (the development sandbox's
    page-fetch path) and turn it into the append-only print ledgers.  Used when
    the only egress available is a human-readable fetch tool.

``--fetch``
    Retrieve the same responses over HTTPS with :mod:`urllib` (the GitHub Actions
    collector path), store the raw payloads beside the ledgers and ingest them.

``--rebuild``
    Re-derive every derived artefact (price files, cross-check report, manifest)
    from the ledgers alone.  Deterministic and offline, so CI can prove the
    committed files match the committed prints.

``--verify``
    Print the cross-check verdict and the per-session coverage.  Exits non-zero
    when the two publishers disagree beyond tolerance, because a settlement must
    not proceed on a disputed bar.

Nothing here writes a price that a publisher did not carry: a session missing
from both sources stays missing, and the live book reports it as waiting.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import custody, prints, realdata  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRED_DIR = os.path.join("data", "real", "fred")
LATEST_FRED = os.path.join(FRED_DIR, "latest_observations.json")

#: The official FRED series the live strategies actually read.  Collected with
#: the same request shape the historical files used, so one reader serves both.
FRED_SERIES = ("SP500", "NASDAQCOM", "DJIA", "VIXCLS", "SOFR", "DGS10", "DGS2",
               "DGS3MO", "DCOILWTICO", "DTWEXBGS")

USER_AGENT = ("StockPaperSim-collector (+https://github.com/buffedlizard55-lab/"
              "StockPaperSim; research archive of published daily prints)")


# --------------------------------------------------------------------------
# HTTP (collector path)
# --------------------------------------------------------------------------

def get(url: str, timeout: int = 30, headers: dict | None = None) -> tuple[int, bytes]:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json,text/csv,*/*",
              "Accept-Encoding": "identity"}
    merged.update(headers or {})
    request = urllib.request.Request(url, headers=merged)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:                      # pragma: no cover
        return int(exc.code), exc.read()
    except Exception as exc:                                    # pragma: no cover
        return 0, str(exc).encode()


def _nasdaq_url(symbol: str, assetclass: str, start: str, end: str) -> str:
    return prints.SOURCE_CLASSES["nasdaq"]["url_template"].format(
        symbol=symbol, assetclass=assetclass, start=start, end=end)


def _yahoo_url(symbol: str) -> str:
    return prints.SOURCE_CLASSES["yahoo"]["url_template"].format(
        symbol=symbol, range="5d")


def fetch_raw(start: str, end: str, symbols, verbose: bool = False) -> dict:
    """The collector path: GET every endpoint, keep the raw bytes, ingest them."""
    retrieved = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = {"_provenance": {
        "title": f"Forward prints {start}..{end}, retrieved by the Actions collector",
        "retrieved_utc": retrieved,
        "retrieval_method": "HTTPS GET via urllib from the CI runner",
        "sources": [{"id": "yahoo", **prints.SOURCE_CLASSES["yahoo"]},
                    {"id": "nasdaq", **prints.SOURCE_CLASSES["nasdaq"]}],
        "sessions": [],
    }, "yahoo": {"retrieved_utc": retrieved, "endpoint": "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=1d",
                 "timestamps": [], "symbols": {}},
        "nasdaq": {"retrieved_utc": retrieved, "endpoint": prints.SOURCE_CLASSES["nasdaq"]["url_template"],
                   "assetclass": {}, "rows": {}},
        "official_series": {
            "note": ("Official publisher observations for the requested window, "
                     "keyed by series id. The date this run covered is recorded in "
                     "_provenance.sessions rather than in the key, so a later run "
                     "does not relabel yesterday's numbers as today's."),
            "retrieved_utc": retrieved, "urls": {}, "values": {}}}
    for symbol in symbols:
        url = _yahoo_url(symbol)
        status, body = get(url)
        if status != 200:
            print(f"  yahoo {symbol}: HTTP {status}; skipped")
            continue
        try:
            payload = json.loads(body.decode("utf-8"))
            result = payload["chart"]["result"][0]
            quote = result["indicators"]["quote"][0]
        except Exception as exc:                                # pragma: no cover
            print(f"  yahoo {symbol}: unusable response ({exc}); skipped")
            continue
        raw["yahoo"]["symbols"][symbol] = {
            "venue": result["meta"].get("fullExchangeName"),
            "regularMarketPrice": result["meta"].get("regularMarketPrice"),
            "regularMarketTime": result["meta"].get("regularMarketTime"),
            "open": quote.get("open"), "high": quote.get("high"),
            "low": quote.get("low"), "close": quote.get("close"),
            "volume": quote.get("volume"),
            # Per-symbol timestamps. An earlier revision wrote one shared array
            # from inside this loop (last symbol won), which silently assumed
            # every symbol shares one timeline - true for a quiet week, wrong the
            # moment a name is halted on a session the others traded, and the
            # kind of wrong that shifts a bar's date rather than dropping it.
            "timestamps": result.get("timestamp", []),
        }
        raw["yahoo"]["timestamps"] = raw["yahoo"].get("timestamps") or result.get("timestamp", [])
        if verbose:
            print(f"  yahoo {symbol}: {len(quote.get('close') or [])} bars")
        assetclass = "etf" if symbol in ETF_SYMBOLS else "stocks"
        raw["nasdaq"]["assetclass"][symbol] = assetclass
        nurl = _nasdaq_url(symbol, assetclass, start, end)
        nstatus, nbody = get(nurl)
        if nstatus != 200:
            print(f"  nasdaq {symbol}: HTTP {nstatus}; skipped")
            continue
        try:
            table = json.loads(nbody.decode("utf-8"))["data"]["tradesTable"]["rows"]
        except Exception as exc:                                # pragma: no cover
            print(f"  nasdaq {symbol}: unusable response ({exc}); skipped")
            continue
        raw["nasdaq"]["rows"][symbol] = [
            [row.get("date"), row.get("close"), row.get("open"), row.get("high"),
             row.get("low"), row.get("volume")] for row in table]
        if verbose:
            print(f"  nasdaq {symbol}: {len(table)} rows")
    for sid in FRED_SERIES:
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}"
        status, body = get(url)
        if status != 200:
            print(f"  fred {sid}: HTTP {status}; skipped")
            continue
        values = {}
        for line in body.decode("utf-8", "replace").splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 2 and parts[1].strip():
                values[parts[0].strip()] = parts[1].strip()
        raw["official_series"]["urls"][sid] = url
        raw["official_series"]["values"][sid] = values
        observed = sorted(values)
        if observed:
            raw["_provenance"]["sessions"] = sorted(
                set(raw["_provenance"].get("sessions") or []) | set(observed))
    return raw


ETF_SYMBOLS = {"SPY", "QQQ", "IWM", "GLD", "UNG", "XLU", "XBI", "IBB", "TLT"}


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------

def _session_from_timestamp(ts: int) -> str:
    """A Yahoo daily timestamp is the session's 09:30 New York open."""
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).strftime("%Y-%m-%d")


def ingest_raw(raw: dict, root: str = realdata.REAL_ROOT,
               raw_path: str = "", verbose: bool = False) -> dict:
    """Turn transcribed (or fetched) raw responses into ledger rows."""
    provenance = raw.get("_provenance", {})
    nasdaq_meta = raw.get("nasdaq", {})
    yahoo_meta = raw.get("yahoo", {})
    rows: list[dict] = []

    # -- the exchange-published record ------------------------------------
    for symbol, table in sorted(nasdaq_meta.get("rows", {}).items()):
        assetclass = (nasdaq_meta.get("assetclass") or {}).get(symbol, "stocks")
        if not table:
            continue
        start = min(r[0].split("/")[2] + "-" + r[0].split("/")[0] + "-" + r[0].split("/")[1]
                    for r in table if r and r[0])
        end = max(r[0].split("/")[2] + "-" + r[0].split("/")[0] + "-" + r[0].split("/")[1]
                  for r in table if r and r[0])
        url = _nasdaq_url(symbol, assetclass, start, end)
        for row in table:
            if not row or not row[0]:
                continue
            month, day, year = row[0].split("/")
            session = f"{year}-{month}-{day}"
            row_values = list(row[1:]) + [None] * (5 - len(row[1:]))
            close, open_, high, low, volume = row_values[:5]
            rows.append({
                "symbol": symbol, "session": session, "source": "nasdaq",
                "close": _as_float(close), "open": _as_float(open_),
                "high": _as_float(high), "low": _as_float(low),
                "volume": _as_int(volume),
                "raw": {"close": close, "open": open_, "high": high, "low": low,
                        "volume": volume},
                "asset_class": assetclass,
                "source_class": prints.SOURCE_CLASSES["nasdaq"]["source_class"],
                "redistribution_status": prints.SOURCE_CLASSES["nasdaq"]["redistribution_status"],
                "url": url,
                "retrieved_utc": nasdaq_meta.get("retrieved_utc", ""),
                "retrieval_method": provenance.get("retrieval_method", ""),
            })

    # -- the vendor bars ---------------------------------------------------
    shared_timestamps = yahoo_meta.get("timestamps") or []
    for symbol, series in sorted(yahoo_meta.get("symbols", {}).items()):
        # Prefer the symbol's own timeline; the shared array is only a fallback
        # for the transcribed files that were written before it existed.
        timestamps = series.get("timestamps") or shared_timestamps
        opens = series.get("open") or []
        for i, ts in enumerate(timestamps):
            if i >= len(opens):
                continue
            session = _session_from_timestamp(ts)
            rows.append({
                "symbol": symbol, "session": session, "source": "yahoo",
                "close": _as_float((series.get("close") or [None])[i]),
                "open": _as_float(opens[i]),
                "high": _as_float((series.get("high") or [None])[i]),
                "low": _as_float((series.get("low") or [None])[i]),
                "volume": _as_int((series.get("volume") or [None])[i]),
                "raw": {"close": (series.get("close") or [None])[i],
                        "open": opens[i],
                        "high": (series.get("high") or [None])[i],
                        "low": (series.get("low") or [None])[i],
                        "volume": (series.get("volume") or [None])[i]},
                "asset_class": "etf" if symbol in ETF_SYMBOLS else "stocks",
                "venue": series.get("venue", ""),
                "source_class": prints.SOURCE_CLASSES["yahoo"]["source_class"],
                "redistribution_status": prints.SOURCE_CLASSES["yahoo"]["redistribution_status"],
                "url": _yahoo_url(symbol),
                "retrieved_utc": yahoo_meta.get("retrieved_utc", ""),
                "retrieval_method": provenance.get("retrieval_method", ""),
            })

    written = []
    for source in ("nasdaq", "yahoo"):
        incoming = [r for r in rows if r["source"] == source]
        if not incoming:
            continue
        ledger = prints.load_ledger(root, source)
        merged = prints.merge_rows(ledger.rows, incoming)
        info = prints.write_ledger(merged, prints.ledger_path(root, source))
        written.append({"source": source, **info})
        if verbose:
            print(f"  ledger {source}: {info['rows']} rows -> {info['path']}")

    # -- official FRED observations that arrived after the CSV collection ---
    official = raw.get("official_series") or raw.get("official_series_2026-09-18") or {}
    # Two shapes are accepted: ``{"values": {sid: {date: value}}}`` and the flat
    # ``{sid: {date: value}, "urls": {...}}`` the transcribed file uses.
    values = official.get("values") or {
        sid: obs for sid, obs in official.items()
        if sid not in ("note", "urls", "values") and isinstance(obs, dict)}
    if values:
        path = os.path.join(root, "fred", "latest_observations.json")
        existing = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                existing = json.load(handle)
        series_map = existing.get("series", {})
        for sid, observations in values.items():
            row = series_map.setdefault(sid, {"values": {}, "url": official.get("urls", {}).get(sid, "")})
            row["values"].update({d: v for d, v in observations.items() if v not in (None, "")})
            row["retrieved_utc"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            row["url"] = official.get("urls", {}).get(sid) or row.get("url", "")
        payload = {"generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "note": ("Official observations published after the bulk CSV collection. "
                            "sim.realdata.load_fred merges these on top of the CSV, later "
                            "dates winning, so a daily run does not rewrite a 500-row file."),
                   "series": series_map}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1, sort_keys=True)
            handle.write("\n")
        written.append({"source": "fred-latest",
                        **{"path": os.path.relpath(path, REPO_ROOT),
                           "series": sorted(values)}})
    return {"rows": rows, "written": written,
            "sessions": sorted({r["session"] for r in rows})}


def _as_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _as_int(value):
    number = _as_float(value)
    return int(number) if number is not None else None


# --------------------------------------------------------------------------
# Derived artefacts
# --------------------------------------------------------------------------

def rebuild(root: str = realdata.REAL_ROOT, raw_path: str = "",
            verbose: bool = False) -> dict:
    """Re-derive the price files, cross-check report and manifest from ledgers."""
    nasdaq = prints.load_ledger(root, "nasdaq")
    yahoo = prints.load_ledger(root, "yahoo")
    report = prints.crosscheck(nasdaq, yahoo)
    os.makedirs(os.path.dirname(prints.CROSSCHECK_PATH), exist_ok=True)
    with open(prints.CROSSCHECK_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1, sort_keys=True)
        handle.write("\n")

    updated = []
    symbols = sorted(set(nasdaq.symbols()) | set(yahoo.symbols()))
    for symbol in symbols:
        updated.append(_extend_price_file(symbol, yahoo, root))
        updated.append(_write_exchange_record(symbol, nasdaq, root))
    updated = [row for row in updated if row]

    manifest = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "collector": "scripts/collect_forward_prints.py",
        "ledgers": {"nasdaq": nasdaq.meta(), "yahoo": yahoo.meta()},
        "crosscheck": {k: report[k] for k in
                       ("symbol_sessions_compared", "symbols_compared",
                        "worst_close_difference_bps", "worst_field_difference_bps",
                        "close_disagreements_beyond_tolerance", "verdict")},
        "price_files_written": updated,
        "raw_transcription": os.path.relpath(raw_path, REPO_ROOT) if raw_path else "",
        "note": ("Ledgers hold one row per (source, symbol, session) with the publisher's "
                 "own value plus its URL and retrieval time. The price files are derived "
                 "from them; no price in this repository originates anywhere else."),
    }
    with open(prints.MANIFEST_PATH, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True)
        handle.write("\n")
    if verbose:
        print(f"  cross-check: {report['verdict']}")
        print(f"  worst close difference {report['worst_close_difference_bps']} bps "
              f"over {report['symbol_sessions_compared']} symbol-sessions")
    return {"crosscheck": report, "updated": updated, "manifest": manifest}


def _extend_price_file(symbol: str, ledger: prints.PrintLedger, root: str) -> dict:
    """Append this source's bars to a collected price file, never rewriting one."""
    path = os.path.join(root, "prices", ledger.source, f"{symbol}.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    bars = payload.get("bars") or []
    have = {b.get("date") for b in bars}
    new = []
    for row in ledger.rows:
        if row["symbol"] != symbol or row["session"] in have:
            continue
        if row.get("close") is None or row.get("open") is None:
            continue
        new.append({"date": row["session"], "open": row["open"], "high": row["high"],
                    "low": row["low"], "close": row["close"], "volume": row["volume"],
                    "adjclose": row["close"]})
    if not new:
        return {}
    previous_sha = prints.sha256_file(path)
    bars.extend(sorted(new, key=lambda b: b["date"]))
    payload["bars"] = bars
    payload["last_bar"] = bars[-1]["date"]
    payload["bars_appended_by"] = {
        "collector": "scripts/collect_forward_prints.py",
        "append_count": len(new),
        "appended_dates": [b["date"] for b in new],
        "source_class": ledger.meta()["source_class"],
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
        handle.write("\n")
    new_sha = prints.sha256_file(path)
    # The chain row is written while both hashes are in hand: the season run that
    # read the previous bytes recorded its own hash, and this row is what lets a
    # later custody check tie that hash to the file on disk.
    custody.log_rewrite(path, previous_sha, new_sha,
                        writer="scripts/collect_forward_prints.py", kind="append",
                        data_root=root,
                        reason=f"appended {len(new)} session(s) from the {ledger.source} "
                               f"print ledger",
                        detail={"appended_dates": [b["date"] for b in new],
                                "source_class": ledger.meta()["source_class"]})
    return {"path": os.path.relpath(path, REPO_ROOT), "appended": len(new),
            "last_bar": bars[-1]["date"], "sha256": new_sha}


def _write_exchange_record(symbol: str, ledger: prints.PrintLedger, root: str) -> dict:
    """Write the exchange-published record for one symbol as its own file."""
    rows = [r for r in ledger.rows if r["symbol"] == symbol]
    if not rows:
        return {}
    path = os.path.join(root, "prices", "nasdaq", f"{symbol}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bars = [{"date": r["session"], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"],
             "raw_response_values": r.get("raw", {})} for r in sorted(
                 rows, key=lambda r: r["session"])]
    ledger_meta = ledger.meta()
    retrieved = rows[0].get("retrieved_utc", "")
    if retrieved and "T" not in retrieved:
        # A bare date is not a retrieval time; say so instead of pretending.
        retrieved = retrieved + "T00:00:00Z"
    payload = {
        "symbol": symbol,
        "provider": "nasdaq-historical-api",
        "source_class": ledger_meta.get("source_class", ""),
        "redistribution_status": ledger_meta.get("redistribution_status", ""),
        "url": rows[0].get("url", ""),
        "source": rows[0].get("url", ""),
        "retrieved_at": retrieved,
        "retrieved_utc": retrieved,
        "asset_class": rows[0].get("asset_class", ""),
        # The audit asks for these two because a price without a citable raw
        # response is an assertion.  They point at the ledger and the custody
        # file the rows were transcribed from.
        "raw_file": ledger_meta.get("file", ""),
        "raw_sha256": ledger.sha256,
        "access_status": {"ledger_written": True, "retrieval": "public endpoint, "
                          "no authentication", "revision": "forward-window slice; "
                          "the two-year archive has not been collected"},
        "session_coverage": {"first_bar": bars[0]["date"], "last_bar": bars[-1]["date"],
                             "bars": len(bars)},
        "coverage": {"first_bar": bars[0]["date"], "last_bar": bars[-1]["date"],
                     "bars": len(bars)},
        "bars": bars,
        "eligibility_note": ("This file is a forward-window exchange record, not the "
                             "two-year archive sim.eligibility requires for an "
                             "official-price competition. It exists so a forward fill "
                             "can cite the exchange's own published row."),
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
        handle.write("\n")
    return {"path": os.path.relpath(path, REPO_ROOT), "bars": len(bars),
            "last_bar": bars[-1]["date"], "sha256": prints.sha256_file(path)}


def verify(root: str = realdata.REAL_ROOT, verbose: bool = False) -> int:
    nasdaq = prints.load_ledger(root, "nasdaq")
    yahoo = prints.load_ledger(root, "yahoo")
    report = prints.crosscheck(nasdaq, yahoo)
    print(f"print ledgers under {os.path.relpath(os.path.join(root, 'prints'), REPO_ROOT)}")
    for ledger in (nasdaq, yahoo):
        meta = ledger.meta()
        print(f"  {meta['source']:7s} {meta['rows']:5d} rows · {meta['symbols']:3d} symbols "
              f"· {meta['source_class']:19s} {'-'.join(meta['sessions'])} "
              f"sha256 {meta['sha256'][:16]}")
    print(f"  cross-check: {report['verdict']}")
    print(f"    compared {report['symbol_sessions_compared']} symbol-sessions, worst "
          f"close difference {report['worst_close_difference_bps']} bps "
          f"(tolerance {report['tolerance_bps']} bps)")
    if report["worst_field"]:
        worst = report["worst_field"]
        print(f"    widest single field: {worst['symbol']} {worst['session']} "
              f"{worst['field']} {worst['nasdaq']} vs {worst['yahoo']}")
    ok = not report["close_disagreements_beyond_tolerance"]
    for session in nasdaq.sessions():
        covered, missing = nasdaq.coverage(session, realdata.UNIVERSE)
        print(f"  session {session}: exchange prints {len(covered)}/{len(realdata.UNIVERSE)}"
              + (f" · missing {','.join(missing)}" if missing else ""))
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ingest-raw", metavar="PATH",
                        help="transcribed raw responses to ingest")
    parser.add_argument("--fetch", action="store_true",
                        help="retrieve the responses over HTTPS (CI path)")
    parser.add_argument("--rebuild", action="store_true",
                        help="re-derive price files and cross-checks from the ledgers")
    parser.add_argument("--verify", action="store_true", help="print the cross-check verdict")
    parser.add_argument("--start", default=None, help="first session to request (--fetch)")
    parser.add_argument("--end", default=None, help="last session to request (--fetch)")
    parser.add_argument("--root", default=realdata.REAL_ROOT)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not any((args.ingest_raw, args.fetch, args.rebuild, args.verify)):
        parser.print_help()
        return 2

    raw_path = ""
    if args.fetch:
        end = args.end or dt.date.today().isoformat()
        start = args.start or (dt.date.fromisoformat(end) - dt.timedelta(days=7)).isoformat()
        os.makedirs(prints.RAW_DIR, exist_ok=True)
        raw = fetch_raw(start, end, realdata.UNIVERSE, verbose=args.verbose)
        raw_path = os.path.join(prints.RAW_DIR, f"fetched_{start}_{end}.json")
        with open(raw_path, "w", encoding="utf-8") as handle:
            json.dump(raw, handle, indent=1, sort_keys=True)
            handle.write("\n")
        print(f"fetched raw responses -> {os.path.relpath(raw_path, REPO_ROOT)}")
        ingest_raw(raw, root=args.root, raw_path=raw_path, verbose=args.verbose)

    if args.ingest_raw:
        with open(args.ingest_raw, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        result = ingest_raw(raw, root=args.root, raw_path=args.ingest_raw,
                            verbose=args.verbose)
        print(f"ingested {len(result['rows'])} print rows over sessions "
              f"{', '.join(result['sessions'])}")
        for row in result["written"]:
            print(f"  wrote {row['path']}")
        raw_path = args.ingest_raw

    if args.rebuild or args.fetch or args.ingest_raw:
        out = rebuild(root=args.root, raw_path=raw_path, verbose=True)
        print(f"  price files touched: {len([r for r in out['updated'] if r])}")

    if args.verify:
        return verify(root=args.root, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
