"""The binding table's shadow count: where what the lowering knows of a
name apart from the table (the walk's `declared`, the closure locals, the
read's own type, the parameter list, the prescan) disagrees with the
binding record, and the record population the planner builds.

TODO.md "One conversion boundary for every value sink", the per-function
binding table. Every lowered body plans its table and reads the per-name
representation facts it models from it. This script compiles every
non-error, non-plugin case under `tests/cases/` in-process, runs codegen for
the entry module and the other modules under the case's `src/` (the stdlib
too for the stdlib render case) with `tpyc.thir.lower.bindings.SHADOW_SINK`
set, then prints the disagreements by class with examples and the record
population. No C++ is built. A manual instrument: minutes, never part of
the pytest run.

    uv run python scripts/thir_migration/review/binding_shadow.py \\
        [--jobs 4] [-k SUBSTR,...] [--every N] [--examples 5]
        [--json OUT.json] [--verify-cpp]

`-k` keeps the cases whose `main.py` path contains any of the
comma-separated substrings; `--every N` keeps every Nth case; `--examples`
caps the examples printed per class; `--json` dumps the classes and the
population.

Classes: `held_type:...` where the record's held under its declared type
differs from its held under the read's type (a narrowed read is held as its
occurrence and is not counted, nor is a str / bytes read whose view / owned
resolution differs from its declaration's, which the `view_axis_split`
statistic counts); `read:const:...` the stamped const against the record's
or the reading function's parameter verdict; `binding:<field>` the record
`binding_of` hands a conversion against the parameter list and the
prescan; `unplanned` a read of a name the walk has in `declared` with no
record; `wrong_scope:*` a record outside `declared` or a nested def's name
outside the closure locals; `cursor_miss:*` a key the planner never saw
(the walk then stops with an internal error); `scope_unentered:<Kind>` a
scope the planner keyed that the walk never entered, and
`scope_unentered_cfg:<Kind>` one a resumable body's CFG decomposes (its
leaves enter their own keys).
The population: records planned, and records per classifier row.
`--verify-cpp` generates every surveyed module twice, with and without the
shadow check, and reports `cpp_differs` where the C++ is not
byte-identical.
"""
from __future__ import annotations

import argparse
import collections
import json
import multiprocessing as mp
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
LIB = REPO / "lib" / "tpy"
STDLIB_CASE = "harness/stdlib_render"


def corpus() -> tuple[list[Path], int, int]:
    mains, n_err, n_plugin = [], 0, 0
    for p in sorted((REPO / "tests" / "cases").glob("**/src/main.py")):
        case = p.parent.parent
        if case.name.startswith("error_"):
            n_err += 1
            continue
        plugin = False
        d = case
        while d != REPO / "tests":
            o = d / "options.json"
            if o.exists():
                try:
                    if json.loads(o.read_text()).get("plugin"):
                        plugin = True
                except (OSError, ValueError):
                    pass
            d = d.parent
        if plugin:
            n_plugin += 1
            continue
        mains.append(p)
    return mains, n_err, n_plugin


def _default_int(case: Path) -> str:
    out = "int32"
    d = case
    chain = []
    while d != REPO / "tests":
        chain.append(d)
        d = d.parent
    for d in reversed(chain):
        o = d / "options.json"
        if o.exists():
            try:
                out = json.loads(o.read_text()).get("default_int", out)
            except (OSError, ValueError):
                pass
    return out


VERIFY_CPP = False


def run_case(path_s: str) -> dict:
    from tpyc.codegen_cpp import CodeGenOptions
    from tpyc.compiler import Compiler
    from tpyc.diagnostics import DiagnosticLevel
    from tpyc.thir.lower import bindings
    path = Path(path_s)
    case = path.parent.parent
    rel = str(case.relative_to(REPO / "tests" / "cases"))
    res = {"case": rel, "status": "ok", "entries": []}
    bindings.SHADOW_SINK = []
    try:
        compiler = Compiler(path, default_int=_default_int(case),
                            lib_dirs=[LIB])
        modules = compiler.compile()
        if any(d.level == DiagnosticLevel.ERROR
               for d in compiler.diagnostics):
            res["status"] = "skip:diag"
            return res
        src = path.parent.resolve()
        for mod in modules:
            mine = mod.is_entry_point
            if not mine:
                try:
                    Path(mod.path).resolve().relative_to(src)
                    mine = True
                except (ValueError, TypeError):
                    mine = rel == STDLIB_CASE
            if not mine or mod.analyzer is None:
                continue
            try:
                if VERIFY_CPP:
                    # The check decides nothing: the module's C++ with the
                    # collector set must equal the C++ without it.
                    sink = bindings.SHADOW_SINK
                    bindings.SHADOW_SINK = None
                    try:
                        plain = compiler.generate_inl_and_code_to_strings(mod)
                    finally:
                        bindings.SHADOW_SINK = sink
                    shadowed = compiler.generate_inl_and_code_to_strings(mod)
                    if plain != shadowed:
                        res["entries"].append(
                            ("cpp_differs", mod.name, "", "", ""))
                    continue
                compiler.collect_thir(mod, CodeGenOptions(),
                                      tolerate_reject=True)
            except Exception as ex:  # noqa: BLE001 -- counted, not fatal
                res["entries"].append(
                    ("codegen_crash:" + type(ex).__name__, mod.name, "", "",
                     str(ex).split("\n")[0][:160]))
    except Exception as ex:  # noqa: BLE001 -- a front-end failure skips
        res["status"] = "skip:" + type(ex).__name__
        return res
    finally:
        entries = bindings.SHADOW_SINK or []
        bindings.SHADOW_SINK = None
    res["entries"] += [list(e) for e in entries]
    return res


