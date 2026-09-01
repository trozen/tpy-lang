"""Look for the ONE asymmetry that makes deleting the AST body emitters unsafe:
a program the AST REFUSES and THIR emits.

Deleting an AST raise site is harmless when THIR refuses the same shape (the
diagnostic changes, a diagnostic remains) and is an improvement when THIR
lowers a shape the AST could not (the rejection was the defect). It is
dangerous only when THIR *renders* something the AST declined to render:
the refusal disappears and the program compiles to unreviewed output.

Note the asymmetry cannot be faked by a fallback. A body THIR cannot lower is
re-emitted by the AST, so if the AST raises, the fallback raises too -- THIR
succeeding while the AST raises means THIR ROUTED that body.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import conftest as C  # noqa: E402
from matrix import gen_multi, gen_single  # noqa: E402
from tpyc.codegen_cpp import CodeGenOptions  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp.context import CodeGenError  # noqa: E402


def front_end(src: Path, width: str):
    """Parse + analyze only. Split from emission so that an emitter refusal is
    never misread as a front-end one: an AST emitter raises `RuntimeError`,
    `NotImplementedError` and bare `assert` as well as `CodeGenError`, and
    bucketing those as "sema refused first" hides exactly the refusals this
    instrument exists to count.

    KNOWN HAZARD, measured and left standing: `run` calls this twice, once per
    emitter, and the second compile re-enters the global-state clear, wiping
    payloads the first attached to static TypeDefs -- so the first emitter can
    render against the second's payloads. Ablated over the whole matrix against
    a single-compile variant: zero bucket differences, so the recorded result
    does not depend on it. Fixing it means deciding whether one compile can
    safely feed both emitters, which is a change worth making deliberately
    rather than late.
    """
    compiler = Compiler(src, default_int=width,
                        lib_dirs=list(C.DEFAULT_LIB_DIRS))
    return compiler, compiler.compile()


def emit(compiler, compiled, src: Path, out: Path, thir: bool) -> dict:
    opts = dataclasses.replace(
        CodeGenOptions(emit_source_comments=True, comment_line_numbers=False),
        thir_codegen=thir)
    entry = next(m for m in compiled if m.is_entry_point)
    d = src.parent.resolve()
    emitted = 0
    for mod in compiled:
        try:
            mod.path.resolve().relative_to(d)
        except ValueError:
            continue
        compiler.generate_code(mod, out, entry_module_name=entry.name,
                               options=opts)
        emitted += 1
    # A path-resolution slip here would emit NOTHING and report success, which
    # is the vacuous green every instrument in this repo has had to grow a
    # guard against.
    if not emitted:
        raise RuntimeError(f"emitted no module for {src} -- path mapping slip")
    return dict(compiler._thir_fallback)


def _tree_differs(base: Path) -> str | None:
    """The two trees are written anyway, so diff them: these are programs no
    committed case contains, and a divergence in one is the same finding the
    corpus byte-diff exists to catch.

    Comparing zero files reports IDENTICAL, so the count is checked rather than
    the loop falling through -- the same vacuous green `emit` guards against,
    one function apart.
    """
    compared = 0
    for f in sorted((base / "ast").rglob("*.[ch]pp")):
        rel = f.relative_to(base / "ast")
        other = base / "thir" / rel
        if not other.exists():
            return f"missing under thir/: {rel}"
        compared += 1
        if f.read_text() != other.read_text():
            return str(rel)
    if not compared:
        return "compared ZERO files -- path mapping found no counterpart"
    return None


def run(src: Path, work: Path, width: str) -> dict:
    base = work / width / src.parent.name
    shutil.rmtree(base, ignore_errors=True)
    row = {"probe": src.parent.name, "width": width}

    try:
        compiler_a, compiled_a = front_end(src, width)
        compiler_b, compiled_b = front_end(src, width)
    except Exception as e:  # noqa: BLE001
        # Refused before either emitter ran, so both paths agree by
        # construction and the program is out of scope for this question.
        row["status"] = "FRONTEND"
        row["detail"] = f"{type(e).__name__}: {e}".splitlines()[0][:160]
        return row

    ast_err = thir_err = None
    try:
        emit(compiler_a, compiled_a, src, base / "ast", thir=False)
    except Exception as e:  # noqa: BLE001
        ast_err = f"{type(e).__name__}: {e}".splitlines()[0][:300]
    try:
        row["fallback"] = emit(compiler_b, compiled_b, src, base / "thir",
                               thir=True)
    except Exception as e:  # noqa: BLE001
        thir_err = f"{type(e).__name__}: {e}".splitlines()[0][:300]

    if ast_err and thir_err:
        row["status"] = "BOTH_REFUSE"
        # BOTH sides recorded: discarding the THIR half made this bucket unable
        # to say anything about which layer refused, which is the whole
        # question a control is asked to settle.
        row["ast_error"] = ast_err[:200]
        row["thir_error"] = thir_err[:200]
    elif ast_err and not thir_err:
        row["status"] = "ASYMMETRY"          # <- the finding this exists for
        row["ast_error"] = ast_err
    elif thir_err and not ast_err:
        # THIR refusing what the AST emits is a REGRESSION at cutover: valid
        # code losing its only emitter. Distinct from a fallback, which is
        # silent; this one raised.
        row["status"] = "THIR_ONLY_REFUSES"
        row["thir_error"] = thir_err
    elif row.get("fallback"):
        # Byte-identity here proves nothing -- the AST emitted this body for
        # BOTH runs. The shape works today and has no emitter after the
        # deletion, which is the blind class, so it gets its own bucket rather
        # than being counted as agreement.
        row["status"] = "THIR_FELL_BACK"
    else:
        row["status"] = "BOTH_EMIT"
        diff = _tree_differs(base)
        if diff is not None:
            row["status"] = "DIVERGE"
            row["diff_file"] = diff
    return row


# Committed cases known to refuse AT CODEGEN (not in sema), run as a positive
# control on every invocation. A zero from a detector is only evidence once the
# detector has been shown to fire, and this one's dominant outcome is a bucket
# (FRONTEND) that looks like a clean result -- so a silent misread and a clean
# tree are indistinguishable without these. Each must come back BOTH_REFUSE.
#
# What this establishes directly is that an AST refusal is DETECTED. Both error
# texts are recorded so a reader can see whether THIR refused for its own
# reason too, but the authoritative test of that is the corpus error-path gate,
# which demands the SAME diagnostic from both paths.
# Probes whose body THIR declines, so the AST emits it for both runs. Measured,
# not aspirational: lowering it is the win, exceeding it is a regression.
MAX_KNOWN_FALLBACKS = 7

CONTROL_CASES = [
    "generators/error_gen_rebind_slot_after_drain",
    "generators/error_gen_rebind_slot_crosses_lambda",
    "async/error_async_match_dyn_await",
    "iterators/error_gen_match_nested_narrowed_ptr_bind",
    "iterators/error_gen_match_nonlvalue_ptr_bind",
]


def run_controls(work: Path, width: str) -> list[dict]:
    rows = []
    for case in CONTROL_CASES:
        src = ROOT / "tests" / "cases" / case / "src" / "main.py"
        if not src.exists():
            rows.append({"probe": case, "status": "CONTROL_MISSING"})
            continue
        # `run` names a probe by its parent directory, which is `src` for every
        # committed case -- relabel so a failure names the case.
        row = run(src, work / "__control__" / case.replace("/", "_"), width)
        row["probe"] = case
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    ap.add_argument("--width", action="append", default=None)
    ap.add_argument("--work", type=Path,
                    default=Path("/tmp/agents/asym/__work__"))
    ap.add_argument("--probes", type=Path,
                    default=Path("/tmp/agents/asym/probes"))
    args = ap.parse_args()

    widths = args.width or ["Int32"]

    # Generated, not committed: the inputs are a cross product, so a generator
    # is both smaller than the corpus and the only form in which "every
    # crossing" is checkable rather than asserted. Wiped first, so a probe left
    # by an earlier matrix cannot join the run and be counted as coverage.
    shutil.rmtree(args.probes, ignore_errors=True)
    n1 = gen_single(args.probes / "single")
    n2 = gen_multi(args.probes / "multi")
    print(f"generated {n1} single-arm + {n2} switch-tier probes")

    probes = sorted(args.probes.rglob("*/main.py"))
    # The generated count and the run count are computed independently, so
    # crossing them catches a glob that silently found something else.
    assert len(probes) == n1 + n2, (
        f"generated {n1 + n2} probes but the run set holds {len(probes)}")

    rows = []
    for width in widths:
        for src in probes:
            r = run(src, args.work, width)
            rows.append(r)
            if r["status"] in ("ASYMMETRY", "THIR_ONLY_REFUSES", "DIVERGE"):
                print(f"  {r['status']:18s} [{width}] {r['probe']}")
                print(f"      {r.get('ast_error') or r.get('thir_error') or r.get('diff_file')}")

    tally: dict[str, int] = {}
    for r in rows:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
    print(f"\n{len(rows)} runs: "
          + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    fell_back = sorted({r["probe"] for r in rows
                        if r["status"] == "THIR_FELL_BACK"})
    if fell_back:
        # Not agreement: the AST emitted these bodies on both runs. Each is a
        # program that works today and has no emitter after the deletion.
        print(f"\nTHIR FELL BACK on {len(fell_back)} probe(s) -- shapes that "
              f"work today and lose their only emitter at the cutover:")
        for p in fell_back:
            reasons = sorted({k for r in rows if r["probe"] == p
                              for k in (r.get("fallback") or {})})
            print(f"  {p}  {','.join(reasons)}")
    # FRONTEND dominating is the expected shape and is the RESULT, not a
    # failure to probe: sema refuses most tier/pattern crossings before either
    # emitter runs, which is WHY the codegen raise sites go unwitnessed.
    ctrl = [c for w in widths for c in run_controls(args.work, w)]
    bad = [c for c in ctrl if c["status"] != "BOTH_REFUSE"]
    print("control: "
          + ", ".join(f"{c['probe'].split('/')[-1]}={c['status']}"
                      for c in ctrl))
    if bad:
        print(f"\nCONTROL FAILED ({len(bad)}) -- the zero above means nothing. "
              f"These cases refuse at codegen, so each must come back "
              f"BOTH_REFUSE; anything else says this instrument is reading the "
              f"wrong signal, not that the tree is clean.")
    if args.json:
        args.json.write_text(json.dumps(rows + ctrl, indent=1))
    # A FINDING is a non-zero exit -- reporting one only on stdout while
    # returning 0 makes the single result this tool exists to produce invisible
    # to anything checking status.
    #
    # Fallbacks are RATCHETED rather than counted as findings: the current set
    # is known and tracked (TODO.md, two of them filed), so failing on its
    # existence would make the exit code a constant and unable to distinguish a
    # broken instrument from the documented backlog. Exceeding it is the
    # regression worth failing on; beating it means lowering this number.
    found = sum(tally.get(k, 0) for k in
                ("ASYMMETRY", "THIR_ONLY_REFUSES", "DIVERGE"))
    fb_regressed = len(fell_back) > MAX_KNOWN_FALLBACKS
    if fb_regressed:
        print(f"\nFALLBACK RATCHET: {len(fell_back)} probes fall back, known "
              f"{MAX_KNOWN_FALLBACKS} -- a new shape lost its only emitter.")
    return 1 if (bad or found or fb_regressed) else 0


if __name__ == "__main__":
    sys.exit(main())
