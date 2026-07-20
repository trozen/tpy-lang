"""Per-body AST-fallback tally; reported by the --thir-codegen summary.

The routed-body tally says how many bodies THIR carries; this module says
what blocks the rest, so the gap to each deletion target (ctor MIL emit /
gen_body+gen_expr / form machinery) is measured, not estimated. Each
lowering attempt records the FIRST reject reason it hits (set-if-empty, so
the innermost/earliest gate wins) and the attempt driver folds a
`component:reason` count onto the active Compiler; the test harness
aggregates the counts across cases and xdist workers. Recording runs on
every case of every run with THIR on; the per-component breakdown PRINTS
only under the marker-ignoring metrics flags (it measures standing
migration backlog, not the run that emitted it). `$THIR_FALLBACK_JSON`
dumps the full counts from any run that asks.

Reason namespaces: `sig.*` (callable-structure rejects),
`body.*` (function-level body facts), `stmt.*` (the first ineligible
statement's shape), `expr.*` (a landmark construct found inside that
statement -- comprehensions, genexpr, lambda, await, walrus), `ctor.*`
(constructor-specific gates). A reject with no recorded reason folds as
`unclassified` -- an instrumentation gap to close, not an error.

The first-reason attribution is deliberately coarse: a body may hold
several blockers, and the landmark scan tags the first landmark found
anywhere inside the first-rejecting statement (not necessarily the exact
failing sub-expression). That is the right resolution for sequencing --
"what would have to land first" -- and keeps the instrumentation at two
chokepoints instead of threaded through every predicate.

A second, finer slot refines bare `stmt.*` tags: gate reject arms that
know the blocking SUB-construct record it via `note_detail` (set-if-empty,
cleared per statement by the body walk), and the statement chokepoint
composes `stmt.<shape>:<detail>`. Details recorded during probes that a
sibling arm later accepts can misattribute -- same coarseness contract as
the first-reason slot; only the composition at a real statement reject
makes a detail visible, so successful statements never leak one.

Like faces.py: the registry-side helpers are pure, the mutable state lives
on the active Compiler, so everything here is a no-op outside a
compilation (standalone-lowering units still work).
"""

from __future__ import annotations

import os
import re
from dataclasses import fields as dataclass_fields, is_dataclass

from ..compilation_context import get_current_compiler
from ..parse.nodes import (
    TpyAwait,
    TpyDictComprehension,
    TpyGeneratorExpression,
    TpyLambda,
    TpyListComprehension,
    TpyNamedExpr,
    TpySetComprehension,
    TpyStmt,
)

# Landmark constructs the statement-shape axis has not opened: their
# presence anywhere in a rejecting statement names the frontier that must
# land first, which is more actionable than the host statement's shape.
_LANDMARKS: tuple[tuple[type, str], ...] = (
    (TpyListComprehension, "expr.list_comp"),
    (TpyDictComprehension, "expr.dict_comp"),
    (TpySetComprehension, "expr.set_comp"),
    (TpyGeneratorExpression, "expr.genexpr"),
    (TpyLambda, "expr.lambda"),
    (TpyAwait, "expr.await"),
    (TpyNamedExpr, "expr.walrus"),
)

_CAMEL_SPLIT = re.compile(r"(?<!^)(?=[A-Z])")

# The arm-residual instrument walks every FALLBACK unit, so it is gated on its
# dump env-var to keep normal runs free of the AST-walk cost.
_ARM_RESIDUAL_ON = bool(os.environ.get("THIR_ARM_RESIDUAL_JSON"))


