"""Fail-closed eligibility checks for official historical security prices.

A public URL is not, by itself, permission to redistribute market data.  This
module separates the two questions that used to be conflated in Season 2:

* can a runner retrieve a publisher's response; and
* does the committed artefact document an accepted source class, raw response
  checksum, coverage, validation and redistribution/access status?

The current Yahoo-backed Season 2 files deliberately fail this audit.  The
Nasdaq adapter writes official-source candidates, but its public-site terms do
not establish permission to redistribute the response, so the strict gate also
fails until a licensed/redistributable status is recorded.  A competition can
therefore never silently fall back to an aggregator.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
from urllib.parse import urlparse
from typing import Dict, List, Optional, Sequence, Tuple

from .calendar import REPO_ROOT


OFFICIAL_PRICE_BACKEND = "nasdaq"
OFFICIAL_SOURCE_CLASS = "OFFICIAL"
ACCEPTED_REDISTRIBUTION_STATUSES = frozenset({
    "CONFIRMED_PUBLIC_REDISTRIBUTION",
    "LICENSED_FOR_REPOSITORY_REPRODUCTION",
})


class OfficialPriceEligibilityError(RuntimeError):
    """Raised when a strict official-price run must stop rather than guess."""

    def __init__(self, audit: dict):
        self.audit = audit
        problems = audit.get("issues") or []
        detail = "; ".join(
            f"{p.get('symbol', '*')}: {p.get('code', 'invalid')}"
            for p in problems[:12]
        ) or "no eligible official price files"
        if len(problems) > 12:
            detail += f"; +{len(problems) - 12} more"
        super().__init__("official price eligibility failed closed: " + detail)


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _resolve_path(value: str, root: str) -> str:
    """Resolve a recorded repository-relative or data-root-relative path."""
    if os.path.isabs(value):
        return value
    if value.startswith("data/real/"):
        return os.path.join(REPO_ROOT, value)
    return os.path.join(root, value)


def _manifest_rows(root: str) -> List[dict]:
    manifest = _read_json(os.path.join(root, "collection_manifest.json")) or {}
    rows = manifest.get("requests") or []
    return [row for row in rows if isinstance(row, dict)]


def _parse_fred_dates(root: str, start: str, end: str) -> List[str]:
    """Use the committed official SP500 observations as the session calendar."""
    directory = os.path.join(root, "fred")
    candidates = []
    if os.path.isdir(directory):
        candidates = [os.path.join(directory, name)
                      for name in os.listdir(directory)
                      if name.startswith("SP500_") and name.endswith(".csv")]
    best: Tuple[int, List[str]] = (0, [])
    for path in candidates:
        dates: List[str] = []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    parts = line.strip().split(",")
                    if len(parts) < 2 or parts[0] in ("DATE", "observation_date"):
                        continue
                    if parts[1] and parts[1] != "." and start <= parts[0] <= end:
                        _dt.date.fromisoformat(parts[0])
                        dates.append(parts[0])
        except (OSError, ValueError):
            continue
        if len(dates) > best[0]:
            best = (len(dates), sorted(set(dates)))
    return best[1]


def _issue(issues: List[dict], symbol: str, code: str, detail: str) -> None:
    issues.append({"symbol": symbol, "code": code, "detail": detail})


def _number(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _validate_bars(symbol: str, bars: object, start: str, end: str,
                   expected_dates: Sequence[str], issues: List[dict]) -> dict:
    if not isinstance(bars, list) or not bars:
        _issue(issues, symbol, "EMPTY_BARS", "bars is absent or empty")
        return {"count": 0, "first": None, "last": None, "missing_dates": list(expected_dates)}

    dates: List[str] = []
    by_date: Dict[str, dict] = {}
    for index, row in enumerate(bars):
        if not isinstance(row, dict):
            _issue(issues, symbol, "BAR_NOT_OBJECT", f"bar {index} is not an object")
            continue
        date = row.get("date")
        try:
            _dt.date.fromisoformat(str(date))
        except (TypeError, ValueError):
            _issue(issues, symbol, "BAD_DATE", f"bar {index} has invalid date {date!r}")
            continue
        date = str(date)
        dates.append(date)
        if date in by_date:
            _issue(issues, symbol, "DUPLICATE_DATE", date)
        by_date[date] = row
        values = {name: _number(row.get(name))
                  for name in ("open", "high", "low", "close", "volume")}
        if any(values[name] is None for name in ("open", "high", "low", "close")):
            _issue(issues, symbol, "BAD_OHLC", f"non-numeric OHLC on {date}")
            continue
        raw_volume = row.get("volume")
        if (values["volume"] is None or values["volume"] < 0 or
                float(values["volume"]).is_integer() is False or
                (isinstance(raw_volume, str) and not raw_volume.replace(",", "").strip().isdigit())):
            _issue(issues, symbol, "BAD_VOLUME", f"negative/non-integer volume on {date}")
        if (any(values[name] <= 0 for name in ("open", "high", "low", "close")) or
                values["low"] > min(values["open"], values["close"]) or
                values["high"] < max(values["open"], values["close"]) or
                values["low"] > values["high"]):
            _issue(issues, symbol, "OHLC_INVARIANT", f"invalid OHLC relationship on {date}")

    ordered = sorted(set(dates))
    if ordered and (ordered[0] > start or ordered[-1] < end):
        _issue(issues, symbol, "DATE_RANGE", f"coverage {ordered[0]} to {ordered[-1]} does not cover {start} to {end}")
    missing = sorted(set(expected_dates) - set(ordered)) if expected_dates else []
    if missing:
        _issue(issues, symbol, "MISSING_SESSIONS", f"{len(missing)} expected sessions missing; first {missing[:3]}")
    if ordered != dates:
        _issue(issues, symbol, "UNSORTED_BARS", "bars are not in chronological order")
    return {"count": len(bars), "first": ordered[0] if ordered else None,
            "last": ordered[-1] if ordered else None, "missing_dates": missing}


def audit_official_prices(
    root: str,
    symbols: Sequence[str],
    start: str,
    end: str,
    backend: str = OFFICIAL_PRICE_BACKEND,
    require_redistribution: bool = True,
    require_dividends: bool = True,
) -> dict:
    """Audit all price files needed by a strict competition.

    The function returns a JSON-serialisable report even on failure.  It never
    substitutes another directory or source class.  ``require_redistribution``
    remains explicit so tests and a future licensed feed can demonstrate the
    distinction between retrieval and permission.
    """
    issues: List[dict] = []
    per_symbol: Dict[str, dict] = {}
    expected_dates = _parse_fred_dates(root, start, end)
    manifest = _manifest_rows(root)
    required = tuple(dict.fromkeys(symbols))

    if not expected_dates:
        _issue(issues, "*", "NO_OFFICIAL_CALENDAR", "data/real/fred/SP500_* has no usable observations")

    for symbol in required:
        symbol_issues: List[dict] = []
        filename = symbol.replace("^", "_") + ".json"
        path = os.path.join(root, "prices", backend, filename)
        path_label = os.path.relpath(path, root)
        row_report = {"symbol": symbol, "path": path_label,
                      "eligible": False, "issues": symbol_issues}
        payload = _read_json(path)
        if payload is None:
            _issue(symbol_issues, symbol, "MISSING_FILE", path_label)
            per_symbol[symbol] = row_report
            issues.extend(symbol_issues)
            continue

        if payload.get("symbol") != symbol:
            _issue(symbol_issues, symbol, "SYMBOL_MISMATCH", repr(payload.get("symbol")))
        if payload.get("source_class") != OFFICIAL_SOURCE_CLASS:
            _issue(symbol_issues, symbol, "NON_OFFICIAL_SOURCE_CLASS",
                   repr(payload.get("source_class")))
        url = str(payload.get("source") or payload.get("url") or "")
        parsed_url = urlparse(url)
        if (parsed_url.scheme != "https" or parsed_url.netloc != "api.nasdaq.com" or
                not parsed_url.path.startswith("/api/quote/")):
            _issue(symbol_issues, symbol, "BAD_SOURCE_URL", url or "missing")
        for field in ("retrieved_at", "access_status", "redistribution_status", "raw_file", "raw_sha256"):
            if not str(payload.get(field) or "").strip():
                _issue(symbol_issues, symbol, "MISSING_PROVENANCE", field)
        if require_redistribution and payload.get("redistribution_status") not in ACCEPTED_REDISTRIBUTION_STATUSES:
            _issue(symbol_issues, symbol, "REDISTRIBUTION_NOT_CONFIRMED",
                   str(payload.get("redistribution_status") or "missing"))
        try:
            _dt.datetime.fromisoformat(str(payload.get("retrieved_at", "")).replace("Z", "+00:00"))
        except ValueError:
            _issue(symbol_issues, symbol, "BAD_RETRIEVAL_TIME", str(payload.get("retrieved_at")))

        raw_value = str(payload.get("raw_file") or "")
        raw_path = _resolve_path(raw_value, root) if raw_value else ""
        raw_sha = str(payload.get("raw_sha256") or "")
        if raw_path and os.path.exists(raw_path):
            actual_raw_sha = _sha256(raw_path)
            row_report["raw_sha256"] = actual_raw_sha
            if actual_raw_sha != raw_sha:
                _issue(symbol_issues, symbol, "RAW_CHECKSUM_MISMATCH",
                       f"recorded {raw_sha}, actual {actual_raw_sha}")
            if payload.get("raw_bytes") is not None:
                try:
                    expected_bytes = int(payload["raw_bytes"])
                except (TypeError, ValueError):
                    expected_bytes = -1
                if expected_bytes != os.path.getsize(raw_path):
                    _issue(symbol_issues, symbol, "RAW_BYTE_COUNT_MISMATCH",
                           f"recorded {payload.get('raw_bytes')}, actual {os.path.getsize(raw_path)}")
            matches = [m for m in manifest if m.get("url") == url and m.get("ok") and
                       m.get("sha256") == actual_raw_sha and str(m.get("status")) == "200"]
            if not matches:
                _issue(symbol_issues, symbol, "MANIFEST_CHECKSUM_NOT_FOUND",
                       "no successful HTTP 200 collection-manifest request matches raw bytes")
        else:
            _issue(symbol_issues, symbol, "RAW_FILE_MISSING", raw_value or "missing")

        if payload.get("http_status") not in (None, 200):
            _issue(symbol_issues, symbol, "PRICE_HTTP_NOT_OK", str(payload.get("http_status")))

        coverage = _validate_bars(symbol, payload.get("bars"), start, end,
                                  expected_dates, symbol_issues)
        row_report.update({"coverage": coverage, "source_class": payload.get("source_class"),
                           "provider": payload.get("provider"),
                           "redistribution_status": payload.get("redistribution_status"),
                           "access_status": payload.get("access_status"),
                           "sha256": _sha256(path)})
        dividend_status = str(payload.get("dividend_status") or "")
        row_report["dividend_status"] = dividend_status
        if require_dividends and dividend_status not in ("AVAILABLE", "NO_DECLARED_DIVIDENDS"):
            _issue(symbol_issues, symbol, "DIVIDEND_DATA_NOT_VERIFIED", dividend_status or "missing")
        if require_dividends:
            dividend_raw_value = str(payload.get("dividend_raw_file") or "")
            dividend_raw_sha = str(payload.get("dividend_raw_sha256") or "")
            dividend_url = str(payload.get("dividend_source") or "")
            if not dividend_raw_value or not dividend_raw_sha or not dividend_url:
                _issue(symbol_issues, symbol, "DIVIDEND_PROVENANCE_MISSING",
                       "dividend source URL/raw file/raw checksum is incomplete")
            else:
                dividend_path = _resolve_path(dividend_raw_value, root)
                if not os.path.exists(dividend_path):
                    _issue(symbol_issues, symbol, "DIVIDEND_RAW_FILE_MISSING", dividend_raw_value)
                else:
                    actual_dividend_sha = _sha256(dividend_path)
                    if actual_dividend_sha != dividend_raw_sha:
                        _issue(symbol_issues, symbol, "DIVIDEND_RAW_CHECKSUM_MISMATCH",
                               f"recorded {dividend_raw_sha}, actual {actual_dividend_sha}")
                    if not any(m.get("url") == dividend_url and m.get("ok") and
                               m.get("sha256") == actual_dividend_sha and
                               str(m.get("status")) == "200" for m in manifest):
                        _issue(symbol_issues, symbol, "DIVIDEND_MANIFEST_CHECKSUM_NOT_FOUND",
                               "no successful HTTP 200 manifest row matches dividend bytes")
        row_report["eligible"] = not symbol_issues
        per_symbol[symbol] = row_report
        issues.extend(symbol_issues)

    eligible = not issues and len(per_symbol) == len(required)
    return {
        "eligible": eligible,
        "backend": backend,
        "source_class_required": OFFICIAL_SOURCE_CLASS,
        "redistribution_statuses_accepted": sorted(ACCEPTED_REDISTRIBUTION_STATUSES),
        "window": {"start": start, "end": end},
        "required_symbols": list(required),
        "calendar_sessions_checked": len(expected_dates),
        "symbols": per_symbol,
        "issues": issues,
        "reason": "eligible official and redistributable price files" if eligible else
                  "at least one official-price provenance, validation, coverage, or licensing check failed",
    }


def require_official_prices(**kwargs) -> dict:
    """Run the audit and raise a descriptive error unless every check passes."""
    audit = audit_official_prices(**kwargs)
    if not audit["eligible"]:
        raise OfficialPriceEligibilityError(audit)
    return audit


__all__ = [
    "ACCEPTED_REDISTRIBUTION_STATUSES", "OFFICIAL_PRICE_BACKEND",
    "OFFICIAL_SOURCE_CLASS", "OfficialPriceEligibilityError",
    "audit_official_prices", "require_official_prices",
]
