"""The reject-reason journal and the diagnostic a lowering gap becomes.

THIR is the only author of an emitted body, so a shape lowering cannot
handle is a compile error rather than a reroute. Each lowering attempt
records the FIRST reject reason it hits (set-if-empty, so the innermost /
earliest gate wins) and the attempt driver turns that reason into the
user-facing `ThirRejectError`.

Reason namespaces: `sig.*` (callable-structure rejects), `body.*`
(function-level body facts), `stmt.*` (the first ineligible statement's
shape), `expr.*` (a landmark construct found inside that statement --
comprehensions, genexpr, lambda, await, walrus), `ctor.*`
(constructor-specific gates). A reject with no recorded reason reports as
`unclassified` -- an instrumentation gap to close, not an error.

The first-reason attribution is deliberately coarse: a body may hold
several blockers, and the landmark scan tags the first landmark found
anywhere inside the first-rejecting statement (not necessarily the exact
failing sub-expression). That keeps the instrumentation at two chokepoints
instead of threaded through every predicate.

A second, finer slot refines bare `stmt.*` tags: gate reject arms that know
the blocking SUB-construct record it via `note_detail` (set-if-empty,
cleared per statement by the body walk), and the statement chokepoint
composes `stmt.<shape>:<detail>`. Details recorded during probes that a
sibling arm later accepts can misattribute -- same coarseness contract as
the first-reason slot; only the composition at a real statement reject makes
a detail visible, so successful statements never leak one.

Like faces.py: the registry-side helpers are pure, the mutable state lives
on the active Compiler, so everything here is a no-op outside a compilation
(standalone-lowering units still work).
"""

from __future__ import annotations

import re
from dataclasses import fields as dataclass_fields, is_dataclass
from typing import Literal, NoReturn, TYPE_CHECKING

from ..compilation_context import get_current_compiler
from ..typesys import is_bodyless_binding as _is_bodyless_binding
from .faces import (begin_witness_journal, commit_witnesses,
                    rollback_witnesses)
from ..parse.nodes import (
    is_parse_node,
    SourceLocation,
    TpyAwait,
    TpyDictComprehension,
    TpyExpr,
    TpyFunction,
    TpyGeneratorExpression,
    TpyLambda,
    TpyListComprehension,
    TpyNamedExpr,
    TpyPattern,
    TpySetComprehension,
    TpyStmt,
)

if TYPE_CHECKING:
    # Runtime import stays local to _strict_error: codegen_cpp.context imports
    # this package transitively.
    from ..codegen_cpp.context import ThirRejectError

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

