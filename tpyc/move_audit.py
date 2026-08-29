"""Cross-path audit of the auto-move verdict.

Both emit paths decide `std::move` at a local's last use with the same shape
of predicate -- `ExpressionGenerator._is_last_use_movable` and THIR's
`_is_move_source` -- over the SAME `TpyName` objects, because the THIR overlay
re-emits the module the AST pass already emitted. So their verdicts join
exactly on node identity, and any disagreement is a move-vs-copy divergence.

This is the ONLY detector for that divergence. The byte-diff cannot see it: a
wrong verdict at a site whose render does not consult it emits identical C++,
which is how a wholesale-seeded movable set stayed green for months while
disagreeing on names no arm happened to act on.

Deliberately NOT keyed on source coordinates: two nodes can share a line, and
the whole point is object identity across the two passes of one compilation.
The key is only valid WITHIN one `Compiler`, which is why the tallies live on
the instance -- `id()` is recycled after GC, so a process-wide dict silently
invents rows for unrelated cases.

KNOWN LIMIT: this joins the base predicate, not the final emit decision. A
sink that overrides the verdict downstream reads as agreement here while the
byte-diff differs, so the two checks are complementary and neither subsumes
the other.

MORTALITY: this is a DUAL-path join, and its AST side
(`ExpressionGenerator._is_last_use_movable`) lives in the emitter the cutover
deletes. It cannot survive that deletion as written -- neither can
`binding_audit.py` or the corpus byte-diff, so the cutover retires three
cross-path detectors, not one. Whatever replaces them has to be decided with
the cutover, not after it.

Only a ROUTED body's THIR verdict counts. A body that falls back emits its
whole tree through the AST path, so its verdicts drove no emitted C++;
counting them would report divergences that cannot exist. The journal mirrors
`faces.begin_witness_journal` exactly, and is opened and closed at the same
seams so the two windows cannot drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .compilation_context import get_current_compiler

if TYPE_CHECKING:
    from .compiler import Compiler
    from .parse.nodes import TpyName

# Off in ordinary compiles: the predicates are called ~76k times over the test
# corpus, and a user build has only one path to record anyway (the join needs
# both). The harness turns it on whenever THIR runs -- a metric gated behind a
# remembered env var is a metric that gets forgotten.
_ON = False


def set_enabled(on: bool) -> None:
    global _ON
    _ON = on


def enabled() -> bool:
    return _ON


def record(path: str, node: 'TpyName', verdict: bool,
           func: 'str | None' = None) -> None:
    """Record one path's move verdict for `node`. `path` is "ast" or "thir".

    OR-folded per node: a name queried at several sites within one body counts
    as moved if any site moves it, which is the question the join asks.

    `func` is the enclosing function, carried so a failure can name WHERE it
    is -- the byte-diff labels its divergences that way and a bare
    `x (ast=True thir=False)` is a poor starting position in a 3700-case run.
    Only the THIR side supplies it (its lowering context holds the function);
    the AST predicate has no equivalent handle, and one side is enough since
    both judged the same node."""
    if not _ON:
        return
    compiler = get_current_compiler()
    if compiler is None:
        return
    d = (compiler._move_verdict_ast if path == "ast"
         else compiler._move_verdict_thir)
    key = id(node)
    prev = d.get(key)
    # The NODE is stored, not just its name: the dict is keyed by id(), so
    # without a strong reference a transient TpyName (a macro expansion, a
    # cloned method, a desugar temp) could be freed and its id reused by a
    # later node the other path queries -- a phantom divergence OR a phantom
    # agreement, in the one detector whose whole premise is node identity.
    d[key] = (node, verdict or (prev[1] if prev else False),
              func or (prev[2] if prev else None))
    if path == "thir" and compiler._move_verdict_journal is not None:
        compiler._move_verdict_journal.add(key)


def begin_body() -> None:
    """Open the journal for one body's lowering attempt (from
    `fallback.begin_attempt`, beside the face journal)."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._move_verdict_journal = set()


def commit_body() -> None:
    """Close the window on a body that ROUTED."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._move_verdict_journal = None


def rollback_body() -> None:
    """Drop every THIR verdict recorded since the journal opened.

    A node belongs to exactly one body, so dropping the key is right -- there
    is no earlier value to restore. Asserts the window was opened, exactly as
    `faces.rollback_witnesses` does: without it the two mirrored journals are
    silently non-equivalent, and a rolled-back verdict would belong to
    whatever ran last rather than to this body."""
    compiler = get_current_compiler()
    if compiler is None:
        return
    assert compiler._move_verdict_journal is not None, (
        "fold_attempt with no move-audit journal open -- every fallback seam "
        "must be preceded by begin_attempt")
    for key in compiler._move_verdict_journal:
        compiler._move_verdict_thir.pop(key, None)
    compiler._move_verdict_journal = None


def disagreements(
        compiler: 'Compiler') -> list[tuple[str, bool, bool, 'str | None']]:
    """(name, ast_verdict, thir_verdict) for every node both paths judged and
    judged differently. Nodes only one path reached are NOT divergences: the
    two walk different arm structures, so an unmatched node means "not asked",
    not "asked and answered differently"."""
    out = []
    ast = compiler._move_verdict_ast
    for key, (node, thir_v, func) in compiler._move_verdict_thir.items():
        hit = ast.get(key)
        if hit is not None and hit[1] != thir_v:
            out.append((node.name, hit[1], thir_v, func))
    return out


def joined(compiler: 'Compiler') -> int:
    """How many nodes BOTH paths judged -- the join's denominator.

    Without it "0 divergences" is indistinguishable from "0 nodes compared":
    if the THIR-side `record` call ever goes quiet (an arm calls a copy of the
    predicate, or moves out of this module), the gate reports green forever.
    Reported beside the divergence count for exactly that reason."""
    ast = compiler._move_verdict_ast
    return sum(1 for key in compiler._move_verdict_thir if key in ast)
