"""Unit tests for the Send/Sync why-not chain-walker (sema/send_chain.py).

Container / record shapes need a live compiler context and are covered by the
tests/cases/send_sync snapshots; here we pin the render format and the
context-free leaf forms (primitives, Ptr, tuple, union)."""
from tpyc.typesys import (
    INT32, VOID, PtrType, TupleType, OptionalType, OwnType, CallableType,
    make_union,
)
from tpyc.sema.send_chain import why_not_send, why_not_sync, render_chain


def test_send_type_has_no_chain():
    assert why_not_send(INT32) is None
    assert why_not_sync(INT32) is None


def test_ptr_is_a_leaf():
    chain = why_not_send(PtrType(INT32))
    assert chain is not None and not chain.children
    out = render_chain(chain, send=True)
    assert out == "Ptr[int32] is not Send (raw pointer, no ownership guarantee)"


def test_tuple_attributes_offending_element():
    chain = why_not_send(TupleType((INT32, PtrType(INT32))))
    out = render_chain(chain, send=True)
    assert out.splitlines() == [
        "tuple[int32, Ptr[int32]] is not Send",
        "+-- element 1: Ptr[int32] is not Send (raw pointer, no ownership guarantee)",
    ]


def test_union_attributes_each_offending_member():
    chain = why_not_send(make_union(PtrType(INT32), INT32))
    # Only the Ptr member is non-Send; int32 is skipped.
    out = render_chain(chain, send=True)
    assert out.splitlines() == [
        "Ptr[int32] | int32 is not Send",
        "+-- Ptr[int32] is not Send (raw pointer, no ownership guarantee)",
    ]


def test_readonly_ptr_sync_recurses_into_pointee():
    # Ptr[readonly[int32]] is Sync (int32 is), so no chain.
    ro = PtrType(INT32, is_readonly=True)
    assert why_not_sync(ro) is None


def test_optional_attributes_inner():
    # tuple inner doesn't collapse the Optional (unlike a pointer inner), so
    # the Optional arm recurses into the offending tuple element.
    chain = why_not_send(OptionalType(TupleType((PtrType(INT32),))))
    out = render_chain(chain, send=True)
    assert out.splitlines()[-1].endswith(
        "Ptr[int32] is not Send (raw pointer, no ownership guarantee)")


def test_callable_leaf_names_the_queried_trait():
    cb = CallableType((INT32,), VOID)
    assert "non-Send state -- wrap with Send[Callable[...]]" in render_chain(
        why_not_send(cb), send=True)
    # The leaf wording must track the trait being asked, not hardcode Send.
    assert "non-Sync state -- wrap with Sync[Callable[...]]" in render_chain(
        why_not_sync(cb), send=False)


def test_own_is_a_sync_leaf_not_a_pass_through():
    # Own[int32] is Send (delegates) but never Sync (single-owner); the sync
    # chain must not mislabel it as a mutable container.
    assert why_not_send(OwnType(INT32)) is None
    out = render_chain(why_not_sync(OwnType(INT32)), send=False)
    assert out == "Own[int32] is not Sync (single-owner move slot, not shareable across threads)"
