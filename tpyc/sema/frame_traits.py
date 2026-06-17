"""Send/Sync frame classification (docs/SEND_SYNC_DESIGN.md OQ3).

Builds FrameType for the four captured-state surfaces -- async coroutines,
generators, lambdas, nested defs -- by mirroring the storage shapes codegen
actually emits (gen_async._classify_params and the hoisted-local decision
tree). The mirror is deliberately conservative: only shapes that codegen
stores as owned values can be Send; everything sema cannot classify makes
the frame non-Send and non-Sync. Relaxations are individual, tested
decisions.

Slot rules (positive cases only; everything else is non-Send):
- value-type params/locals/captures: owned copies -> the type's own traits
- `str` params: stored as std::string_view (caller's storage) -> StrView's
  traits (non-Send, Sync)
- Own[T] params: moved in, stored as T by value -> T's traits
- hoisted non-value locals: tpy::frame_slot<T> owned storage -> T's traits,
  except loop vars / unpack targets (may lower to raw-pointer slots) and
  pointer-repr Optionals / unions (pointer variants) -> non-Send
- async methods capture the receiver as <Class>& -> non-Send

Sub-frames (awaited coroutines) are FunctionInfo references resolved
recursively at query time with a greatest-fixed-point cycle guard,
mirroring the generic-record is_send walker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..typesys import (
    FrameSlot, FrameType, OwnType, OptionalType, UnionType, ReadonlyType,
    TypeParamRef, TupleType, STRVIEW, unwrap_ref_type, unwrap_readonly,
    PendingListType, PendingDictType, PendingSetType, PendingStrType,
    PendingBytesType, IntLiteralType, FloatLiteralType,
)
from ..type_def_registry import is_str_type

if TYPE_CHECKING:
    from ..typesys import FunctionInfo, TpyType


def _owned_traits(t: 'TpyType') -> tuple[bool, bool]:
    """Traits of a slot that stores `t` as an owned value.

    Capture classification can run before local deduction resolves
    Pending* forms, so the common pending shapes are answered directly
    (the resolved container would be Send-iff-elements, never Sync; the
    resolved literal types are value types).
    """
    if isinstance(t, PendingListType):
        return (_owned_traits(t.element_type)[0], False)
    if isinstance(t, PendingDictType):
        return (_owned_traits(t.key_type)[0]
                and _owned_traits(t.value_type)[0], False)
    if isinstance(t, PendingSetType):
        return (_owned_traits(t.element_type)[0], False)
    if isinstance(t, (PendingStrType, PendingBytesType)):
        # Storage form is a view of the originating buffer
        return (False, True)
    if isinstance(t, (IntLiteralType, FloatLiteralType)):
        return (True, True)
    return (t.is_send(), t.is_sync())


def _value_slot_traits(t: 'TpyType') -> tuple[bool, bool]:
    """Traits of a value-typed (non-Own) frame slot.

    A tuple with reference elements is a value type, but codegen emits its
    frame slot in borrow form (`std::tuple<T*, ...>`, aliasing the caller)
    rather than owned -- so the slot's traits are NOT the tuple's
    storage-form is_send/is_sync (which would falsely report Send for a
    tuple of Send records). A borrow slot aliases originating-thread memory:
    non-Send, non-Sync. Own[tuple[...]] does NOT route here -- it moves in
    and is genuinely owned, so the Own paths call _owned_traits directly.

    Unwraps readonly first: param_slot/local_slot reach here with the
    readonly wrapper still on (they strip only Ref), so a
    readonly[tuple[...]] borrow slot must still be caught."""
    bare = unwrap_readonly(t)
    if isinstance(bare, TupleType) and bare.has_ref_elements():
        return (False, False)
    return _owned_traits(t)


def param_slot(pname: str, ptype: 'TpyType') -> FrameSlot:
    """Classify one async/generator param's frame slot.

    Conservative mirror of gen_async._classify_param_kind: only the shapes
    that store owned values (VALUE, OWNED_VALUE) can be Send; str params
    store a string_view borrow; every other kind (REF, POINTER, TYPE_PARAM,
    STATIC_PROTOCOL) aliases caller state or is unknown per-instantiation.
    """
    ptype_inner = unwrap_ref_type(ptype)
    actual = unwrap_readonly(ptype_inner)
    if is_str_type(ptype_inner):
        return FrameSlot(pname, STRVIEW.is_send(), STRVIEW.is_sync(), ptype_inner)
    if isinstance(ptype_inner, TypeParamRef):
        return FrameSlot(pname, False, False, ptype_inner)
    if isinstance(actual, OptionalType) or isinstance(actual, UnionType):
        # Pointer-repr Optional / pointer-variant union borrow forms
        return FrameSlot(pname, False, False, ptype_inner)
    if isinstance(ptype_inner, OwnType):
        send, sync = _owned_traits(ptype_inner.wrapped)
        return FrameSlot(pname, send, sync, ptype_inner)
    if ptype_inner.is_value_type():
        send, sync = _value_slot_traits(ptype_inner)
        return FrameSlot(pname, send, sync, ptype_inner)
    return FrameSlot(pname, False, False, ptype_inner)  # T& reference


def local_slot(name: str, ltype: 'TpyType', is_loop_var: bool) -> FrameSlot:
    """Classify one hoisted-across-suspension local's frame slot."""
    if is_loop_var:
        # Loop vars / unpack targets may lower to raw-pointer slots
        return FrameSlot(name, False, False, ltype)
    inner = unwrap_ref_type(ltype)
    if isinstance(inner, (OptionalType, UnionType)):
        # Pointer-repr storage shapes
        return FrameSlot(name, False, False, inner)
    if isinstance(inner, TypeParamRef):
        return FrameSlot(name, False, False, inner)
    if isinstance(inner, OwnType):
        send, sync = _owned_traits(inner.wrapped)
        return FrameSlot(name, send, sync, inner)
    # Value types are stored by value; non-value locals use owned
    # tpy::frame_slot<T> storage -- both follow the type's own traits,
    # except a borrow-form tuple (handled by _value_slot_traits).
    send, sync = _value_slot_traits(inner)
    return FrameSlot(name, send, sync, inner)


