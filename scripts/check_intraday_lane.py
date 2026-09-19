#!/usr/bin/env python3
"""Look for the intraday capture, and record what was found.

Three rows of the executive summary are ``not_run``: the intraday study, the
volatile-stock division, and the per-stock rows that inherit the division's
blocker.  The reason given for all three is one fact - the intraday capture has
not landed on this branch - and a fact like that should be *checked*, not
asserted in prose.  If the lane commits tomorrow, the summary must notice by
itself; nobody should have to remember to edit a sentence.

This script performs the check and writes the evidence to
``research/INTRADAY_LANE_CHECK.json``:

* whether ``data/real/intraday/`` exists in this working tree, and what is in it;
* every tracked path under it in the whole git history (``git log --all``), so a
  capture that landed on the lane's own branch is visible from here;
* every local or remote ref whose name mentions the lane;
* the exact commands and their output, so a reader can re-run them.

Two questions are asked and kept apart, because they have different answers and
different strengths of evidence:

``verdict``
    Is a capture **in this working tree**?  This is a directory listing, so the
    answer is definitive: ``LANDED_IN_THIS_TREE`` or ``NOT_IN_THIS_TREE``.

``history_check``
    Has a capture ever been committed **anywhere this clone can see**?  A shallow
    clone cannot answer that, and this script says so -
    ``CANNOT_TELL_SHALLOW_CLONE`` - rather than reporting a confident "never",
    which is the one claim a shallow clone is not entitled to make.  On a
    full-history checkout (the scheduled workflow uses ``fetch-depth: 0``) the
    answer is ``SEEN`` or ``NOT_SEEN``.

The executive summary publishes the tree answer, which is the fact its three
``not_run`` rows rest on, and carries the history answer beside it as evidence.

Run:  python3 scripts/check_intraday_lane.py [--json PATH] [--quiet]
Exit: 0 when a check completed (whatever it found), 1 when the check itself
      failed to run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from typing import Dict, List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTRADAY_DIR = os.path.join(REPO_ROOT, "data", "real", "intraday")
DEFAULT_OUT = os.path.join(REPO_ROOT, "research", "INTRADAY_LANE_CHECK.json")

#: Path prefixes a capture would live under, in the layout the summary names.
CAPTURE_PATHS = ("data/real/intraday", "data/real/prints/intraday")


def _run(args: List[str]) -> Dict[str, object]:
    """Run one git command and keep everything a reader needs to re-run it."""
    try:
        proc = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True,
                              timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": " ".join(args), "ok": False, "error": str(exc),
                "stdout": "", "stderr": "", "returncode": None}
    return {"command": " ".join(args), "ok": proc.returncode == 0,
            "returncode": proc.returncode, "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip()[:2000]}


def _tree_state() -> dict:
    """What is on disk right now, for every capture path the summary names."""
    state = {}
    for rel in CAPTURE_PATHS:
        path = os.path.join(REPO_ROOT, rel)
        if not os.path.isdir(path):
            state[rel] = {"exists": False, "files": 0, "bytes": 0}
            continue
        files = 0
        total = 0
        for base, _dirs, names in os.walk(path):
            for name in names:
                files += 1
                try:
                    total += os.path.getsize(os.path.join(base, name))
                except OSError:
                    pass
        state[rel] = {"exists": True, "files": files, "bytes": total}
    return state


def check() -> dict:
    """The evidence, gathered once so the JSON and the printout cannot differ."""
    tree = _tree_state()
    history: Dict[str, dict] = {}
    for rel in CAPTURE_PATHS:
        history[rel] = _run(["git", "log", "--all", "--oneline", "--", rel])
    refs = _run(["git", "for-each-ref", "--format=%(refname)", "--contains", "HEAD"])
    lane_refs = _run(["git", "for-each-ref", "--format=%(refname)",
                      "refs/heads", "refs/remotes"])
    shallow = _run(["git", "rev-parse", "--is-shallow-repository"])

    committed = {}
    for rel, row in history.items():
        committed[rel] = [line for line in str(row["stdout"]).splitlines() if line.strip()]
    is_shallow = str(shallow.get("stdout", "")).strip().lower() == "true"
    git_usable = all(row["ok"] for row in history.values()) and shallow["ok"]

    names = []
    for value in (lane_refs.get("stdout") or "", refs.get("stdout") or ""):
        for line in str(value).splitlines():
            line = line.strip()
            if line and ("intraday" in line.lower() or "lane" in line.lower()):
                names.append(line)
    names = sorted(set(names))

    on_disk = any(state["exists"] and state["files"] for state in tree.values())
    in_history = any(committed.get(rel) for rel in CAPTURE_PATHS)
    verdict = "LANDED_IN_THIS_TREE" if on_disk else "NOT_IN_THIS_TREE"
    if in_history:
        history_check = "SEEN"
    elif not git_usable or is_shallow:
        history_check = "CANNOT_TELL_SHALLOW_CLONE"
    else:
        history_check = "NOT_SEEN"

    return {
        "title": "Intraday capture check",
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runner": {"workflow": os.environ.get("GITHUB_WORKFLOW", ""),
                   "run_id": os.environ.get("GITHUB_RUN_ID", ""),
                   "on_actions": bool(os.environ.get("GITHUB_ACTIONS"))},
        "verdict": verdict,
        "history_check": history_check,
        "in_this_tree": on_disk,
        "seen_anywhere_in_history": in_history,
        "lane_refs_visible_count": len(names),
        "what_this_checks": ("whether session-sliced intraday prints exist in this "
                             "working tree or anywhere in the git history of this clone"),
        "tree": tree,
        "commits_touching_the_path": committed,
        "lane_refs_visible": names,
        "shallow_clone": is_shallow,
        "commands": {"history": history, "refs": lane_refs, "shallow": shallow},
        "why_it_is_not_run": {
            "study": ("the intraday study is defined on session-sliced intraday "
                      "prints; with no capture in the tree there is no tape to "
                      "study, and a daily-bar study would be a different study"),
            "volatile_stock_division": ("the division's candidate set is selected "
                                        "on a volatility screen measured over "
                                        "intraday sessions, and a scoreboard "
                                        "cannot be published before its selector "
                                        "has been pre-registered and applied"),
            "exec_summary_stock_rows": ("the per-name stock rows are the "
                                        "division's output, so they inherit its "
                                        "blocker rather than reporting a number "
                                        "from a different sample"),
        },
        "unblocks_when": ("a capture commits prints under data/real/intraday/ with "
                          "a retrievable URL and a SHA-256 per file, or an official "
                          "free intraday source is collected the same way"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default=DEFAULT_OUT,
                    help="where to write the check (default: %(default)s)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        payload = check()
    except Exception as exc:                                     # pragma: no cover
        print(f"intraday lane check failed to run: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True)
        handle.write("\n")

    if not args.quiet:
        print(f"intraday capture: {payload['verdict']}")
        for rel, state in payload["tree"].items():
            print(f"  {rel}: exists={state['exists']} files={state['files']} "
                  f"bytes={state['bytes']}")
        for rel, rows in payload["commits_touching_the_path"].items():
            print(f"  commits touching {rel}: {len(rows)}")
        if payload["lane_refs_visible"]:
            print(f"  lane refs visible: {', '.join(payload['lane_refs_visible'][:5])}")
        print(f"  written to {os.path.relpath(args.json, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
