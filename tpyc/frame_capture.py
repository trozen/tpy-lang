"""How a resumable frame holds each captured parameter across suspensions
(`classify_param_kind`): the one decision the frame builder, its printer and
sema's escape checks read. Kept below `thir/` so sema reads it without
importing the IR."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING

from .type_def_registry import is_owned_in_coro_frame
from .typesys import (
    open_type_param,
    CallableType, OwnType, TupleType, copy_owns_storage,
    is_fn_type, is_ptr_variant_union, param_takes_ownership,
    pointer_repr_optional, unwrap_readonly, unwrap_ref_type,
)

if TYPE_CHECKING:
    from .typesys import TpyType


class FrameCaptureKind(Enum):
    """How a frame holds a captured parameter across suspensions (the
    storage-form cascade of `classify_param_kind`)."""
    REF = 0
    VALUE = 1
    POINTER = 2
    TYPE_PARAM = 3
    STATIC_PROTOCOL = 4
    OWNED_VALUE = 5
    FN = 6
    OWNED_COPY = 7
    CAPTURE = 8
    OWNED_TUPLE = 9


def classify_param_kind(
        ptype: 'TpyType',
        is_static_protocol_param: 'Callable[[TpyType], bool]',
) -> FrameCaptureKind:
    """Decide the coro-frame storage kind for an async-def param.

    Single source of truth for the kind cascade: `_classify_params`
    derives the C++ spelling from it, `param_borrows` reads it to decide
    whether an rvalue-temporary arg bound to this param must be hoisted
    into the awaiter's frame, and a protocol parameter's frame member is
    built from its concrete twin's kind (`frame_member_slot`).
    """
    ptype_inner = unwrap_ref_type(ptype)
    actual = unwrap_readonly(ptype_inner)
    # str / bytes are captured owned in the frame (copied at construction)
    # so the view doesn't dangle across a suspension when the arg is a
    # temporary -- see is_owned_in_coro_frame (the sema borrow set excludes
    # the same set). Explicit view params (StrView/Span) stay borrow-form.
    if is_owned_in_coro_frame(ptype_inner):
        return FrameCaptureKind.OWNED_COPY
    # `readonly[T]` is the same slot seen through the const wrapper, so it
    # takes the same per-instantiation traits: a literal `const T&` field
    # would hold a reference at a VALUE instantiation too, where the frame
    # outlives the temporary the factory call materialized.
    if open_type_param(ptype, int_kind=True) is not None:
        return FrameCaptureKind.TYPE_PARAM
    # Checked before the OptionalType-pointer-repr branch so
    # `Own[Awaitable[T]] | None` isn't mis-routed to POINTER --
    # `_protocol_template_parts` rejects the nullable shape instead.
    if is_static_protocol_param(ptype):
        return FrameCaptureKind.STATIC_PROTOCOL
    # Fn callable: concrete type is deduced as an `F_<pname>` template arg
    # (like a static protocol). Checked before is_value_type() -- a
    # CallableType would otherwise route through type_to_cpp(), which a
    # template-mode Fn cannot answer.
    if is_fn_type(actual):
        return FrameCaptureKind.FN
    # A generic `T | None` slot lands here too, and the arm's spelling is
    # what it wants: `to_cpp_param_type()` renders the runtime's
    # per-instantiation form, which owns at a value T and points at a
    # reference one. Only the KIND's name is a pointer -- the borrow
    # question is answered per instantiation, on the substituted slot the
    # call site passes to `param_borrows`.
    if pointer_repr_optional(actual) is not None:
        return FrameCaptureKind.POINTER
    if is_ptr_variant_union(actual):
        return FrameCaptureKind.POINTER
    # A borrow-form tuple param (std::tuple<..., T*>) is held in the frame
    # by value-of-pointers so it aliases the caller across suspensions; a
    # VALUE-kind field would store the storage form (std::tuple<..., T>)
    # and silently copy the element where CPython shares it.
    if isinstance(actual, TupleType) and actual.has_pointer_repr_element():
        return (FrameCaptureKind.OWNED_TUPLE if param_takes_ownership(ptype)
                else FrameCaptureKind.POINTER)
    # Own[T] checked before is_value_type(): `OwnType.is_value_type()` is
    # True (Own[T] uses T&& at param boundaries) but the move-only
    # semantics need explicit forwarding, so it can't ride the VALUE path.
    if isinstance(ptype_inner, OwnType):
        return FrameCaptureKind.OWNED_VALUE
    if ptype_inner.is_value_type():
        return FrameCaptureKind.VALUE
    return FrameCaptureKind.REF


def frame_owns_param_copy(ptype: 'TpyType') -> bool:
    """Whether a coroutine frame holds its own copy of a parameter declared
    `ptype` that owns storage, by the frame's storage decision
    (`classify_param_kind`): an owned copy (`str` / `bytes`), a value
    member (a value record, tuple or union; a handle -- a view or a `Ptr`
    -- copied by value still points at the caller's storage) or a moved-in
    `Own[...]`. A tuple
    with `Own` elements is held per element, a deduced or generic slot
    per instantiation, and neither answers here."""
    kind = classify_param_kind(ptype, lambda _t: False)
    if kind is FrameCaptureKind.OWNED_COPY or kind is FrameCaptureKind.OWNED_VALUE:
        return True
    if kind is not FrameCaptureKind.VALUE:
        return False
    bare = unwrap_readonly(unwrap_ref_type(ptype))
    # A callable value holds no buffer a view could point into; what a call
    # through it lends is its contract's.
    if isinstance(bare, CallableType):
        return False
    return copy_owns_storage(bare)