class ThirUnsupported(Exception):
    """A lowering-time rejection, raised where an arm has no lowering for
    the shape in front of it.

    Not on its own a user-facing diagnostic: handlers inside `tpyc/thir/`
    catch it as an internal retry, and only the ones that let it reach a
    body boundary turn it into the `ThirRejectError` `reject_attempt`
    raises.
    """

    def __init__(self, reason: str, *, detail: bool = False,
                 loc: 'SourceLocation | None' = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        # The offending source position, stamped by the statement chokepoint as
        # the reject unwinds (innermost frame wins) -- what the diagnostic
        # points the user at.
        self.loc = loc
        compiler = get_current_compiler()
        if loc is None and compiler is not None:
            cause = compiler._thir_reject_detail
            if cause is not None and reason.endswith(":" + cause):
                self.loc = compiler._thir_reject_detail_loc

    def with_context(self, reason: str) -> 'ThirUnsupported':
        # Lambda failures identify the actual expression; an enclosing
        # statement's landmark scan must not replace that evidence.
        if self.loc is not None and ":lambda." in self.reason:
            return self
        return ThirUnsupported(reason, loc=self.loc)


# One rule for a FunctionInfo and the TpyFunction it came from; shared with
# `--dump-thir`, which must tell "never attempted" apart from "attempted and
# rejected".
is_bodyless_binding = _is_bodyless_binding


def note(reason: str, loc: 'SourceLocation | None' = None) -> bool:
    """Record `reason` as the current attempt's first reject, if none is
    recorded yet. Returns False so admission sites can `return note("sig.x")`
    without restructuring.

    `loc` is stored with the reason and never on its own: the pair is what the
    strict diagnostic prints, so a location outliving the reason it belongs to
    would point the user at an unrelated line."""
    compiler = get_current_compiler()
    if compiler is not None and compiler._thir_reject_reason is None:
        compiler._thir_reject_reason = reason
        compiler._thir_reject_loc = loc
    return False


def note_detail(reason: str, loc: 'SourceLocation | None' = None) -> bool:
    """Record the blocking sub-construct for the statement currently being
    gated, if none is recorded yet (first reject wins, like `note`). Returns
    False so reject arms can `return note_detail("call.linkage")`.
    A location belongs to this detail alone; only a reject carrying the detail
    may use it instead of the enclosing statement's position."""
    compiler = get_current_compiler()
    if compiler is not None and compiler._thir_reject_detail is None:
        compiler._thir_reject_detail = reason
        compiler._thir_reject_detail_loc = loc
    return False


def begin_stmt() -> None:
    """Clear the detail slot ahead of one statement's eligibility check, so
    a detail left by an earlier (accepted) statement cannot leak into a
    later statement's composed tag."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._thir_reject_detail = None
        compiler._thir_reject_detail_loc = None


def begin_attempt() -> None:
    """Clear the first-reject slot ahead of one body's lowering attempt."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._thir_reject_reason = None
        compiler._thir_reject_detail = None
        compiler._thir_reject_detail_loc = None
        compiler._thir_reject_loc = None
    begin_witness_journal()


def commit_attempt() -> None:
    """Close one body's lowering attempt on the ROUTED side -- the sibling of
    `reject_attempt`. Only the face journal cares (the reject slots are
    cleared by the next `begin_attempt`), but the call belongs at every success
    branch so the attempt window is bracketed where the lowering decision is
    made.

    A verbatim forward to `commit_witnesses()` today: the attempt bracket is
    the API lowering drivers hold, so it stays spelled as the bracket's own
    half rather than as the journal call it currently delegates to.
    """
    commit_witnesses()


# The lowering positions an attempt can fail at: a function or method body,
# a constructor (member-init list plus tail), the module-init body, an
# async / generator frame, a class constant, and a `Final` global.
RejectComponent = Literal["body", "ctor", "top_level", "resumable",
                          "class_const", "final_global"]


def reject_attempt(component: RejectComponent, node: object = None, *,
                   where: str | None = None,
                   loc: 'SourceLocation | None' = None) -> NoReturn:
    """Turn a failed lowering attempt into the user-facing diagnostic.

    `component` names the position that failed (see `RejectComponent`). `node`
    is the parse-tree unit that failed; passing it also records the reason per
    unit, so `--dump-thir` can name why a lowering raised. `where` names the
    enclosing unit, and `loc` the position to report, for the positions whose
    `node` carries neither.

    The single formulation for all of them: keeping the raise here rather than
    at each lowering boundary is what lets the boundary handlers keep returning
    None, which is also how the `_reject`-style helpers (they note a reason and
    return None rather than raising) reach the diagnostic at all.
    """
    rollback_witnesses()
    if loc is None:
        loc = getattr(node, "loc", None)
    compiler = get_current_compiler()
    if compiler is None:
        raise _strict_error(component, node, where, loc, 'unclassified')
    reason = compiler._thir_reject_reason or 'unclassified'
    if node is not None:
        compiler._thir_reject_by_node[node] = reason
    # The reject's own position when lowering recorded one; the enclosing
    # unit's `def`/decl line is the floor, never a stale sibling's.
    raise _strict_error(component, node, where,
                        compiler._thir_reject_loc or loc, reason)


def reject_or_defer(component: RejectComponent, node: object = None, *,
                    where: str | None = None) -> None:
    """`reject_attempt` for the bodies lowered AHEAD of emission. In a survey
    the diagnostic is kept rather than raised, so the loop goes on to the next
    body; `raise_deferred_rejects` raises the first one when the loops are
    done, which is the error an ordinary compile reports."""
    from ..codegen_cpp.context import ThirRejectError
    try:
        reject_attempt(component, node, where=where)
    except ThirRejectError as err:
        compiler = get_current_compiler()
        if compiler is None or not compiler._thir_survey:
            raise
        compiler._thir_deferred_rejects.append(err)


def raise_deferred_rejects() -> None:
    """Close a survey's pre-emission loops: emission cannot run over a body
    that did not lower, so the first kept diagnostic is raised here."""
    compiler = get_current_compiler()
    if compiler is not None and compiler._thir_deferred_rejects:
        raise compiler._thir_deferred_rejects[0]


def _strict_error(component: RejectComponent, node: object, where: str | None,
                  loc: 'SourceLocation | None',
                  reason: str) -> 'ThirRejectError':
    """The diagnostic a reject becomes.

    Phrased for a TPy user reading a compile error: the reason tag is the only
    internal token in the sentence. `component` and `reason` also ride on the
    error object, so tooling reads them without parsing the message."""
    from ..codegen_cpp.context import ThirRejectError
    if where is None:
        name = getattr(node, "name", None)
        # The user wrote a generator expression, not its function: name the
        # function the expression sits in, or the module.
        in_genexpr = getattr(node, "is_genexpr", False)
        if in_genexpr:
            name = node.genexpr_owner
        if component == "ctor":
            where = "in a constructor"
        elif name:
            where = f"in function '{name}'"
        elif component == "top_level" or in_genexpr:
            where = "at module level"
        else:
            where = "in this module"
    return ThirRejectError(
        f"{where}: this construct is not yet supported by C++ code "
        f"generation ({reason})", loc=loc, component=component, reason=reason)


def _walk(root: object):
    # Parse-tree nodes only: a TpyType carries no landmark and can be shared
    # or cyclic, so the walk would not terminate on one.
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for f in dataclass_fields(node):
            v = getattr(node, f.name, None)
            if isinstance(v, (list, tuple)):
                stack.extend(x for x in v if is_parse_node(x))
            elif is_parse_node(v):
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


def call_reject_reason(base: str) -> str:
    """The composed tag for a rejected CALL landmark (`expr.call` /
    `expr.method_call`) -- the sibling of `stmt_reject_reason` for the reasons
    that stand alone instead of composing onto a statement shape.

    Without it a bare `expr.call` raise DISCARDS the detail its own reject arm
    just recorded: the gates already name which receiver shape, argument row or
    slot family blocked (`_recv_shape_reject`, the argument table's family
    tails), but a bare landmark reason never reads that slot, so the tally
    collapses every one of them into two opaque buckets.

    CALL-TIME composition, like `stmt_reject_reason`: the detail is read off the
    compiler when THIS function runs, so it must be called AT the raise, never
    pre-evaluated into an argument ahead of the note_detail that should ride
    it."""
    compiler = get_current_compiler()
    detail = compiler._thir_reject_detail if compiler is not None else None
    return f"{base}:{detail}" if detail else base


def stmt_reject_reason(stmt: TpyStmt, detail: str | None = None) -> str:
    """The composed tag for a statement lowering rejected:
    a landmark tag stands alone except for a located lambda cause;
    a bare `stmt.*` shape picks up the
    sub-construct detail recorded during this statement's admission, if any.

    CALL-TIME composition: the detail is read off the compiler when THIS
    function runs, so never pre-evaluate the result into a helper argument
    ahead of the note_detail that should ride it -- pass a zero-arg callable
    and render at the raise instead. A pre-evaluated string reads the detail
    slot before the rejecting arm has written it, so the tag degrades to the
    bare statement shape with no failure anywhere to say so."""
    reason = classify_stmt(stmt)
    compiler = get_current_compiler()
    if detail is None:
        detail = (compiler._thir_reject_detail
                  if compiler is not None else None)
    if detail is not None and (reason.startswith("stmt.")
                               or (reason == "expr.lambda"
                                   and detail.startswith("lambda."))):
        return f"{reason}:{detail}"
    return reason
