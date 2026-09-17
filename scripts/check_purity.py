"""Check that the simulation is pure standard library.

The engine deliberately has **no** third-party dependencies: no numpy, no pandas,
no requests. That is a design decision, not an accident - it means a season can be
reproduced years from now on any CPython 3.11+ without resolving a dependency
tree, which matters for a project whose entire output is an audit trail.

This script walks the import statements of ``sim/``, ``scripts/`` and ``tests/``
and fails if anything outside the standard library (or the local packages
themselves) is imported. It is run by CI and can be run by hand::

    python3 scripts/check_purity.py
"""

from __future__ import annotations

import ast
import os
import sys
from typing import List, Set

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_PACKAGES = {"sim", "scripts", "tests"}


def _top_level(name: str) -> str:
    return name.split(".", 1)[0]


def _is_local(module: str) -> bool:
    """True for this project's own packages and for sibling modules inside them
    (``fixtures``, ``build_site``, one test importing another)."""
    if module in LOCAL_PACKAGES:
        return True
    for root in (REPO_ROOT,) + tuple(
            os.path.join(REPO_ROOT, p) for p in sorted(LOCAL_PACKAGES)):
        if (os.path.exists(os.path.join(root, module + ".py"))
                or os.path.exists(os.path.join(root, module, "__init__.py"))):
            return True
    return False


def imports_in(path: str) -> List[str]:
    """Every top-level module name imported by a source file."""
    with open(path, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    found: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(_top_level(alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # relative import -> local by definition
                continue
            if node.module:
                found.add(_top_level(node.module))
    return sorted(found)


def main(argv: List[str]) -> int:
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    offenders: List[str] = []
    checked = 0
    for package in ("sim", "scripts", "tests"):
        root = os.path.join(REPO_ROOT, package)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for filename in sorted(filenames):
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                checked += 1
                for module in imports_in(path):
                    if _is_local(module):
                        continue
                    if module in stdlib:
                        continue
                    offenders.append(
                        f"{os.path.relpath(path, REPO_ROOT)}: {module}")

    print(f"scanned {checked} modules under sim/, scripts/, tests/")
    if offenders:
        print("\nTHIRD-PARTY IMPORTS FOUND - the engine must stay stdlib-only:")
        for line in offenders:
            print(f"  {line}")
        return 1
    print("OK: every import resolves to the standard library or to this project")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
