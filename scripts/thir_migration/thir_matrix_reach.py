"""Which CELLS of a THIR admission matrix does the corpus actually witness?

Several THIR predicates are position-enumeration matrices: one widened
argument class (an OR-chain of pointee/member shapes) consulted from many
distinct emit positions. Completeness for those has only ever been shown
by the corpus byte-diff -- and the byte-diff, the move-verdict join and
the binding join are all DUAL-PATH detectors whose AST side disappears at
cutover. Once they are gone nothing can answer "does cell (site, class)
have any witness at all", so the answer has to be measurable on demand
rather than re-derived by hand each time.

The measurement: patch the predicate, record `(calling site, argument
class)` on every TRUE verdict plus a per-site consult count, sweep a
population, print the coverage table.

Read the output as a LOWER BOUND on reachability, never as a proof of
deadness. A cell with zero witnesses means this population never drove
it; a hand-written probe has reached a class the whole corpus missed.
The nominal cross product (static sites x classes) is likewise an UPPER
bound: many pairs are unreachable by construction, and this script has no
way to tell those apart from the untested ones.

Two axes, two different denominators, deliberately:
  * SITES are attributed at runtime by walking the stack to the first
    tpyc frame that is not one of the predicate's own wrapper functions,
    so a wrapper call is charged to the position that made it. The
    denominator is a static `ast` scan for calls to the target or any of
    its wrappers, excluding calls inside those wrappers themselves.
  * CLASSES are the predicate's own OR-chain, labelled by first match in
    the predicate's order (that is what its short circuit does).

CONSULTED and TRUE are different numbers and both are printed. Consults
are counted per SITE only: a FALSE verdict means no class matched, so a
false consult has no cell to be charged to -- there is no such thing as a
"consulted cell" for these predicates.

Adding a matrix: append a `Matrix` to MATRICES with its patch targets and
a classifier that mirrors the predicate's OR-chain. Every requested matrix
rides ONE population sweep, so a second target costs measurement time only
if it needs a different population. The shape only fits a predicate whose
TRUE verdict PARTITIONS its argument -- `_field_markers_clean` does not:
TRUE there means "none of seven markers present", a single class, and the
axis worth measuring is which marker made it FALSE. That is a different
instrument, not a Matrix entry.

Usage (from the repo root); the full sweep is ~25 min at --procs 8, so
iterate with --sample and measure once:
    uv run python scripts/thir_migration/thir_matrix_reach.py \
        --matrix all --population both --procs 8 --json out.json
    ... [--sample N] [--case-filter SUBSTR] [--modules a,b]
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import importlib
import importlib.util
import json
import os
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Callable

REPO = Path(os.environ.get("TPY_REPO") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

# Comprehension and lambda frames are never an emit POSITION -- the
# position is the function that contains them, one frame further up.
_SYNTHETIC_FRAMES = frozenset(
    ("<genexpr>", "<listcomp>", "<setcomp>", "<dictcomp>", "<lambda>"))
_TPYC_MARK = os.sep + "tpyc" + os.sep


# --------------------------------------------------------------------------
# Matrix definitions
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Matrix:
    """One predicate matrix.

    `cell_targets` carry the class axis: their TRUE verdicts are the cells.
    `entry_targets` are the wrappers callers actually name; they are
    patched too, but only to COUNT consults -- a wrapper that rejects
    before reaching the class chain still proves its site executed, and a
    site whose consults come only through a wrapper would otherwise read as
    never reached. Their own TRUE verdicts are not cells: a wrapper TRUE
    implies the accessor TRUE at the same site, so recording both would
    double-count.

    Both sets are transparent for site attribution and both contribute to
    the static site denominator (calling a wrapper reaches the target).
    """
    name: str
    cell_targets: tuple[str, ...]
    entry_targets: tuple[str, ...]
    classes: tuple[str, ...]
    classify: Callable[[tuple, dict], "str | None"]
    note: str

    @property
    def wrappers(self) -> tuple[str, ...]:
        return self.cell_targets + self.entry_targets


def _classify_pointee(args, kwargs) -> "str | None":
    """`_opt_pointee_wide(inner, analyzer)` -- the 10-class pointee chain."""
    from tpyc.thir.lower import predicates as P

    inner = args[0] if args else kwargs.get("inner")
    analyzer = args[1] if len(args) > 1 else kwargs.get("analyzer")
    if inner is None:
        return None
    iu = P.unwrap_readonly(inner)
    if P._f1_record(iu, analyzer):
        return "f1_record"
    if P._wrapper_union_like(iu, analyzer) is not None:
        return "wrapper_union_like"
    if isinstance(iu, P.TypeParamRef):
        return "type_param"
    if isinstance(iu, P.NominalType) and P.is_dyn_protocol(iu):
        return "dyn_protocol"
    if P.is_list(iu):
        return "list"
    if P.is_dict(iu):
        return "dict"
    if P.is_set(iu):
        return "set"
    if P.is_bytearray_type(iu):
        return "bytearray"
    if P._eligible_scalar(iu):
        return "scalar"
    if P._eligible_char(iu):
        return "char"
    return None


def _classify_union_member(args, kwargs) -> "str | None":
    """`_ptr_union_member_wide(m, analyzer)` -- the 9-class member chain."""
    from tpyc.thir.lower import predicates as P

    m = args[0] if args else kwargs.get("m")
    analyzer = args[1] if len(args) > 1 else kwargs.get("analyzer")
    if m is None:
        return None
    if P._f1_record(m, analyzer):
        return "f1_record"
    if P._eligible_scalar(m):
        return "scalar"
    if P.is_void_like_type(m):
        return "void_like"
    mu = P.unwrap_readonly(m)
    if P.is_str_type(mu):
        return "str"
    if P.is_list(mu):
        return "list"
    if P.is_dict(mu):
        return "dict"
    if P.is_set(mu):
        return "set"
    if P._span_value(mu):
        return "span"
    if P._value_tuple(mu, analyzer) is not None:
        return "value_tuple"
    return None


MATRICES: dict[str, Matrix] = {
    "pointee": Matrix(
        name="pointee",
        cell_targets=("_opt_pointee_wide",),
        entry_targets=("_optional_ptr_borrow_wide",
                       "_optional_ptr_borrow_wide_name"),
        classes=("f1_record", "wrapper_union_like", "type_param",
                 "dyn_protocol", "list", "dict", "set", "bytearray",
                 "scalar", "char"),
        classify=_classify_pointee,
        note="widened Optional-pointee class for the RETURN/DECL/COND rows",
    ),
    "union": Matrix(
        name="union",
        cell_targets=("_ptr_union_member_wide",),
        entry_targets=("_eligible_ptr_union_wide",),
        classes=("f1_record", "scalar", "void_like", "str", "list", "dict",
                 "set", "span", "value_tuple"),
        classify=_classify_union_member,
        note="widened ptr-variant union MEMBER class",
    ),
}


# --------------------------------------------------------------------------
# Static site enumeration (the SITE denominator)
# --------------------------------------------------------------------------

def static_sites(matrix: Matrix) -> set[str]:
    """Call sites of the target or any wrapper, outside the wrappers.

    A call inside a wrapper is plumbing, not a position: the site the
    runtime spy attributes for it is the wrapper's own caller.
    """
    names = set(matrix.wrappers)
    sites: set[str] = set()
    for path in sorted((REPO / "tpyc").rglob("*.py")):
        # A unit test calling the predicate is not an emit POSITION, and no
        # population sweep can reach it -- counting it would only inflate
        # the denominator with a permanently-unreachable row.
        if "__pycache__" in path.parts or path.name.startswith("test_"):
            continue
        text = path.read_text(errors="replace")
        if not any(n in text for n in names):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        spans = [(n.lineno, n.end_lineno or n.lineno)
                 for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name in matrix.wrappers]
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            nm = (f.id if isinstance(f, ast.Name)
                  else f.attr if isinstance(f, ast.Attribute) else None)
            if nm not in names:
                continue
            if any(a <= n.lineno <= b for a, b in spans):
                continue
            sites.add(f"{path.name}:{n.lineno}")
    return sites


# --------------------------------------------------------------------------
# The spy
# --------------------------------------------------------------------------

# Keyed by matrix name so one population sweep feeds every matrix: a
# full-corpus pass costs half an hour, and the matrices are independent
# patches over the same compile.
TRUE_CELLS: dict = {}    # matrix -> Counter[(site, class)]
SITE_CONSULTS: dict = {}  # matrix -> Counter[site]
# Guards recording, not the predicates: a classifier calls sibling
# predicates, and a consult raised from inside one would be charged to a
# frame in this script instead of an emit position. Global rather than
# per-matrix, so a nested consult is dropped from every matrix -- an
# undercount, which is the safe direction for a lower bound.
_BUSY = [False]


def _site(wrappers: frozenset) -> "tuple[str, bool]":
    """The emit position that drove this consult, and whether the call came
    through another patched wrapper (which already counted the consult)."""
    f = sys._getframe(2)  # skip _site + the spy
    via_wrapper = False
    while f is not None:
        code = f.f_code
        name = code.co_name
        if name in _SYNTHETIC_FRAMES:
            f = f.f_back
            continue
        if name in wrappers:
            via_wrapper = True
        elif _TPYC_MARK in code.co_filename:
            return f"{Path(code.co_filename).name}:{f.f_lineno}", via_wrapper
        f = f.f_back
    return "<no-tpyc-frame>", via_wrapper


def _make_spy(matrix: Matrix, orig, record_cells: bool):
    wrappers = frozenset(matrix.wrappers)
    classify = matrix.classify
    cells = TRUE_CELLS.setdefault(matrix.name, Counter())
    consults = SITE_CONSULTS.setdefault(matrix.name, Counter())

    def spy(*args, **kwargs):
        verdict = orig(*args, **kwargs)
        if _BUSY[0]:
            return verdict
        site, via_wrapper = _site(wrappers)
        if not via_wrapper:
            consults[site] += 1
        if verdict and record_cells:
            _BUSY[0] = True
            try:
                cls = classify(args, kwargs) or "(unclassified)"
            except Exception:  # noqa: BLE001 -- a classifier crash is a datum
                cls = "(classify-error)"
            finally:
                _BUSY[0] = False
            cells[(site, cls)] += 1
        return verdict

    return spy


_HOLDER_CACHE: dict = {}


def _holder_modules(names: set) -> "dict[str, list]":
    """Per target name, the imported modules holding a copy of it.

    `from ... import _opt_pointee_wide` binds the function into the
    importing module's globals, so patching `predicates` alone leaves most
    call sites pointing at the unpatched original -- and the sweep then
    reports a silent, plausible-looking undercount. The importer set is
    read statically so a module that has not been imported yet is still
    found and imported here.
    """
    key = frozenset(names)
    if key in _HOLDER_CACHE:
        return _HOLDER_CACHE[key]
    holders: dict[str, list] = {n: [] for n in names}
    for path in sorted((REPO / "tpyc").rglob("*.py")):
        if "__pycache__" in path.parts or path.name.startswith("test_"):
            continue
        text = path.read_text(errors="replace")
        # Substring prefilter: parsing every tpyc file costs seconds per
        # worker process, and only a handful mention these names at all.
        if not any(n in text for n in names):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        imported = {a.name for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) for a in n.names}
        wanted = names & imported
        if path.name == "predicates.py":
            wanted = set(names)
        if not wanted:
            continue
        parts = list(path.relative_to(REPO).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        mod = importlib.import_module(".".join(parts))
        for n in wanted:
            holders[n].append(mod)
    _HOLDER_CACHE[key] = holders
    return holders


def install(matrix: Matrix) -> int:
    from tpyc.thir.lower import predicates as P

    holders = _holder_modules(set(matrix.wrappers))
    patched = 0
    for target in matrix.wrappers:
        orig = getattr(P, target)
        if getattr(orig, "_reach_spy", False):
            continue  # already installed in this process
        spy = _make_spy(matrix, orig, target in matrix.cell_targets)
        spy._reach_spy = True
        hit = 0
        for mod in holders[target]:
            if getattr(mod, target, None) is orig:
                setattr(mod, target, spy)
                hit += 1
        if hit != len(holders[target]):
            raise RuntimeError(
                f"{target}: patched {hit} of {len(holders[target])} holder "
                f"modules -- an unpatched copy makes every count an "
                f"unmarked undercount")
        patched += hit
    return patched


# --------------------------------------------------------------------------
# Populations
# --------------------------------------------------------------------------

def corpus_cases(sample: "int | None", case_filter: "str | None" = None):
    import conftest as C

    cases = [(n, str(cd), str(ms)) for (n, cd, ms) in C.discover_cases()
             if case_filter is None or case_filter in n]
    if sample and sample < len(cases):
        # Deterministic stride, not a random draw: two runs of the same
        # --sample must be comparable.
        step = len(cases) / sample
        cases = [cases[int(i * step)] for i in range(sample)]
    return cases


def sweep_case(case_dir: Path, main_src: Path) -> None:
    """Compile one corpus case with THIR on for its USER modules.

    Options are layered exactly as the harness layers them (plugin,
    dsl_opts, default_int, walked up from the case dir): a sweep that
    skips that errors out every plugin and non-default-int case and then
    reports coverage over the survivors as if it were coverage over the
    corpus.
    """
    import conftest as C
    from tpyc.compiler import Compiler
    from tpyc.codegen_cpp import CodeGenOptions
    from tpyc.sema import DiagnosticLevel

    frontend_registry, extra_lib_dirs = C._frontend_registry_for(main_src)
    lib_dirs = list(extra_lib_dirs) + list(C.DEFAULT_LIB_DIRS)
    opts = dataclasses.replace(
        CodeGenOptions(emit_source_comments=True, comment_line_numbers=False),
        thir_codegen=True)

    compiler = Compiler(main_src, default_int=C.get_case_default_int(case_dir),
                        lib_dirs=lib_dirs, frontend_registry=frontend_registry)
    modules = compiler.compile()
    for mod in modules:
        if mod.analyzer and any(d.level == DiagnosticLevel.ERROR
                                for d in mod.analyzer.diagnostics):
            raise _ExpectedError("compile_error")
    src_dir = main_src.parent.resolve()
    for mod in modules:
        try:
            mod.path.resolve().relative_to(src_dir)
        except ValueError:
            continue
        # collect_thir DISCARDS the C++ -- nothing is written next to the
        # case, so a sweep leaves the corpus tree untouched.
        compiler.collect_thir(mod, opts)


class _ExpectedError(Exception):
    """A case the harness itself would not emit (an `error_` case)."""


def _reset(names) -> None:
    # CLEAR in place: the spies close over the Counter objects at install
    # time, so rebinding the dict slots would silently orphan them and
    # every count would read zero.
    for n in names:
        TRUE_CELLS.setdefault(n, Counter()).clear()
        SITE_CONSULTS.setdefault(n, Counter()).clear()


def _worker_init(matrix_names: tuple) -> None:
    for n in matrix_names:
        install(MATRICES[n])


def _worker(args):
    name, case_dir_s, main_src_s = args
    _reset(list(TRUE_CELLS))
    bucket, detail = "ok", ""
    try:
        sweep_case(Path(case_dir_s), Path(main_src_s))
    except Exception as exc:  # noqa: BLE001
        detail = ("compile_error" if isinstance(exc, _ExpectedError)
                  else f"{type(exc).__name__}: {exc}")
        # An `error_` case is SUPPOSED not to emit -- rejecting is its whole
        # point. Counting those as instrument failures inflates the skipped
        # population and hides the real ones behind them.
        bucket = ("expected_reject"
                  if Path(case_dir_s).name.startswith("error_") else "error")
    return (name, bucket, detail,
            {m: {f"{s}\x00{c}": n for (s, c), n in cells.items()}
             for m, cells in TRUE_CELLS.items()},
            {m: dict(c) for m, c in SITE_CONSULTS.items()})


def sweep_corpus(names: list, cases, procs: int) -> dict:
    from multiprocessing import Pool

    statuses: Counter = Counter()
    # An `error_` case that died of something OTHER than a compile
    # diagnostic is an instrument failure wearing the expected-reject
    # bucket's clothes, so the bucket keeps a tally of what killed it.
    reject_kinds: Counter = Counter()
    failures: dict[str, str] = {}
    t0 = time.monotonic()
    with Pool(procs, initializer=_worker_init,
              initargs=(tuple(names),)) as pool:
        for i, (name, bucket, detail, cells, consults) in enumerate(
                pool.imap_unordered(_worker, cases, chunksize=4)):
            statuses[bucket] += 1
            if bucket == "error":
                failures[name] = detail
            elif bucket == "expected_reject":
                reject_kinds[detail.split(":")[0]] += 1
            for m, per in cells.items():
                for k, n in per.items():
                    s, c = k.split("\x00")
                    TRUE_CELLS[m][(s, c)] += n
            for m, per in consults.items():
                for s, n in per.items():
                    SITE_CONSULTS[m][s] += n
            if (i + 1) % 500 == 0:
                print(f"  corpus {i + 1}/{len(cases)} "
                      f"({round(time.monotonic() - t0)}s)", file=sys.stderr,
                      flush=True)
    return {"attempted": len(cases), "statuses": dict(statuses),
            "failures": failures, "reject_kinds": dict(reject_kinds),
            "elapsed_s": round(time.monotonic() - t0, 1)}


def sweep_stdlib(names: list, modules: "list[str] | None") -> dict:
    """The stdlib half, over `thir_stdlib_fallback`'s entry-per-module sweep.

    This population is not optional colour: at cutover the stdlib is the
    only THIR consumer left that still has fallback bodies, so a cell only
    the corpus reaches is a cell that loses its witness with the cases.
    """
    spec = importlib.util.spec_from_file_location(
        "tpy_thir_stdlib_fallback",
        REPO / "scripts" / "thir_migration" / "thir_stdlib_fallback.py")
    sweep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sweep)

    for n in names:
        install(MATRICES[n])
    mods = modules or sweep.stdlib_module_names()
    mods = [m for m in mods if not sweep.is_macro_module(m)]
    tmp = REPO / "__tpyc__" / "_reach_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    ok = errs = 0
    failures: dict[str, str] = {}
    t0 = time.monotonic()
    for i, mod in enumerate(mods, 1):
        print(f"  stdlib {i}/{len(mods)} {mod}", file=sys.stderr, flush=True)
        try:
            sweep.run_entry(f"import {mod}\n", tmp, mod.replace(".", "_"))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            errs += 1
            failures[mod] = f"{type(exc).__name__}: {exc}"
    return {"attempted": len(mods), "statuses": {"ok": ok, "error": errs},
            "failures": failures,
            "elapsed_s": round(time.monotonic() - t0, 1)}


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def report(matrix: Matrix, sites: set[str], pops: dict) -> dict:
    cells_counter = TRUE_CELLS[matrix.name]
    consults_counter = SITE_CONSULTS[matrix.name]
    reached_cells = set(cells_counter)
    reached_sites = {s for (s, _) in reached_cells}
    consulted = set(consults_counter)
    reached_classes = {c for (_, c) in reached_cells}
    unknown = sorted(consulted - sites)

    all_sites = sorted(sites | consulted)
    nominal = len(sites) * len(matrix.classes)

    print(f"\n=== matrix: {matrix.name} -- {matrix.note} ===")
    for pop, info in pops.items():
        # Cumulative, because the populations run in sequence into one set
        # of counters: what matters is what each population ADDS, since a
        # cell only one of them reaches has exactly one witness.
        cum = info.get("cells_cumulative", {}).get(matrix.name)
        print(f"population {pop}: {info['attempted']} attempted  "
              + "  ".join(f"{k}={v}" for k, v in sorted(
                  info["statuses"].items()))
              + f"  ({info['elapsed_s']}s)"
              + (f"  cells after: {cum}" if cum is not None else ""))
        if info.get("reject_kinds"):
            print("  expected_reject by exception: "
                  + "  ".join(f"{k}={v}" for k, v in
                              sorted(info["reject_kinds"].items(),
                                     key=lambda kv: -kv[1])))

    width = max(len(s) for s in all_sites) if all_sites else 4
    print("\n" + f"{'site':<{width}} {'consults':>8} "
          + " ".join(f"{c[:9]:>9}" for c in matrix.classes))
    for s in all_sites:
        n = consults_counter.get(s, 0)
        row = " ".join(f"{cells_counter.get((s, c), 0) or '.':>9}"
                       for c in matrix.classes)
        print(f"{s:<{width}} {n:>8} {row}")

    print(f"\ncells with >=1 TRUE : {len(reached_cells)} of {nominal} nominal "
          f"({len(sites)} static sites x {len(matrix.classes)} classes)")
    # A cell whose whole evidence is one TRUE verdict is one edit to one
    # case away from having none -- the same exposure as a zero cell, just
    # not yet realized.
    once = sum(1 for n in cells_counter.values() if n == 1)
    print(f"cells witnessed ONCE: {once} of {len(reached_cells)} reached")
    print(f"sites consulted     : {len(consulted & sites)} of {len(sites)}")
    print(f"sites with >=1 TRUE : {len(reached_sites & sites)} of {len(sites)}")
    print(f"classes with >=1 TRUE: {len(reached_classes & set(matrix.classes))}"
          f" of {len(matrix.classes)}")
    zero_classes = [c for c in matrix.classes if c not in reached_classes]
    print(f"classes NEVER true  : {zero_classes or '(none)'}")
    print(f"sites never consulted: {sorted(sites - consulted) or '(none)'}")
    if unknown:
        # A runtime site the static scan did not produce means the two
        # denominators disagree (a multi-line call whose frame line is not
        # the Call node's line, or a caller the scan cannot see).
        print(f"sites seen but not statically enumerated: {unknown}")
    print("\nCells reached is a LOWER BOUND (this population, not the "
          "predicate's reachable set);\nnominal is an UPPER bound (many "
          "pairs are unreachable by construction).")

    return {
        "matrix": matrix.name,
        "populations": pops,
        "static_sites": sorted(sites),
        "nominal_cells": nominal,
        "cells_true": len(reached_cells),
        "cells_witnessed_once": once,
        "sites_consulted": len(consulted & sites),
        "sites_true": len(reached_sites & sites),
        "classes_true": sorted(reached_classes),
        "classes_never_true": zero_classes,
        "sites_never_consulted": sorted(sites - consulted),
        "sites_unmatched": unknown,
        "cells": {f"{s}|{c}": n
                  for (s, c), n in sorted(cells_counter.items())},
        "consults": dict(sorted(consults_counter.items())),
    }


def _snapshot(info: dict, names) -> None:
    info["cells_cumulative"] = {n: len(TRUE_CELLS[n]) for n in names}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default="pointee",
                    choices=sorted(MATRICES) + ["all"])
    ap.add_argument("--population", default="corpus",
                    choices=["corpus", "stdlib", "both"])
    ap.add_argument("--sample", type=int, default=None,
                    help="stride-sample N corpus cases instead of all")
    ap.add_argument("--case-filter", default=None,
                    help="substring on the case name (audits a slice of the "
                         "population without re-sweeping the corpus)")
    ap.add_argument("--modules", default=None,
                    help="comma-separated stdlib subset")
    ap.add_argument("--procs", type=int,
                    default=int(os.environ.get("REACH_PROCS", "8")))
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    names = sorted(MATRICES) if args.matrix == "all" else [args.matrix]
    _reset(names)
    # ONE sweep of the population feeds every requested matrix; the
    # per-matrix split happens in the spies, not in the population loop.
    pops: dict = {}
    if args.population in ("corpus", "both"):
        pops["corpus"] = sweep_corpus(
            names, corpus_cases(args.sample, args.case_filter), args.procs)
        _snapshot(pops["corpus"], names)
    if args.population in ("stdlib", "both"):
        mods = args.modules.split(",") if args.modules else None
        pops["stdlib"] = sweep_stdlib(names, mods)
        _snapshot(pops["stdlib"], names)

    out = [report(MATRICES[n], static_sites(MATRICES[n]), pops)
           for n in names]

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=1))
        print(f"\njson: {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        traceback.print_exc(limit=2)
        raise SystemExit(130)