def capture_slot(name: str, ctype: 'TpyType | None', by_ref: bool) -> FrameSlot:
    """Classify one lambda / nested-def capture."""
    if by_ref or ctype is None:
        return FrameSlot(name, False, False, ctype)
    inner = unwrap_ref_type(ctype)
    if isinstance(inner, ReadonlyType):
        inner = inner.wrapped
    if isinstance(inner, (OptionalType, UnionType, TypeParamRef)):
        return FrameSlot(name, False, False, inner)
    # By-value captures copy -- owned storage, the type's own traits.
    # Conservative on a borrow-form tuple: a captured owned tuple would copy
    # to owned (Send-ok), but a captured borrow tuple copies its pointers and
    # still aliases; frame_traits can't tell the captured var's storage form
    # apart, so _value_slot_traits treats the tuple as non-Send either way.
    send, sync = _value_slot_traits(inner)
    return FrameSlot(name, send, sync, inner)


def build_closure_frame(
    captures: list[tuple[str, 'TpyType | None', bool]],
) -> FrameType:
    """FrameType for a lambda / nested def from its (name, type, by_ref)
    capture list."""
    return FrameType(
        tuple(capture_slot(n, t, r) for n, t, r in captures), "closure",
    )


def _own_frame_type(fi: 'FunctionInfo') -> Optional[FrameType]:
    """Compute (and memoize) fi's own-slot FrameType, excluding sub-frames.

    Returns None when the function has no frame materials at all (e.g. an
    async/generator whose body was never analyzed) -- callers treat that
    as unclassifiable.
    """
    if fi.frame_type is not None:
        return fi.frame_type
    if fi.is_async or fi.is_generator:
        if fi.frame_locals is None:
            return None  # body not analyzed; unclassifiable
        slots: list[FrameSlot] = []
        if fi.is_method:
            # Receiver captured as <Class>& across suspensions
            slots.append(FrameSlot("self", False, False, None))
        for p in fi.params:
            slots.append(param_slot(p.name, p.type))
        for lname, ltype in fi.frame_locals:
            slots.append(local_slot(
                lname, ltype, lname in fi.frame_loop_var_names))
        kind = "coroutine" if fi.is_async else "generator"
        fi.frame_type = FrameType(tuple(slots), kind)
    elif fi.frame_captures is not None:
        fi.frame_type = build_closure_frame(fi.frame_captures)
    elif not fi.is_method:
        # Plain free function used as a value: no captured state
        fi.frame_type = FrameType((), "closure")
    else:
        return None  # bound method value -- receiver capture, unclassified
    return fi.frame_type


def frame_type_of_function(fi: 'FunctionInfo') -> Optional[FrameType]:
    """fi's own-slot FrameType (excluding awaited sub-frames), for diagnostics
    that name the offending captured slot. Sub-frame causes are not reflected
    here -- frame_traits_of_function is the source of truth for the boolean."""
    return _own_frame_type(fi)


def frame_traits_of_function(
    fi: 'FunctionInfo', _seen: set | None = None,
) -> tuple[bool, bool]:
    """(is_send, is_sync) of fi's frame, including awaited sub-frames.

    Greatest-fixed-point on await cycles: an in-progress fi is assumed
    conformant, so mutually-awaiting coroutines settle on the AND of all
    their concrete slots (mirrors the generic-record re-entrancy guard).
    """
    if _seen is None:
        _seen = set()
    if id(fi) in _seen:
        return (True, True)
    _seen.add(id(fi))
    # @unsafe_send/@unsafe_sync/@nosend/@nosync force the whole frame
    # answer -- the author takes responsibility, no structural walk.
    if fi.send_override is not None and fi.sync_override is not None:
        return (fi.send_override, fi.sync_override)
    own = _own_frame_type(fi)
    if own is None:
        send, sync = False, False
    else:
        send, sync = own.is_send(), own.is_sync()
        for sub in (fi.frame_subframes or ()):
            if sub is None:
                send = sync = False  # unclassifiable await operand
                break
            sub_send, sub_sync = frame_traits_of_function(sub, _seen)
            send = send and sub_send
            sync = sync and sub_sync
    if fi.send_override is not None:
        send = fi.send_override
    if fi.sync_override is not None:
        sync = fi.sync_override
    return (send, sync)
