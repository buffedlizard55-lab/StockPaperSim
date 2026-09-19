"""Custody: which collected file a run recorded, and how a later write chains to it.

A season run records the SHA-256 of every file it read.  Those files are *live*:
the collector appends the next session's bars to a price file, refreshes a FRED
CSV with the publisher's current values, and regenerates the derived index
documents.  A hash recorded in September therefore stops matching in October, and
the interesting question is not "does it still match" but "can the change be
explained, and can the bytes the run read be recovered".

Two kinds of change are provable, and this module exists so the distinction is
mechanical rather than a matter of trust:

``APPEND``
    New observations were added at the end.  Removing them and re-serialising the
    file the way its writer does must reproduce the run's recorded hash exactly.
    That is a proof, not an assertion: anything else about the file having moved
    would break the reconstruction.

``REWRITE``
    The file was regenerated (a publisher revised an observation, a derived index
    document was rebuilt).  The bytes the run read are gone, so the best available
    evidence is a *chain*: a log row recorded by the writer at the moment of the
    write, naming the previous hash, the new hash, who wrote it and why.  A chain
    row is weaker evidence than a reconstruction and every report that counts one
    says so, which is the point of keeping the two apart.

The log is append-only (``data/real/regeneration_log.jsonl``), one JSON object per
line, so a later write cannot erase the record of an earlier one.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from typing import Dict, List, Optional

from .calendar import REPO_ROOT

REAL_ROOT = os.path.join(REPO_ROOT, "data", "real")
REGENERATION_LOG = os.path.join(REAL_ROOT, "regeneration_log.jsonl")

#: What a row's ``kind`` may be.  ``append`` means the writer added observations
#: to the end of an existing file; ``rewrite`` means it regenerated the file.
KINDS = ("append", "rewrite")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def log_rewrite(path: str, previous_sha256: str, new_sha256: str, writer: str,
                kind: str = "rewrite", reason: str = "",
                data_root: str = REAL_ROOT, detail: Optional[dict] = None) -> dict:
    """Record one write to a collected file, before anyone can forget it.

    Called by the writer while it still has both hashes in hand.  ``kind`` has to
    be one of :data:`KINDS`; a caller that is unsure says ``rewrite``, which is
    the weaker claim and the one a reader will check.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
    rel = os.path.relpath(os.path.abspath(path), REPO_ROOT)
    row = {
        "utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "path": rel,
        "kind": kind,
        "previous_sha256": previous_sha256,
        "sha256": new_sha256,
        "writer": writer,
        "reason": reason,
        "runnable": os.environ.get("GITHUB_RUN_ID", ""),
    }
    if detail:
        row["detail"] = detail
    # ``data_root`` is the collected-data directory (data/real), not the
    # repository root: the log lives beside the files it describes.
    log_path = os.path.join(data_root, "regeneration_log.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def read_log(data_root: str = REAL_ROOT) -> List[dict]:
    path = os.path.join(data_root, "regeneration_log.jsonl")
    if not os.path.exists(path):
        return []
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def chain_for(path: str, recorded_sha256: str,
              data_root: str = REAL_ROOT) -> Optional[dict]:
    """The log row that explains ``recorded_sha256`` for ``path``, if there is one.

    A row explains the recorded hash when the row's *previous* hash is the
    recorded one - i.e. the recorded bytes were the state immediately before the
    write.  When several rows chain (two collection runs in a row), the caller
    only needs the first link: that is the one that consumed the run's bytes.
    """
    rel = os.path.relpath(os.path.abspath(path), REPO_ROOT)
    for row in read_log(data_root):
        if row.get("path") == rel and row.get("previous_sha256") == recorded_sha256:
            return row
    return None


def chained_forward(path: str, recorded_sha256: str,
                    data_root: str = REAL_ROOT) -> Dict[str, object]:
    """Walk the log forward from ``recorded_sha256`` to what is on disk now.

    A chain that does not end at the file's current hash is not evidence of
    anything, so this returns ``{"chained": False, "reason": ...}`` rather than a
    hopeful answer.  A chain that does end there says the file moved through the
    recorded states and no others - which is exactly the claim a custody check
    wants to make.
    """
    rel = os.path.relpath(os.path.abspath(path), REPO_ROOT)
    rows = [r for r in read_log(data_root) if r.get("path") == rel]
    index = {r.get("previous_sha256"): r for r in rows}
    current = sha256_file(path) if os.path.exists(path) else ""
    steps: List[dict] = []
    cursor = recorded_sha256
    seen = set()
    while cursor in index and cursor not in seen:
        seen.add(cursor)
        row = index[cursor]
        steps.append({"kind": row.get("kind"), "utc": row.get("utc"),
                      "writer": row.get("writer"), "reason": row.get("reason"),
                      "sha256": row.get("sha256")})
        cursor = row.get("sha256")
    chained = bool(steps) and cursor == current
    return {"chained": chained, "steps": steps, "final_sha256": cursor,
            "current_sha256": current, "path": rel,
            "reason": "" if chained else (
                "no logged chain from the recorded hash ends at the file on disk")}


__all__ = ["REGENERATION_LOG", "KINDS", "sha256_file", "log_rewrite", "read_log",
           "chain_for", "chained_forward"]
