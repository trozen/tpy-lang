"""Fail-closed checks shared by body and definition coverage."""

from collections.abc import Mapping

from ..thir import nodes as th
from ..thir.metadata import unsupported_metadata
# `view_compatible` is the one predicate between a view holder and what it
# borrows: a TypeDef family pairing, a leaf fact rather than a MIR rule.
from ..thir.scalar_leaves import (  # noqa: F401
    MIRClass, primitive_leaf, primitive_owned_leaf, storage_leaf, view_compatible,
)
from ..typesys import (
    FLOAT, INT32, FloatLiteralType, IntLiteralType, Representation, TpyType, passing_representation, through_view,
)
from .call_contract import MIRCallSummary, MIRParameterBinding
from .nodes import MIRCall, MIRPlace, MIRSlot, MIRSlotId, call_place
from .value import holding_representation


def literal_type(expr: th.THIRLiteral, expected: TpyType | None = None) -> TpyType:
    """The type of the leaf a scalar literal holds. A number literal sema
    left unresolved converts to its context's leaf type when the context
    names one; alone it is the C++ literal it spells (`int` / `double`).
    Whether the value fits is `scalar_leaves.leaf_constant`'s question."""
    typ = expr.result_type
    if isinstance(typ, (IntLiteralType, FloatLiteralType)):
        if expected is not None and storage_leaf(expected):
            return expected
        return INT32 if isinstance(typ, IntLiteralType) else FLOAT
    return typ


def scalar_param(binding: MIRParameterBinding) -> bool:
    """A parameter bound as the callee's own scalar value
    (`MIRParameterBinding.scalar_value`)."""
    return binding.scalar_value


def slot_representation(slot: MIRSlot, bindings: Mapping[MIRSlotId, MIRParameterBinding]) -> Representation:
    """The representation a slot's value has: a parameter's binding's
    passing convention (`MIRFunction.slot_bindings`), else its holding's
    (a value and owned storage are their own storage, a holder points
    elsewhere)."""
    if slot.id in bindings:
        return passing_representation(bindings[slot.id].passing)
    return holding_representation(slot.shape.holding)


def _path_key(path: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(item.name if isinstance(item, th.THIRFieldIdentity) else type(item).__name__ for item in path)


def call_write_places(call: MIRCall, effects: MIRCallSummary, slots: Mapping[MIRSlotId, MIRSlot]
                      ) -> tuple[MIRPlace, ...]:
    """The caller's places a call may write: each write of its present
    effects, at its parameter path (`call_place`)."""
    return tuple(call_place(call, write.parameter, write.path, slots)
                 for write in sorted(effects.writes, key=lambda w: (w.parameter, _path_key(w.path))))


def read_leaf(slot: MIRSlot) -> TpyType:
    """The leaf type an operation reading `slot` sees: a view holder reads
    its family's owned leaf."""
    return through_view(slot.type)


def primitive_operand(slot: MIRSlot) -> bool:
    """An operand a certified primitive operation reads: an inert leaf by
    value, or an owned leaf through a borrowed holder or a view, whose
    TypeDef carries the primitive-operation contract."""
    if slot.shape.leaf_borrow:
        return primitive_owned_leaf(read_leaf(slot))
    return slot.shape.is_scalar and primitive_leaf(slot.type)


class MIRUnsupported(Exception):
    def __init__(self, node: object, reason: str) -> None:
        self.node = node
        self.reason = reason


def require(node: object, condition: bool, reason: str) -> None:
    if not condition:
        raise MIRUnsupported(node, reason)


def plain(node: object, allowed: set[str]) -> None:
    # New non-default metadata must not silently acquire scalar semantics.
    member = unsupported_metadata(node, allowed)
    require(node, member is None, f"unsupported metadata: {member}")
