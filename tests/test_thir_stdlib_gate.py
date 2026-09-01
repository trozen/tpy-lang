"""Pre-merge gate over lib/tpy: THIR/AST byte-identity and the fallback ratchet.

Both properties come from ONE mega-entry compile (~6s), which is the whole point
of the file: measured separately they cost ~6s + ~64s, and the expensive half
only ever ran in a nightly row.

The library's committed RENDER oracle is NOT here -- it is an ordinary test
case, tests/cases/harness/stdlib_render, which imports every non-macro module
and snapshots them all through options.json. What this file adds is the
AST-vs-THIR diff: two authors checking each other, which goes away with the AST
body emitter. The case's snapshot outlives it, and is what the library's render
is checked against from then on; the guards that keep that case's coverage from
narrowing outlive it too, and live in tests/test_stdlib_render_coverage.py.
Everything in THIS file loses its subject once one emitter remains, which is
why the two are separate files.

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
)
# The lib/tpy module scan is shared with the render-coverage guards, which
# survive this file; it is imported rather than copied so the two readers
# cannot drift on what counts as a library module.
from test_stdlib_render_coverage import MIN_MODULES, compile_lib_tpy

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
# These are deliberately hard-coded well below the measured values (110 file
# compares / ~1.37 MB as of 2026-08-24) rather than tracking them, so they
# never need touching -- but a refactor that stops comparing fails loudly.
# MIN_MODULES is the same kind of floor, imported above because the
# render-coverage guards need it for the same reason.
MIN_COMPARES = 100
MIN_EMITTING_MODULES = 50
MIN_BYTES = 500_000

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
    compiler, compiled = compile_lib_tpy(tmp_path)

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
