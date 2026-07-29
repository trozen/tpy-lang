"""Cutover gate A5: how much of the STDLIB (lib/tpy) falls back if THIR
routing is extended past the user-module boundary.

The migration deliberately scopes THIR to user modules (compiler.py
`_make_codegen`), so the stdlib surface is invisible to the corpus dial and to
the ratchet -- yet the AST body emitter cannot be deleted while stdlib bodies
still need it. This lifts the scoping gate for a MEASUREMENT ONLY: every module
routes, the generated C++ is DISCARDED (there is no oracle for it -- see the D4
stdlib-oracle gap), and each body is classified routed / fallback(reason) /
not-attempted / not-a-candidate.

One entry program per stdlib module (`import <mod>`), so transitive deps are
covered and a module that fails to compile standalone costs only itself. Bodies
are deduped by (module, name) across entries, worst sighting winning.

FLOOR, not a total: an import-only entry instantiates almost nothing, and
resumable frames plus generic monomorphizations only attempt lowering at
emission time. The reject-reason MIX is representative; the count is a lower
bound.

Usage: uv run python scripts/thir_migration/thir_stdlib_fallback.py \
           [--out out.json] [--modules a,b]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

REPO = Path(os.environ.get("TPY_REPO") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(REPO))

from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp.context import CodeGenOptions  # noqa: E402
from tpyc import get_lib_dir  # noqa: E402
from tpyc.thir.fallback import is_bodyless_binding  # noqa: E402
from tpyc.thir.lower import iter_module_callables, iter_module_constructors  # noqa: E402

LIB = get_lib_dir() / "tpy"


def stdlib_module_names() -> list[str]:
    names: list[str] = []
    for path in sorted(LIB.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(LIB)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][: -len(".py")]
        if not parts:
            continue
        names.append(".".join(parts))
    return names


def is_macro_module(name: str) -> bool:
    path = LIB / (name.replace(".", "/") + ".py")
    if not path.exists():
        path = LIB / (name.replace(".", "/") + "/__init__.py")
    if not path.exists():
        return False
    head = path.read_text(errors="replace")[:4000]
    return "macro_module" in head


def classify(compiled, ctx, reasons) -> list[dict]:
    """Per-body classification, mirroring dump_codegen_thir's routing reads.

    `not_attempted` is its own bucket, not a fallback: a resumable body only
    attempts lowering at FRAME EMISSION, so one that this entry program never
    instantiates records no reason at all. Calling that a fallback would
    inflate the gate; calling it routed would flatter it."""
    out: list[dict] = []
    ast = compiled.ast
    analyzer = compiled.analyzer
    if ast.top_level_stmts:
        routed = getattr(ctx, "thir_top_level", None) is not None
        reason = reasons.get(id(ast))
        out.append({
            "name": "<top_level>", "kind": "top_level",
            "status": ("routed" if routed
                       else "fallback" if reason else "not_attempted"),
            "reason": reason,
        })
    for func, _self_type in iter_module_callables(ast, analyzer):
        key = id(func)
        stubs = analyzer.overload_groups.get(key)
        stub_fns = ([ctx.thir_functions.get((key, id(s))) for s in stubs]
                    if stubs else [])
        kind = ("async" if func.is_async
                else "generator" if func.is_generator else "fn")
        if (key in ctx.thir_functions
                or (stub_fns and all(f is not None for f in stub_fns))
                or ctx.thir_resumables.get(key) is not None
                or key in ctx.thir_simple_gens):
            status, reason = "routed", None
        elif is_bodyless_binding(func) or getattr(func, "is_overload_stub", False):
            status, reason = "not_a_candidate", None
        else:
            reason = reasons.get(key)
            status = "fallback" if reason else "not_attempted"
        out.append({"name": func.name, "kind": kind,
                    "status": status, "reason": reason})
    for record, init, _self in iter_module_constructors(ast, analyzer):
        key = id(init)
        name = f"{record.name}.__init__"
        if key in ctx.thir_constructors:
            status, reason = "routed", None
        elif is_bodyless_binding(init) or getattr(init, "is_overload_stub", False):
            status, reason = "not_a_candidate", None
        else:
            reason = reasons.get(key)
            status = "fallback" if reason else "not_attempted"
        out.append({"name": name, "kind": "ctor",
                    "status": status, "reason": reason})
    return out


# A body seen by several entry programs takes its WORST sighting: codegen of a
# stdlib module depends on which instantiations the program requests, so a body
# that routes under one program and rejects under another is a real blocker.
_STATUS_RANK = {"fallback": 3, "routed": 2, "not_attempted": 1,
                "not_a_candidate": 0}


def merge_module(merged: dict, mod: str, bodies: list[dict]) -> None:
    slot = merged.setdefault(mod, {})
    for b in bodies:
        prev = slot.get(b["name"])
        if prev is None or _STATUS_RANK[b["status"]] > _STATUS_RANK[prev["status"]]:
            slot[b["name"]] = b


def run_entry(source: str, tmpdir: Path, tag: str) -> tuple[dict, list[str]]:
    """Compile one entry program with THIR forced on for EVERY module."""
    src_file = tmpdir / f"entry_{tag}.py"
    src_file.write_text(source)
    compiler = Compiler(src_file, lib_dirs=[LIB])
    modules = compiler.compile()
    errors: list[str] = []
    per_module: dict[str, list[dict]] = {}
    # THE measurement lever -- the same knob `--thir-stdlib` uses, so the
    # scoping gate keeps ONE definition (compiler.py `_make_codegen`).
    opts = CodeGenOptions(thir_codegen=True, thir_all_modules=True)
    for m in modules:
        if m.is_entry_point:
            continue
        try:
            ctx = compiler.collect_thir(m, opts)
        except Exception as e:  # noqa: BLE001 -- a crash IS the datum
            errors.append(f"{m.name}: {type(e).__name__}: {e}")
            continue
        per_module[m.name] = classify(m, ctx, compiler.thir_reject_by_node)
    return per_module, errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="a5_stdlib_fallback.json")
    ap.add_argument("--modules", default=None,
                    help="comma-separated subset (default: every lib/tpy module)")
    args = ap.parse_args()

    tmpdir = Path(args.out).resolve().parent / "_a5_tmp"
    tmpdir.mkdir(parents=True, exist_ok=True)

    names = (args.modules.split(",") if args.modules else stdlib_module_names())
    skipped = [n for n in names if is_macro_module(n)]
    names = [n for n in names if n not in set(skipped)]

    merged: dict[str, list[dict]] = {}
    entry_errors: dict[str, str] = {}
    module_errors: dict[str, list[str]] = {}
    t0 = time.monotonic()
    for i, name in enumerate(names, 1):
        print(f"[{i}/{len(names)}] {name}", file=sys.stderr, flush=True)
        try:
            per_module, errs = run_entry(f"import {name}\n", tmpdir,
                                         name.replace(".", "_"))
        except Exception as e:  # noqa: BLE001
            entry_errors[name] = f"{type(e).__name__}: {e}"
            traceback.print_exc(limit=3, file=sys.stderr)
            continue
        if errs:
            module_errors[name] = errs
        for mod, bodies in per_module.items():
            merge_module(merged, mod, bodies)

    payload = {
        "elapsed_s": round(time.monotonic() - t0, 1),
        "entries_attempted": len(names),
        "entry_errors": entry_errors,
        "module_errors": module_errors,
        "skipped_macro_modules": skipped,
        "modules": {m: sorted(b.values(), key=lambda x: x["name"])
                    for m, b in merged.items()},
    }
    Path(args.out).write_text(json.dumps(payload, indent=1))

    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    per_mod_fb: dict[str, int] = {}
    for mod, bodies in merged.items():
        for b in bodies.values():
            counts[b["status"]] = counts.get(b["status"], 0) + 1
            if b["status"] == "fallback":
                per_mod_fb[mod] = per_mod_fb.get(mod, 0) + 1
                r = b["reason"] or "unclassified"
                reasons[r] = reasons.get(r, 0) + 1
    print(f"\nmodules: {len(merged)}  " +
          "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())),
          file=sys.stderr)
    print("\ntop reject reasons:", file=sys.stderr)
    for r, c in sorted(reasons.items(), key=lambda kv: -kv[1])[:25]:
        print(f"  {c:5d}  {r}", file=sys.stderr)
    print("\ntop modules by fallback:", file=sys.stderr)
    for m, c in sorted(per_mod_fb.items(), key=lambda kv: -kv[1])[:25]:
        print(f"  {c:5d}  {m}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
