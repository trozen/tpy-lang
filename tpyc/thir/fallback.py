"""Per-body AST-fallback tally for the --thir-codegen summary.

The routed-body tally says how many bodies THIR carries; this module says
what blocks the rest, so the gap to each deletion target (ctor MIL emit /
gen_body+gen_expr / form machinery) is measured, not estimated. Each
lowering attempt records the FIRST reject reason it hits (set-if-empty, so
the innermost/earliest gate wins) and the attempt driver folds a
`component:reason` count onto the active Compiler; the test harness
aggregates the counts across cases and xdist workers and prints a
per-component breakdown next to the routed tally.

Reason namespaces: `sig.*` (signature-level rejects in _function_eligible),
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

Like faces.py: the registry-side helpers are pure, the mutable state lives
on the active Compiler, so everything here is a no-op outside a
compilation (standalone-lowering units still work) and the default
(non---thir-codegen) path never reaches it at all.
"""

from __future__ import annotations

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


def note(reason: str) -> bool:
    """Record `reason` as the current attempt's first reject, if none is
    recorded yet. Returns False so gate sites can `return note("sig.x")`
    without restructuring."""
    compiler = get_current_compiler()
    if compiler is not None and compiler._thir_reject_reason is None:
        compiler._thir_reject_reason = reason
    return False


def begin_attempt() -> None:
    """Clear the first-reject slot ahead of one body's lowering attempt."""
    compiler = get_current_compiler()
    if compiler is not None:
        compiler._thir_reject_reason = None


def fold_attempt(component: str) -> None:
    """Fold a failed attempt's reason into the per-compilation tally.
    `component` is the deletion-target population: "body" (functions and
    methods -- the gen_body/gen_expr target) or "ctor" (the MIL target)."""
    compiler = get_current_compiler()
    if compiler is None:
        return
    key = f"{component}:{compiler._thir_reject_reason or 'unclassified'}"
    fb = compiler._thir_fallback
    fb[key] = fb.get(key, 0) + 1


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
    """Reason tag for a statement the eligibility walk rejected: the first
    landmark construct found inside it, else the statement's own shape
    (`stmt.<snake_case_kind>`)."""
    if is_dataclass(stmt) and not isinstance(stmt, type):
        for node in _walk(stmt):
            for cls, tag in _LANDMARKS:
                if isinstance(node, cls):
                    return tag
    kind = _CAMEL_SPLIT.sub("_", type(stmt).__name__.removeprefix("Tpy")).lower()
    return f"stmt.{kind}"