def report(results: list[dict], n_examples: int) -> dict:
    ok = [r for r in results if r["status"] == "ok"]
    skipped = collections.Counter(r["status"] for r in results
                                  if r["status"] != "ok")
    stats = collections.Counter()
    by_class: dict[str, list] = collections.defaultdict(list)
    progs: dict[str, set] = collections.defaultdict(set)
    sites: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    rows = collections.Counter()
    for r in ok:
        for cls, fn, name, loc, detail in r["entries"]:
            if cls.startswith("_stat:"):
                stats[cls[6:]] += 1
                continue
            if cls.startswith("_row:"):
                rows[cls[5:]] += 1
                continue
            by_class[cls].append((r["case"], fn, name, loc, detail))
            progs[cls].add(r["case"])
            if cls.startswith("write:"):
                sites[cls][detail.split(" ")[0]] += 1
    print(f"programs compiled {len(ok)}, skipped {sum(skipped.values())} "
          f"{dict(skipped)}")
    print(f"bodies with a table {stats['functions']}, name reads checked "
          f"{stats['reads']}")
    for k in sorted(stats.keys() - {"functions", "reads", "records"}):
        print(f"  {k}: {stats[k]}")
    def count(prefix: str) -> int:
        return sum(len(v) for k, v in by_class.items()
                   if k == prefix or k.startswith(prefix + ":"))
    print(f"records {stats['records']} from {len(rows)} classifier rows "
          f"(top: {', '.join(f'{k} {v}' for k, v in rows.most_common(8))}); "
          f"unplanned {count('unplanned')}, cursor_miss "
          f"{count('cursor_miss')}, scope_unentered "
          f"{count('scope_unentered')} (cfg "
          f"{count('scope_unentered_cfg')})")
    total = sum(len(v) for k, v in by_class.items())
    print(f"disagreement entries {total} in {len(by_class)} classes\n")
    order = sorted(by_class, key=lambda k: (-len(by_class[k]), k))
    for cls in order:
        items = by_class[cls]
        print(f"{len(items):7d}  {cls}  ({len(progs[cls])} programs)")
        if cls in sites:
            for site, n in sites[cls].most_common(6):
                print(f"           writer {site}: {n}")
        for ex in items[:n_examples]:
            print("           e.g.", " | ".join(str(x) for x in ex))
    return {"programs": len(ok), "skipped": dict(skipped),
            "stats": dict(stats), "rows": dict(rows),
            "classes": {k: {"count": len(v), "programs": len(progs[k]),
                            "writers": dict(sites.get(k, {})),
                            "examples": v[:n_examples]}
                        for k, v in by_class.items()}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--examples", type=int, default=5)
    ap.add_argument("-k", default="",
                    help="comma-separated path substrings; a case matching "
                         "any of them runs")
    ap.add_argument("--json", default="")
    ap.add_argument("--verify-cpp", action="store_true",
                    help="also generate each module's C++ with and without "
                         "the shadow check and report any difference")
    args = ap.parse_args()
    global VERIFY_CPP
    VERIFY_CPP = args.verify_cpp
    mains, n_err, n_plugin = corpus()
    keys = [k for k in args.k.split(",") if k] or [""]
    mains = [m for m in mains if any(k in str(m) for k in keys)][::args.every]
    print(f"corpus: {len(mains)} programs (excluded error_={n_err}, "
          f"plugin={n_plugin}, every={args.every})", flush=True)
    os.environ.setdefault("CCACHE_DISABLE", "1")
    # Imported before the pool forks, so a worker runs the compiler as it
    # was when the run started even if the tree is edited meanwhile.
    import tpyc.compiler  # noqa: F401
    import tpyc.thir.lower.statements  # noqa: F401
    results = []
    with mp.get_context("fork").Pool(args.jobs, maxtasksperchild=40) as pool:
        for i, r in enumerate(pool.imap_unordered(
                run_case, [str(m) for m in mains], chunksize=4)):
            results.append(r)
            if i % 500 == 0:
                print(f"  {i}/{len(mains)}", flush=True)
    summary = report(results, args.examples)
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
