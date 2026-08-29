"""Cross-path audit of the per-function binding-classification sets.

THIR keeps per-name mirrors of codegen's binding sets (`_LowerCtx.pointers`
<-> `ctx.pointer_locals`, ...). A mirror that misses a producer makes every
render keyed on it silently pick the wrong form, and the byte-diff only sees
the miss once some arm actually consults the name -- two such gaps were found
by accident, one already diverging in the corpus.

The overlay runs both paths in one process over the same TpyFunction objects,
so the sets join per function. Each side records the UNION of names its sets
ever held during that function's emit/lowering: captures fire at the
scope-restore seams (`restore_local_scope` / `branch_scope`), where
branch-scoped adds are still live, plus once when the window closes. The
check then asserts the AST-side union is a SUBSET of the THIR-side union for
every ROUTED body. Extra THIR names are legal (a mirror may seed wider); a
missing name is a missed producer, caught the first time any case exercises
it instead of by divergence.

Keyed by `id(func)` with a strong ref to the node, valid within one Compiler
-- the move_audit discipline (see its docstring for why id-keyed cross-pass
joins must hold the node). Routed-only scoping reuses the same journal seams
as move_audit and the face tally: a fallback body's partial THIR record is
dropped at rollback, so the join never compares a body whose THIR walk did
not complete.

KNOWN LIMIT: the join sees set MEMBERSHIP, not the render decisions keyed on
it, and only for bodies both paths finished -- it complements the byte-diff
and the move-verdict join, subsuming neither.

MORTALITY: like the move-verdict join, this needs BOTH paths, and its AST side
is the emitter the cutover deletes -- see `move_audit.py`'s note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .compilation_context import get_current_compiler

if TYPE_CHECKING:
    from .compiler import Compiler
    from .parse.nodes import TpyFunction

# (label, codegen ctx attr, THIR _LowerCtx attr). Only pairs whose THIR side
# claims to mirror the codegen set for every ROUTED body belong here; adding
# a pair turns that claim from a comment into a corpus-checked fact.
SET_PAIRS = (
    ("pointers", "pointer_locals", "pointers"),
    ("ptr_variant_locals", "ptr_variant_locals", "ptr_variant_locals"),
    ("optional_locals", "optional_locals", "optional_locals"),
    ("storage_tuple_locals", "storage_form_tuple_locals",
     "storage_tuple_locals"),
    # The borrow-tuple const fixpoint (ensure_borrow_tuple_const): THIR
    # computes lazily, so every arm consuming a reassigned borrow-tuple
    # binding must call ensure before the join can hold.
    ("const_borrow_tuple_locals", "const_borrow_form_tuple_locals",
     "const_borrow_tuple_locals"),
    ("const_opt_borrow_tuple_locals", "const_optional_borrow_tuple_locals",
     "const_opt_borrow_tuple_locals"),
)

# AST-side names EXCLUDED from the join, by prefix: synthetic temps the AST
# emitter draws from its own counters and classifies for its own rendering.
# User code cannot spell them, and THIR bakes the corresponding renders into
# nodes at lowering (`oneshot_lift_locals` for the await lifts, the unpack
# nodes' baked element renders for the for-each tuple holders), so no THIR
# mirror set can or should hold them. `__await_lift_*` is frame-skeleton
# state (permanently AST per the D3 printer-layer decision); `__for_tup_*`
# is the for-each unpack holder the sync emitter registers for element-read
# lifting.
_EMIT_INTERNAL_NAME_PREFIXES = ("__await_lift_", "__for_tup_")

# Off in ordinary compiles (a user build has only one path to record). The
# harness turns it on whenever THIR runs, same footing as move_audit.
_ON = False


def set_enabled(on: bool) -> None:
    global _ON
    _ON = on


def enabled() -> bool:
    return _ON


# Key inside a THIR-side record holding `(label, name)` pairs a lowering arm
# ACKNOWLEDGED as deliberately unmirrored (a documented partial whose full
# mirror is parked design work). The join subtracts them, so the partial is
# machine-visible at the arm instead of failing the corpus until the design
# lands; delete the acknowledgment with the mirror.
ACK_KEY = "__acknowledged"


def fresh_record() -> dict:
    rec = {label: set() for label, _a, _t in SET_PAIRS}
    rec[ACK_KEY] = set()
    return rec


def acknowledge_binding_partial(lc, label: str, name: str) -> None:
    """Record a deliberately-unmirrored producer hit: `name` will appear in
    the AST's `label` set for this body, and the THIR mirror is a KNOWN
    partial (see the calling arm's comment for why)."""
    rec = lc.binding_union
    if rec is not None:
        rec[ACK_KEY].add((label, name))


def _merge(store: dict, func: 'TpyFunction', rec: dict) -> None:
    key = id(func)
    hit = store.get(key)
    if hit is None:
        store[key] = (func, rec)
        return
    _f, prev = hit
    for label, names in rec.items():
        prev[label] |= names


# --- AST side --------------------------------------------------------------

def begin_ast_body(func: 'TpyFunction') -> None:
    """Open the recording window for one body's emission (from
    `setup_body_scope`, the single per-function scope setup, which has the
    function in hand)."""
    if not _ON:
        return
    compiler = get_current_compiler()
    if compiler is None:
        return
    compiler._binding_ast_open = (func, fresh_record())


def capture_ast(ctx) -> None:
    """Union the live codegen sets into the open window. Called at
    `restore_local_scope` -- the one seam where branch-scoped adds are still
    live -- and by `end_ast_body`. No window open means emission outside any
    recorded body (module init, between-body scaffolding): skip."""
    if not _ON:
        return
    compiler = get_current_compiler()
    if compiler is None:
        return
    open_rec = compiler._binding_ast_open
    if open_rec is None:
        return
    _f, rec = open_rec
    for label, ast_attr, _t in SET_PAIRS:
        rec[label] |= getattr(ctx, ast_attr)


def end_ast_body(ctx) -> None:
    """Close the window into the per-compiler AST record. Called from
    `reset_scope` (BEFORE the wipe: the next body's setup closes the previous
    body's window, whose sets are still live at that point) and at the end of
    module generation (the last body has no successor to close it)."""
    if not _ON:
        return
    compiler = get_current_compiler()
    if compiler is None:
        return
    open_rec = compiler._binding_ast_open
    if open_rec is None:
        return
    capture_ast(ctx)
    func, rec = open_rec
    _merge(compiler._binding_facts_ast, func, rec)
    compiler._binding_ast_open = None


# --- THIR side -------------------------------------------------------------

def capture_thir(lc) -> None:
    """Union the live `_LowerCtx` mirror sets into its per-lowering ledger.
    Called at every `branch_scope` pop (covering nested-def scopes, which
    wrap one) and by `publish_thir`. `binding_union` is None when the audit
    is off, so the pop pays a single attribute test."""
    rec = lc.binding_union
    if rec is None:
        return
    for label, _a, thir_attr in SET_PAIRS:
        rec[label] |= getattr(lc, thir_attr)


def publish_thir(lc) -> None:
    """Fold one COMPLETED lowering's ledger into the per-compiler THIR record
    (call at each entry point's success return -- a finished body proves the
    whole walk ran). Journaled like move_audit: a body that later folds back
    drops its record at rollback, and an @overload impl publishes once per
    stub with the unions merged under one key."""
    rec = lc.binding_union
    if rec is None:
        return
    compiler = get_current_compiler()
    if compiler is None:
        return
    capture_thir(lc)
    _merge(compiler._binding_facts_thir, lc.func, rec)
    if compiler._binding_journal is not None:
        compiler._binding_journal.add(id(lc.func))


def begin_body() -> None:
    """Open the journal for one body's lowering attempt (from
    `fallback.begin_attempt`, beside the face and move-audit journals)."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._binding_journal = set()


def commit_body() -> None:
    """Close the window on a body that ROUTED."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._binding_journal = None


def rollback_body() -> None:
    """Drop every THIR record published since the journal opened. A func key
    belongs to exactly one body, so popping is right (move_audit's shape)."""
    compiler = get_current_compiler()
    if compiler is None:
        return
    assert compiler._binding_journal is not None, (
        "fold_attempt with no binding-audit journal open -- every fallback "
        "seam must be preceded by begin_attempt")
    for key in compiler._binding_journal:
        compiler._binding_facts_thir.pop(key, None)
    compiler._binding_journal = None


# --- The join --------------------------------------------------------------

def violations(compiler: 'Compiler') -> list[tuple[str, str, list[str]]]:
    """(func_name, set_label, missing_names) for every routed body whose
    AST-side union holds a name the THIR mirror never did. Keys only one side
    recorded are skipped: an AST-only key is a body THIR never routed, a
    THIR-only key has no oracle record (nothing to be a subset of)."""
    out = []
    ast = compiler._binding_facts_ast
    for key, (func, thir_rec) in compiler._binding_facts_thir.items():
        hit = ast.get(key)
        if hit is None:
            continue
        _f, ast_rec = hit
        acks = thir_rec.get(ACK_KEY, set())
        for label, names in ast_rec.items():
            if label == ACK_KEY:
                continue
            missing = {n for n in names - thir_rec[label]
                       if not n.startswith(_EMIT_INTERNAL_NAME_PREFIXES)
                       and (label, n) not in acks}
            if missing:
                out.append((func.name, label, sorted(missing)))
        # A STALE ack -- one whose name the THIR mirror now records -- is
        # the silent hazard: it suppresses nothing today but would absorb
        # a future genuine miss for that name forever. Report it so the
        # acknowledgment dies with the mirror that made it obsolete.
        # (Eager acks whose name NEITHER side records are fine: the ack
        # sites register per-shape, not per-actual-miss.)
        for label, name in sorted(acks):
            if name in thir_rec.get(label, set()):
                out.append((func.name, f"dead-ack:{label}", [name]))
    return out


def joined(compiler: 'Compiler') -> int:
    """How many bodies BOTH paths recorded -- the join's denominator, kept
    for the same reason move_audit keeps its: without it "0 violations" is
    indistinguishable from "0 bodies compared"."""
    ast = compiler._binding_facts_ast
    return sum(1 for key in compiler._binding_facts_thir if key in ast)
