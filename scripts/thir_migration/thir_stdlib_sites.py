"""Raise-site spy over the stdlib fallback sweep: where each reject is DECIDED.

A composed reject tag names where a reason was FORMATTED, not where it was
decided -- a statement chokepoint prefixes its own tag onto the operand reason
it caught, so `stmt.return:X`, `stmt.assign:X` and `stmt.var_decl:X` are three
spellings of one decision. Scoping work by tag therefore partitions by the
wrong thing. This records every ThirUnsupported construction with the tpyc
frame that built it, so the tail can be scoped by raise SITE instead.

Reasons compose by containment, so a captured reason that is a SUBSTRING of a
body's final reason is a candidate decider; longest match first puts the
innermost site above the chokepoint that reformatted it.

BLIND SPOT, and it is not small: the resumable and simple-generator frame gates
reject via a `_reject()` helper that notes the reason and returns None without
ever constructing a ThirUnsupported. Those bodies print "(no matching raise
captured)" and their sites must be found by grepping the reason string.

Usage (from the repo root):
    uv run python scripts/thir_migration/thir_stdlib_sites.py [mod,mod,...]
"""

from __future__ import annotations

import importlib.util
import os
import sys
import traceback
from collections import defaultdict
from pathlib import Path

REPO = Path(os.environ.get("TPY_REPO") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(REPO))

_SWEEP = REPO / "scripts" / "thir_migration" / "thir_stdlib_fallback.py"
_spec = importlib.util.spec_from_file_location("tpy_thir_stdlib_fallback", _SWEEP)
sweep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sweep)

from tpyc.thir import fallback as FB  # noqa: E402

_orig_init = FB.ThirUnsupported.__init__
_seen: list[tuple[str, str]] = []


def _spy(self, *args, **kwargs):
    _orig_init(self, *args, **kwargs)
    reason = args[0] if args else kwargs.get("reason", "?")
    site = "?"
    for frame in traceback.extract_stack()[:-1][::-1]:
        if os.sep + "tpyc" + os.sep in frame.filename:
            site = f"{Path(frame.filename).name}:{frame.lineno}"
            break
    _seen.append((str(reason), site))


def main() -> int:
    FB.ThirUnsupported.__init__ = _spy
    names = (sys.argv[1].split(",") if len(sys.argv) > 1
             else sweep.stdlib_module_names())
    tmp = REPO / "__tpyc__" / "_thir_sites_tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    merged: dict = {}
    per_body_raises: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for name in names:
        if sweep.is_macro_module(name):
            continue
        _seen.clear()
        try:
            per_module, _errors = sweep.run_entry(f"import {name}\n", tmp,
                                                  name.replace(".", "_"))
        except Exception as exc:  # noqa: BLE001 -- a crash IS the datum
            print(f"!! {name}: {type(exc).__name__}: {exc}")
            continue
        raises = list(_seen)
        for mod, bodies in per_module.items():
            sweep.merge_module(merged, mod, bodies)
            for body in bodies:
                if body["status"] == "fallback":
                    per_body_raises[(mod, body["name"])] = raises

    rows = [(mod, name, body["reason"])
            for mod, slot in merged.items()
            for name, body in slot.items() if body["status"] == "fallback"]
    print(f"fallback rows: {len(rows)}\n")

    for mod, name, reason in sorted(rows):
        hits = [(r, s) for r, s in per_body_raises.get((mod, name), [])
                if r and r in reason]
        hits.sort(key=lambda rs: -len(rs[0]))
        ordered: list[tuple[str, str]] = []
        for hit in hits:
            if hit not in ordered:
                ordered.append(hit)
        print(f"{mod}::{name}\n    reason: {reason}")
        for r, s in ordered[:4]:
            print(f"    site  : {s}   <- {r}")
        if not ordered:
            print("    site  : (no matching raise captured -- frame-gate reject, grep the reason)")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