class ThirUnsupported(Exception):
    """A lowering-time rejection that routes the whole body to AST codegen."""

    def __init__(self, reason: str, *, detail: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def is_bodyless_binding(fn) -> bool:
    """A callable with NO gen_body / MIL emit -- out of the body-migration
    scope entirely, NOT a fallback.

    A call-site dispatch to a runtime symbol / template -- method-style
    `@native("push_back")` (native_name), `@cpp_template(...)`, free
    `@native(function=True)` (native_function) -- or any `...` stub (is_stub
    covers declaration-only stubs like `cast`, native-class method stubs, and
    bare-`@native` methods whose native_name stays None). Covers the whole
    builtin-type method/ctor surface (str / int / list / dict / ...); a BODIED
    method on a builtin receiver still counts (a real deferred surface).

    Shared with `--dump-thir`, which must tell "never attempted" apart from
    "attempted and rejected" -- reporting the former as a fallback misreports
    the migration frontier.
    """
    return (fn.native_function or fn.native_name is not None
            or fn.cpp_template is not None or fn.is_stub)


def note(reason: str) -> bool:
    """Record `reason` as the current attempt's first reject, if none is
    recorded yet. Returns False so admission sites can `return note("sig.x")`
    without restructuring."""
    compiler = get_current_compiler()
    if compiler is not None and compiler._thir_reject_reason is None:
        compiler._thir_reject_reason = reason
    return False


def note_detail(reason: str) -> bool:
    """Record the blocking sub-construct for the statement currently being
    gated, if none is recorded yet (first reject wins, like `note`). Returns
    False so reject arms can `return note_detail("call.linkage")`."""
    compiler = get_current_compiler()
    if compiler is not None and compiler._thir_reject_detail is None:
        compiler._thir_reject_detail = reason
    return False


def begin_stmt() -> None:
    """Clear the detail slot ahead of one statement's eligibility check, so
    a detail left by an earlier (accepted) statement cannot leak into a
    later statement's composed tag."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._thir_reject_detail = None


def begin_attempt() -> None:
    """Clear the first-reject slot ahead of one body's lowering attempt."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._thir_reject_reason = None
        compiler._thir_reject_detail = None


def fold_attempt(component: str, node: object = None) -> None:
    """Fold a failed attempt's reason into the per-compilation tally.
    `component` is the deletion-target population: "body" (functions and
    methods -- the gen_body/gen_expr target) or "ctor" (the MIL target).

    `node` is the AST callable that failed; passing it also records the
    reason per body (`--dump-thir` names it), so the aggregate tally and the
    per-body attribution cannot drift apart."""
    compiler = get_current_compiler()
    if compiler is None:
        return
    reason = compiler._thir_reject_reason or 'unclassified'
    key = f"{component}:{reason}"
    fb = compiler._thir_fallback
    fb[key] = fb.get(key, 0) + 1
    if node is not None:
        compiler._thir_reject_by_node[id(node)] = reason


def record_arm_residual(body: 'list') -> None:
    """Tally, per AST construct kind, how many FALLBACK units CONTAIN it -- the
    deletion residual for that construct's AST emit arm (smallest = closest to
    deletable). NOT the first-reject reason: a fallback emits its WHOLE tree via
    AST, so it keeps alive every arm its constructs use regardless of WHY it fell
    back (a body blocked on a call still keeps its for-each arm alive). Deduped
    per unit; called at all three fallback seams (body / ctor / resumable), so
    the population is every un-routed unit. Gated on $THIR_ARM_RESIDUAL_JSON to
    keep normal runs free of the AST walk."""
    if not _ARM_RESIDUAL_ON:
        return
    compiler = get_current_compiler()
    if compiler is None:
        return
    kinds: set[str] = set()
    for stmt in body:
        for node in _walk(stmt):
            kinds.add(_CAMEL_SPLIT.sub("_", type(node).__name__
                                      .removeprefix("Tpy")).lower())
    r = compiler._thir_arm_residual
    for k in kinds:
        r[k] = r.get(k, 0) + 1


def _is_node(x: object) -> bool:
    # Recurse only into parse-tree dataclasses (patterns, handlers, and
    # comprehension generators included); TpyType/SourceLocation values
    # carry no landmark and types can be shared/cyclic, so skip them.
    return (is_dataclass(x) and not isinstance(x, type)
            and type(x).__module__ == TpyStmt.__module__
            and type(x).__name__ != "SourceLocation")


def _walk(root: object):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for f in dataclass_fields(node):
            v = getattr(node, f.name, None)
            if isinstance(v, (list, tuple)):
                stack.extend(x for x in v if _is_node(x))
            elif _is_node(v):
                stack.append(v)


def classify_stmt(stmt: TpyStmt) -> str:
    """Reason tag for a statement lowering rejected: the first
    landmark construct found inside it, else the statement's own shape
    (`stmt.<snake_case_kind>`)."""
    if is_dataclass(stmt) and not isinstance(stmt, type):
        for node in _walk(stmt):
            for cls, tag in _LANDMARKS:
                if isinstance(node, cls):
                    return tag
    kind = _CAMEL_SPLIT.sub("_", type(stmt).__name__.removeprefix("Tpy")).lower()
    return f"stmt.{kind}"


def expr_kind_tag(e: object) -> str:
    """`expr.<snake_case_kind>` for an expression node -- the dispatch-tail
    catch-all detail (an expression kind the gate has no arm for)."""
    kind = _CAMEL_SPLIT.sub("_", type(e).__name__.removeprefix("Tpy")).lower()
    return f"expr.{kind}"


def stmt_reject_reason(stmt: TpyStmt, detail: str | None = None) -> str:
    """The composed tag for a statement lowering rejected:
    a landmark tag stands alone; a bare `stmt.*` shape picks up the
    sub-construct detail recorded during this statement's admission, if any."""
    reason = classify_stmt(stmt)
    compiler = get_current_compiler()
    if detail is None:
        detail = (compiler._thir_reject_detail
                  if compiler is not None else None)
    if detail is not None and reason.startswith("stmt."):
        return f"{reason}:{detail}"
    return reason
