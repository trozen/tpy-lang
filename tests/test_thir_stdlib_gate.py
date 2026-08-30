"""Pre-merge gate over lib/tpy: THIR/AST byte-identity and the fallback ratchet.

Both properties come from ONE mega-entry compile (~6s), which is the whole point
of the file: measured separately they cost ~6s + ~64s, and the expensive half
only ever ran in a nightly row.

The library's committed RENDER oracle is NOT here -- it is an ordinary test
case, tests/cases/harness/stdlib_render, which imports every non-macro module
and snapshots them all through options.json. What this file adds is the
AST-vs-THIR diff: two authors checking each other, which goes away with the AST
body emitter. The case's snapshot outlives it, and is what the library's render
is checked against from then on.

*Complementary to the wide overlay, not superseded by it.* The default corpus run
also routes lib/tpy through THIR per case and diffs it (conftest's stdlib
oracle), which reaches the generic monomorphizations and resumable frames a bare
`import` never instantiates -- but only for the stdlib modules some case actually
IMPORTS, and only at the option sets those cases run. This gate covers EVERY
module under lib/tpy including any nothing imports yet, in one fast item rather
than smeared across the corpus. Neither contains the other.

Coverage caveats, per property. DIVERGENCE here is a FLOOR twice over: one entry
program instantiates almost nothing (generics and resumable frames only lower at
emission time), and the sweep compiles at ONE option set -- `default_int` is
compilation-wide and reaches every stdlib module's sema, so an option-gated
divergence is invisible here even though BigInt-narrow selection is exactly the
bug class that motivated this gate. The first half reads PESSIMISTIC where it
has been measured: over ten stdlib-heavy corpus cases, 63 library file emissions
differed from this sweep's and NONE differed outside the include block, because
lib/tpy's generics and resumable frames lower to C++ templates in their defining
module. The second half is REAL and was once claimed away: the same emission over
Int32/Int64/BigInt is NOT byte-identical. Measured 2026-08-30 over the mega-entry
at each option (population held equal per comparison, since most of the library
does not compile at all above Int32): 164 file compares, 153 identical, 11
differing across 7 modules -- `collections.hpp` alone turns `int32_t i = 0` into
`int64_t i = 0`. So everything committed at Int32, here and in the case, says
nothing about the other two widths. FALLBACK is not a floor: `iter_module_callables`
attempts each callable once per module, so the body population cannot grow with
instantiation count -- two independent corpus sweeps (495 cases; 90 entry
programs) added zero bodies over the import-only sweep.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import json
import os
import shlex
import time
from pathlib import Path

import pytest

from conftest import (
    TEST_CODEGEN_OPTIONS,
    _format_unified_diff,
    enclosing_function,
    first_divergent_line,
    module_to_expected_path,
)

from tpyc import get_lib_dir
from tpyc.compiler import Compiler
# The two table modules are imported for their SIDE EFFECT: a family exists
# only once its `register_sink` call has run. conftest already pulls both in
# transitively, but the expected set below is derived from the registry, so
# an empty one would assert nothing -- naming them here keeps this gate from
# depending on somebody else's import graph.
from tpyc.thir.lower import arg_table, checks, expressions  # noqa: F401

LIB_TPY = get_lib_dir() / "tpy"

# The case that commits the library's render. It compiles what this gate
# compiles, so its import list is the coverage and the guard below pins it.
RENDER_CASE_MAIN = (Path(__file__).resolve().parent / "cases" / "harness"
                    / "stdlib_render" / "src" / "main.py")

# The families the arg table has sinks for, snapshotted at COLLECTION time so
# a unit test registering a throwaway sink (tpyc/thir/test_arg_table.py has
# three) cannot join the set the reach floor demands. Derived from the
# registry rather than listed, so a family a later sink adds is covered
# without editing anything here.
THIR_ARG_FAMILIES = arg_table.registered_families()
THIR_ARG_CELLS = arg_table.registered_cells()

# The routing classifier lives with the standalone sweep script rather than
# being copied here: a new lowering kind must not be able to be taught to one
# reader and not the other.
_FALLBACK_SCRIPT = (Path(__file__).resolve().parent.parent / "scripts"
                    / "thir_migration" / "thir_stdlib_fallback.py")
_fb_spec = importlib.util.spec_from_file_location("tpy_thir_stdlib_fallback",
                                                  _FALLBACK_SCRIPT)
fallback_sweep = importlib.util.module_from_spec(_fb_spec)
_fb_spec.loader.exec_module(fallback_sweep)

# Self-check floors. A past harness in this repo reported "IDENTICAL" having
# compared ZERO files, because its path mapping silently found no counterpart.
# These are deliberately hard-coded well below the measured values (88 modules /
# 110 file compares / ~1.37 MB as of 2026-08-24) rather than tracking them, so
# they never need touching -- but a refactor that stops comparing fails loudly.
MIN_MODULES = 80
MIN_COMPARES = 100
MIN_EMITTING_MODULES = 50
MIN_BYTES = 500_000
# Detection sanity: macro modules run under CPython at compile time and emit no
# C++ at all, so they are excluded from the sweep. Over-exclusion is already
# caught by MIN_MODULES; this catches the mirror failure of a detector that
# suddenly classifies nothing (or everything) as a macro module.
MAX_MACRO_MODULES = 20

# The fallback ratchet: stdlib bodies THIR cannot lower. EXCEEDING this fails;
# beating it passes, so routing progress never needs a config edit (lowering the
# number is a deliberate, reviewed one). Measured 2026-08-30 -- at ZERO: every
# stdlib body routes, so any new fallback is a regression, not a backlog item.
#
# Counted per BODY, unlike the standalone sweep: that script merges its
# per-entry results with `merge_module`, which keys on the BARE body name, so
# every same-named body in a module (overloads, one method name across several
# records) collapses to its worst sighting. Deleting the AST body emitter needs
# each BODY routed, not each distinct name, so this counts them. The two
# numbers coincide whenever no same-named fallback pair survives -- they are
# still different keys, and the nightly pin below is derived, never copied.
MAX_FALLBACK_BODIES = 0
# The ratchet's blind spot: a sweep that stops classifying reports FEWER
# fallbacks and so reads as progress. Assert on the work done, not just on the
# number -- same guard, same reason, as the script's MIN_MODULES_MEASURED.
# Measured 2026-08-30: 2843 bodies, 1245 routed over 88 body-bearing modules.
# Re-measure and re-arm BOTH numbers with the ceiling -- a floor left behind
# while the ceiling drops is slack, and the two must move by the same amount.
#
# The routed floor is a RATCHET at the measured value, not slack: left loose it
# drifts behind, and a floor hundreds of bodies below the truth cannot catch the
# regression it exists for. Re-arm it with the fallback ceiling, not after.
MIN_BODIES_CLASSIFIED = 2400
MIN_ROUTED_BODIES = 1245
MIN_CLASSIFIED_MODULES = 70

# Arg-table reach floor: the registry is the expected set, so this is only a
# self-check that the set was snapshotted after the sinks registered. Well
# below the 12 families the fold produced, and never needs touching.
MIN_ARG_FAMILIES = 10

MAX_REPORTED_DIVERGENCES = 10
DIFF_CONTEXT = 2

_NIGHTLY_CONFIGS = (Path(__file__).resolve().parent.parent / "ci" / "nightly"
                    / "configs.json")


def _nightly_max_fallback() -> int:
    """The `--max-fallback` threshold the nightly `thir-stdlib-fallback` row
    arms the standalone sweep with."""
    rows = json.loads(_NIGHTLY_CONFIGS.read_text())["configs"]
    row = next(c for c in rows if c["name"] == "thir-stdlib-fallback")
    argv = shlex.split(row["script"])
    return int(argv[argv.index("--max-fallback") + 1])


def _collapsed(per_module: dict[str, list[dict]]) -> tuple[int, int]:
    """The same population under the SWEEP SCRIPT's counting key: (fallback
    bodies, bodies of any status).

    `merge_module` keys on the bare body name within a module, so overloads and
    a method name shared across records collapse to one entry. Reusing the
    script's own function (rather than reimplementing the collapse) keeps the
    two counts derived from one definition of the key."""
    collapsed: dict[str, dict] = {}
    for mod, bodies in per_module.items():
        fallback_sweep.merge_module(collapsed, mod, bodies)
    rows = [b for bodies in collapsed.values() for b in bodies.values()]
    return sum(1 for b in rows if b["status"] == "fallback"), len(rows)


def _lib_module_names() -> list[str]:
    """Every importable module name under lib/tpy, package dirs included."""
    names: list[str] = []
    for path in sorted(LIB_TPY.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(LIB_TPY).parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][: -len(".py")]
        if parts:
            names.append(".".join(parts))
    return names


def _is_macro_module(name: str) -> bool:
    path = LIB_TPY / (name.replace(".", "/") + ".py")
    if not path.exists():
        path = LIB_TPY / (name.replace(".", "/") + "/__init__.py")
    if not path.exists():
        return False
    # Directives live in the header comment block; the parser scans the same way.
    return "macro_module" in path.read_text(errors="replace")[:4000]


def _render_divergences(rows: list[tuple[str, str]], heading: str,
                        noun: str) -> str:
    shown = rows[:MAX_REPORTED_DIVERGENCES]
    body = "\n\n".join(f"=== {where}\n{diff}" for where, diff in shown)
    more = (f"\n\n... and {len(rows) - len(shown)} more {noun}"
            if len(rows) > len(shown) else "")
    return f"{heading}\n\n{body}{more}"


def _label(text: str, other: str) -> str:
    """Name the function enclosing the first divergent hunk, as the corpus
    divergence reporter does -- a bare line number in 30k lines of generated
    C++ is not actionable."""
    idx = first_divergent_line(other, text)
    if idx is None:
        return "?"
    return enclosing_function(text.splitlines(), idx) or "<module scope>"


def _fallback_report(per_module: dict[str, list[dict]]) -> str:
    """Where the fallback lives, so a ratchet failure is actionable without
    re-running the standalone sweep."""
    reasons: dict[str, int] = {}
    per_mod: dict[str, int] = {}
    for mod, bodies in per_module.items():
        for b in bodies:
            if b["status"] != "fallback":
                continue
            per_mod[mod] = per_mod.get(mod, 0) + 1
            r = b["reason"] or "unclassified"
            reasons[r] = reasons.get(r, 0) + 1
    lines = ["top reject reasons:"]
    lines += [f"  {c:5d}  {r}"
              for r, c in sorted(reasons.items(), key=lambda kv: -kv[1])[:15]]
    lines.append("top modules by fallback:")
    lines += [f"  {c:5d}  {m}"
              for m, c in sorted(per_mod.items(), key=lambda kv: -kv[1])[:15]]
    return "\n".join(lines)


def _assert_every_family_reached(compiler: Compiler) -> None:
    """Every registered arg-table family is REACHED by the stdlib sweep.

    Not the fold's audit join, which compared the table against the ladders
    it replaced and is gone with them. That join proved a cell decides what
    its ladder decided; this proves anything ever asks. Different questions,
    and the second has no other reliable answer: an unreached family's
    bodies fall back, a fallback emits the AST's own bytes, so the byte-diff
    stays silent; the fallback ratchet notices only while it happens to
    carry no slack; and only a minority of the table's cells carry a face,
    so the zero-witness census covers some rows and no family as a whole.

    Assert on the sweep that reaches all of them -- ~2800 stdlib bodies
    through every callee shape the library uses, which no single corpus case
    does. Non-zero, never a fixed count: the population moves with every
    routing change. The family SET comes from the sink registry, so a step
    that adds a sink is covered by this gate without touching it -- and if
    the stdlib genuinely cannot reach a newly folded family, that is the gate
    saying the family has no witness, which is the thing worth knowing.
    """
    assert len(THIR_ARG_FAMILIES) >= MIN_ARG_FAMILIES, (
        f"only {len(THIR_ARG_FAMILIES)} arg-table families registered "
        f"(expected >= {MIN_ARG_FAMILIES}) -- the snapshot was taken before "
        f"the sink modules were imported, so this gate asserts nothing")
    reached = arg_table.reached_families(compiler)
    missing = [f for f in THIR_ARG_FAMILIES if f not in reached]
    assert not missing, (
        f"the arg table's {', '.join(missing)} family/families decided ZERO "
        f"arguments over the whole stdlib sweep -- nothing dispatches to "
        f"them here, so their cells are unexercised and an over- or "
        f"under-admission in them has nothing pointed at it.\n"
        f"Reached: {sorted(reached)}\n"
        f"Check the gate that selects the sink still routes to it "
        f"(tpyc/thir/lower/checks.py, tpyc/thir/lower/expressions.py).")


def _compile_lib_tpy(tmp_path: Path) -> tuple[Compiler, list]:
    """Compile every non-macro lib/tpy module from ONE entry that imports them
    all: the dependency graph is walked once (~6s instead of ~73s) and each
    module is emitted with the union of the instantiations its siblings
    request, not just its own."""
    all_names = _lib_module_names()
    macro_names = [n for n in all_names if _is_macro_module(n)]
    names = [n for n in all_names if n not in set(macro_names)]
    assert 0 < len(macro_names) <= MAX_MACRO_MODULES, (
        f"macro_module detection returned {len(macro_names)} of "
        f"{len(all_names)} modules -- the detector is broken, not the stdlib"
    )
    entry = tmp_path / "thir_stdlib_gate_entry.py"
    entry.write_text("".join(f"import {n}\n" for n in names))
    compiler = Compiler(entry, lib_dirs=[LIB_TPY])
    compiled = compiler.compile()
    # A cycle peer also emits a `<mod>_fwd.hpp`, which generate_code_to_strings
    # has no slot for -- it would go unsnapshotted and undiffed in silence. No
    # lib/tpy module is one today; if that changes, this must grow a third
    # artifact rather than quietly stop covering it.
    assert not compiler._cycle_peers, (
        f"lib/tpy now has import cycles ({sorted(compiler._cycle_peers)}); "
        f"their _fwd.hpp headers are outside everything this gate compares")
    return compiler, compiled


def test_stdlib_thir_matches_ast(request: pytest.FixtureRequest,
                                 tmp_path: Path) -> None:
    """Emit every non-macro lib/tpy module through both codegen paths from one
    entry program; byte-diff THIR against the AST and ratchet the fallback
    count.

    Both properties ride the same emissions -- the ctx is what codegen actually
    used, so the routing classified here is the routing that produced the C++
    compared here.
    """
    if request.config.getoption("--no-thir"):
        pytest.skip("--no-thir disables THIR entirely")
    if (request.config.getoption("--update-snapshots")
            or os.environ.get("UPDATE_EXPECTED", "").lower() in ("1", "true")):
        # Same rule --thir-stdlib follows (conftest `_thir_flag_conflict`):
        # snapshots are AST-authored, so THIR does not run in update mode.
        pytest.skip("--update-snapshots authors snapshots from the AST path")

    t0 = time.monotonic()
    compiler, compiled = _compile_lib_tpy(tmp_path)

    ast_opts = dataclasses.replace(TEST_CODEGEN_OPTIONS, thir_codegen=False)
    # thir_all_modules lifts the user-module scoping gate (compiler.py
    # `_make_codegen`) -- the same knob --thir-stdlib uses, so routing keeps one
    # definition.
    thir_opts = dataclasses.replace(TEST_CODEGEN_OPTIONS, thir_codegen=True,
                                    thir_all_modules=True)

    modules = compares = emitting = total_bytes = 0
    divergences: list[tuple[str, str]] = []
    bodies_by_module: dict[str, list[dict]] = {}
    for mod in compiled:
        if mod.is_entry_point:
            continue
        modules += 1
        try:
            ast_out = compiler.generate_code_to_strings(mod, ast_opts)
            thir_out, thir_ctx = compiler.generate_code_and_thir(mod, thir_opts)
        except Exception as exc:  # noqa: BLE001
            # Lowering must raise ThirUnsupported and fall back; anything else
            # escaping is a defect, and naming the module beats one aborted
            # sweep with a bare traceback.
            divergences.append((f"{mod.name} (codegen crashed)",
                                f"{type(exc).__name__}: {exc}"))
            continue
        bodies_by_module[mod.name] = fallback_sweep.classify(
            mod, thir_ctx, compiler.thir_reject_by_node)
        emitted = False
        for i, ext in ((0, ".hpp"), (1, ".cpp")):
            ast_src, thir_src = ast_out[i], thir_out[i]
            if not ast_src and not thir_src:
                # Declaration-only (`native_module`) modules emit nothing --
                # not a missing counterpart, so neither a compare nor a
                # snapshot file. The set comparison below is what keeps that
                # absence from also excusing a file the tree is missing.
                continue
            compares += 1
            emitted = True
            total_bytes += len(ast_src)
            if ast_src != thir_src:
                diff = _format_unified_diff(
                    ast_src, thir_src, fromfile=f"{mod.name}{ext} (AST oracle)",
                    tofile=f"{mod.name}{ext} (THIR)", context=DIFF_CONTEXT)
                divergences.append(
                    (f"{mod.name}{ext} in {_label(thir_src, ast_src)}", diff))
        emitting += emitted
    elapsed = time.monotonic() - t0

    status_counts: dict[str, int] = {}
    for bodies in bodies_by_module.values():
        for b in bodies:
            status_counts[b["status"]] = status_counts.get(b["status"], 0) + 1
    fallback = status_counts.get("fallback", 0)
    routed = status_counts.get("routed", 0)
    classified = sum(status_counts.values())
    with_bodies = sum(1 for b in bodies_by_module.values() if b)

    ratchet_problem = None
    if fallback > MAX_FALLBACK_BODIES:
        ratchet_problem = (
            f"THIR stdlib fallback REGRESSED: {fallback} bodies fall back, "
            f"max {MAX_FALLBACK_BODIES} (of {classified} classified: "
            f"{routed} routed, "
            f"{status_counts.get('not_attempted', 0)} not-attempted, "
            f"{status_counts.get('not_a_candidate', 0)} not-a-candidate).\n"
            f"Deleting the AST body emitter needs this at zero, so it may go "
            f"down but never up. Full per-body JSON: uv run python "
            f"scripts/thir_migration/thir_stdlib_fallback.py\n\n"
            f"{_fallback_report(bodies_by_module)}")

    if divergences:
        # Everything the run learned goes in one report: the two properties are
        # independent, and reporting only the first would cost a second 6s
        # round-trip to discover the other.
        parts = [_render_divergences(
            divergences,
            f"THIR/AST stdlib divergence: {len(divergences)} problem(s) "
            f"over {compares} generated file(s) in {modules} lib/tpy "
            f"modules.\nThe AST path is the oracle: fix THIR lowering, "
            f"not the snapshot.\nReproduce the wide gate with: uv run "
            f"pytest --no-exec (the stdlib oracle is on by default)",
            "diverging file(s)")]
        if ratchet_problem:
            parts.append(f"ALSO: {ratchet_problem}")
        pytest.fail("\n\n########\n\n".join(parts), pytrace=False)

    # Self-check: the assertions above are vacuously true if nothing was
    # compared, so the gate must prove it did work.
    assert modules >= MIN_MODULES, (
        f"only {modules} lib/tpy modules compiled (expected >= {MIN_MODULES}) "
        f"-- the sweep stopped covering the stdlib")
    assert compares >= MIN_COMPARES, (
        f"only {compares} generated files byte-compared "
        f"(expected >= {MIN_COMPARES})")
    assert emitting >= MIN_EMITTING_MODULES, (
        f"only {emitting} modules emitted any C++ (expected >= "
        f"{MIN_EMITTING_MODULES}) -- codegen is returning empty output")
    assert total_bytes >= MIN_BYTES, (
        f"only {total_bytes} bytes of generated C++ compared "
        f"(expected >= {MIN_BYTES})")

    # The fallback half. Its floors come first: a ratchet read off a sweep that
    # stopped classifying is a ratchet reporting breakage as progress.
    assert classified >= MIN_BODIES_CLASSIFIED, (
        f"only {classified} stdlib bodies classified (expected >= "
        f"{MIN_BODIES_CLASSIFIED}) -- the routing sweep stopped measuring, so "
        f"its {fallback} fallback count is not comparable to the ratchet")
    assert routed >= MIN_ROUTED_BODIES, (
        f"only {routed} stdlib bodies routed through THIR (expected >= "
        f"{MIN_ROUTED_BODIES}) -- routing collapsed, or classification did")
    assert with_bodies >= MIN_CLASSIFIED_MODULES, (
        f"only {with_bodies} modules contributed any body (expected >= "
        f"{MIN_CLASSIFIED_MODULES})")

    _assert_every_family_reached(compiler)
    if ratchet_problem:
        pytest.fail(ratchet_problem, pytrace=False)

    # The nightly `thir-stdlib-fallback` row ratchets the SAME population on the
    # sweep script's own key, so pin its threshold to that key measured here.
    # Without this, "aligning" the nightly number to MAX_FALLBACK_BODIES passes
    # every test in the repo while handing the row slack for every collapsed
    # body -- and slack quietly accumulating is exactly how a ratchet stops
    # being one.
    collapsed, collapsed_total = _collapsed(bodies_by_module)
    nightly_max = _nightly_max_fallback()
    # Prove the collapse is LIVE, and prove it over the CLASSIFIED population
    # rather than the fallback subset: the two keys differ only where a module
    # holds same-named bodies, and the fallback subset can legitimately hold no
    # such pair (it does today), which makes an assertion scoped to it
    # unfalsifiable. Over ~2800 stdlib bodies the pairs are abundant, so a
    # merge key that stopped coarsening -- the failure that would silently make
    # the nightly pin below a duplicate of MAX_FALLBACK_BODIES -- shows up here.
    assert collapsed_total < classified, (
        f"collapsing by bare body name merged NOTHING over {classified} "
        f"classified bodies ({collapsed_total} survive) -- the merge key is no "
        f"longer a coarsening of the per-body one, so the two ratchets below "
        f"are not measuring one population on two keys")
    assert collapsed <= fallback, (
        f"collapsing by bare body name GREW the fallback count ({collapsed} vs "
        f"{fallback}) -- the merge key is not a coarsening of the per-body one")
    assert nightly_max == collapsed, (
        f"the nightly thir-stdlib-fallback row arms --max-fallback "
        f"{nightly_max}, but its own counting key measures {collapsed} "
        f"fallback bodies on this tree ({fallback} counted per BODY here).\n"
        f"If stdlib routing improved, lower the row's --max-fallback to "
        f"{collapsed} in ci/nightly/configs.json. If the number was raised: "
        f"the two ratchets count DIFFERENT keys on purpose -- this file counts "
        f"every body (what deleting the AST emitter needs), the sweep collapses "
        f"same-named bodies per module -- so they must not be made equal.")

    print(f"\ntpy| thir stdlib gate: {modules} modules, {compares} files, "
          f"{total_bytes} bytes identical in {elapsed:.1f}s; "
          f"fallback {fallback}/{MAX_FALLBACK_BODIES} over {classified} bodies "
          f"({routed} routed; {collapsed} under the nightly row's collapsed "
          f"key)")
    # Per-CELL coverage is REPORTED, never asserted. A cell no sweep reaches
    # is not a defect -- some are deliberate fences, some transcribe a ladder
    # leg the stdlib has no shape for -- so a floor here would be a
    # false-alarm generator. The number is a trend line, and having it at all
    # is the point: the fold's own coverage question went unanswerable once
    # its instruments came down.
    _cells = arg_table.reached(compiler)
    _decided = {(f, c.removeprefix("!")) for f, c in _cells
                if c not in (arg_table.PROLOGUE_CELL, arg_table.NO_CELL)}
    print(f"tpy| thir arg-table: {len(THIR_ARG_FAMILIES)} families all "
          f"reached over {sum(_cells.values())} argument verdicts; "
          f"{len(_decided & THIR_ARG_CELLS)}/{len(THIR_ARG_CELLS)} cells "
          f"decided at least one")


def test_snapshot_path_scheme_round_trips() -> None:
    """The dotted-name -> path mapping is injective and reversible.

    A module name holds no `/` and a path component holds no `.`, so splitting
    on dots inverts exactly. The pair that looks like a collision is a package
    and its submodule: `tplib.json` lands on the file `json.hpp` beside the
    directory `json/` that `tplib.json.parser` lives in, which a filesystem
    holds side by side. Asserted over the whole library because the render case
    puts all of it in ONE expected/ tree, where a collision would silently
    overwrite one module's render with another's.
    """
    names = [n for n in _lib_module_names() if not _is_macro_module(n)]
    assert len(names) >= MIN_MODULES
    base = RENDER_CASE_MAIN.parent.parent / "expected"

    def invert(path: Path, ext: str) -> str:
        rel = path.relative_to(base)
        parts = list(rel.parts[1:])  # drop include/ or src/
        parts[-1] = parts[-1][: -len(ext)]
        return ".".join(parts)

    seen: dict[Path, str] = {}
    for name in names:
        for ext in (".hpp", ".cpp"):
            path = module_to_expected_path(base, name, ext)
            assert invert(path, ext) == name
            assert seen.setdefault(path, name) == name, (
                f"{name} and {seen[path]} both map to {path}")

    pkg = module_to_expected_path(base, "tplib.json", ".hpp")
    sub = module_to_expected_path(base, "tplib.json.parser", ".hpp")
    assert pkg != sub and sub.parent.name == "json"


def test_render_case_imports_every_lib_module() -> None:
    """The render case's import list equals the non-macro lib/tpy module set.

    Its options.json snapshots `*`, which takes every library module that
    COMPILES -- so the import list alone decides what gets a committed render.
    A module nobody imports there is matched by nothing, and the pattern
    reports no error because it did match the modules that were compiled.
    """
    want = {n for n in _lib_module_names() if not _is_macro_module(n)}
    tree = ast.parse(RENDER_CASE_MAIN.read_text())
    got = {a.name for node in tree.body if isinstance(node, ast.Import)
           for a in node.names}
    missing = sorted(want - got)
    extra = sorted(got - want)
    assert len(want) >= MIN_MODULES, (
        f"only {len(want)} non-macro modules found under {LIB_TPY} -- the "
        f"module scan broke, so this guard is comparing against nothing")
    if missing or extra:
        pytest.fail(
            f"{RENDER_CASE_MAIN} does not import every non-macro module under "
            f"{LIB_TPY}, so the library's committed render has holes.\n"
            + (f"  ADD these lines: "
               f"{', '.join('import ' + n for n in missing)}\n"
               if missing else "")
            + (f"  REMOVE these lines (no such non-macro module): "
               f"{', '.join('import ' + n for n in extra)}\n"
               if extra else "")
            + f"Then refresh with `uv run python tests/update_snapshots.py "
              f"-k stdlib_render`.",
            pytrace=False)
