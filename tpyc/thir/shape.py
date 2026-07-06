"""Per-body distinct-SHAPE tally for the --thir-codegen summary.

The routed-body count (299k+) is body-weighted: the stdlib links into every
case, so one `Box.clone` body counts once per case. That measures throughput,
not migration progress. This module answers the other question -- of the
DISTINCT body shapes the corpus contains, what fraction routes -- by
fingerprinting each candidate body (routed or fallback) with a structural
signature that is invariant across the stdlib-linked-everywhere repetition and
across cross-module structural twins, then deduping by signature.

A shape signature is `kind | <sorted AST node-kinds> | p:<param families> |
r:<return family>`: the callable kind, the set of statement/expression
constructs the body uses, and the coarse type-families of its signature. Two
bodies with the same signature are "the same shape"; the same function compiled
in 3000 cases collapses to one, and two structurally-identical getters over
different records collapse to one.

Reported: `distinct routed / distinct total` (the honest %), plus the
top BLOCKED shapes ranked by leverage (how many bodies each gates -- the
inflated count is the right weight for sequencing). A shape with both routed
and fallback bodies is "partial" -- the signature is too coarse to separate a
type-driven routing split, or a real one; counted as not-yet-routed.

Like faces.py / fallback.py: the helpers are pure and the mutable state lives on
the active Compiler, so this is a no-op outside a compilation and the default
(non---thir-codegen) path never reaches it.
"""

from __future__ import annotations

import re
from dataclasses import fields as dataclass_fields, is_dataclass

from ..compilation_context import get_current_compiler
from ..parse.nodes import TpyStmt
from ..typesys import (
    NominalType,
    OptionalType,
    OwnType,
    TupleType,
    TypeParamRef,
    UnionType,
    VoidType,
    is_any_bytes_type,
    is_any_int_type,
    is_any_str_type,
    is_float_type,
    is_primitive_type,
    is_protocol_type,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)

_CAMEL_SPLIT = re.compile(r"(?<!^)(?=[A-Z])")

# Container element/collection nominal names -- a coarse family so `list[T]` /
# `dict[K, V]` / `Array` / `Span` collapse together (their body-shape relevance
# is "a container", finer element typing is captured by the node-kinds used).
_CONTAINERS = frozenset({"list", "dict", "set", "Array", "Span", "frozenset",
                         "bytearray", "tuple"})


def _peel(t):
    return unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))


def _type_family(t) -> str:
    """A coarse routing-relevant family tag for a signature type. Defensive:
    any unexpected shape falls to `other` rather than raising -- an
    instrumentation tag, never a correctness path."""
    if t is None:
        return "void"
    try:
        t = _peel(t)
        if isinstance(t, VoidType):
            return "void"
        if isinstance(t, OwnType):
            return f"own[{_type_family(t.wrapped)}]"
        if isinstance(t, TypeParamRef):
            return "generic"
        if isinstance(t, OptionalType):
            return "opt"
        if isinstance(t, UnionType):
            return "union"
        if isinstance(t, TupleType):
            return "tuple"
        if isinstance(t, NominalType):
            # scalar folds the register scalars (bool/Char/IntN/Float) AND
            # BigInt (`int`) -- primitive excludes BigInt (heap), but shape-wise
            # they route as value scalars.
            if is_primitive_type(t) or is_any_int_type(t) or is_float_type(t):
                return "scalar"
            if is_any_str_type(t):
                return "str"
            if is_any_bytes_type(t):
                return "bytes"
            if t.name in _CONTAINERS:
                return "container"
            if is_protocol_type(t):
                return "protocol"
            if t.is_user_record:
                return "record"
        return "other"
    except Exception:
        return "other"


def _is_node(x: object) -> bool:
    # Recurse only into parse-tree dataclasses (mirrors fallback._is_node):
    # TpyType / SourceLocation values carry no construct and types can be
    # shared/cyclic, so skip them.
    return (is_dataclass(x) and not isinstance(x, type)
            and type(x).__module__ == TpyStmt.__module__
            and type(x).__name__ != "SourceLocation")


def _node_kinds(body) -> set[str]:
    """The set of distinct AST node-kind tags (snake_case, `Tpy` stripped)
    reachable in `body` -- the construct vocabulary the body uses."""
    out: set[str] = set()
    stack: list[object] = list(body)
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        out.add(_CAMEL_SPLIT.sub("_", type(node).__name__.removeprefix("Tpy")).lower())
        for f in dataclass_fields(node):
            v = getattr(node, f.name, None)
            if isinstance(v, (list, tuple)):
                stack.extend(x for x in v if _is_node(x))
            elif _is_node(v):
                stack.append(v)
    return out


def _callable_kind(func, component: str) -> str:
    if component == "ctor":
        return "ctor"
    if func.is_staticmethod:
        return "static"
    if func.is_property_getter or func.is_property_setter:
        return "property"
    if func.is_method:
        return "method"
    return "free"


def body_shape_signature(func, component: str) -> str:
    """The canonical shape signature for one body -- invariant across the
    stdlib-repeat and cross-module structural twins, distinct across construct /
    signature-type differences."""
    kinds = ",".join(sorted(_node_kinds(func.body)))
    params = ",".join(sorted(_type_family(pt) for _n, pt in func.params))
    ret = _type_family(func.return_type)
    return f"{_callable_kind(func, component)}|{kinds}|p:{params}|r:{ret}"


def record_shape(func, component: str, routed: bool) -> None:
    """Fold one candidate body into the per-compilation shape tally. Keyed by
    signature; the inner dict accumulates `routed` (a routed body) or the
    body's first-reject reason (read off the compiler, the same slot
    `fold_attempt` folds) so aggregation stays a plain additive dict merge
    (worker -> controller, like the fallback tally)."""
    compiler = get_current_compiler()
    if compiler is None:
        return
    try:
        sig = body_shape_signature(func, component)
    except Exception:
        return
    slot = "routed" if routed else (compiler._thir_reject_reason or "unclassified")
    shapes = compiler._thir_shapes
    inner = shapes.get(sig)
    if inner is None:
        inner = {}
        shapes[sig] = inner
    inner[slot] = inner.get(slot, 0) + 1


def summarize_shapes(shapes: dict[str, dict[str, int]]) -> dict[str, object]:
    """Reduce the aggregated `signature -> {slot: count}` map to the reported
    figures: distinct totals, the routed fraction, and the top blocked shapes
    ranked by leverage (blocked body count). A signature with any non-`routed`
    slot is not fully routed."""
    total = len(shapes)
    routed = 0
    partial = 0
    blocked = []
    for sig, inner in shapes.items():
        fallback_bodies = sum(n for slot, n in inner.items() if slot != "routed")
        if fallback_bodies == 0:
            routed += 1
            continue
        if inner.get("routed"):
            partial += 1
        reasons = sorted(((slot, n) for slot, n in inner.items() if slot != "routed"),
                         key=lambda kv: (-kv[1], kv[0]))
        blocked.append((sig, fallback_bodies, reasons[0][0]))
    blocked.sort(key=lambda x: (-x[1], x[0]))
    return {
        "total": total,
        "routed": routed,
        "partial": partial,
        "blocked": blocked,
        "pct": (100.0 * routed / total) if total else 0.0,
    }
