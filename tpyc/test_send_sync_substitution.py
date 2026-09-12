"""Unit tests for NominalType.is_send / is_sync substitution at use sites.

`RecordInfo.is_send` / `is_sync` are computed at registration with any
field whose type mentions a TypeParamRef treated as conservatively-OK.
The use site re-walks fields and parents under concrete `type_args`,
so the answer is correct in both directions: types wrapping a non-Send
concrete arg report False, and types wrapping a Send concrete arg
report True even when the registration-time bool was conservative.
"""

import pytest

from .typesys import (
    INT32, FieldInfo, NominalType, OptionalType, PtrType, RecordInfo,
    TypeParamRef, make_list,
)
from .type_def_registry import (
    TypeCategory, attach_dynamic_type_def, clear_dynamic_type_defs,
)


@pytest.fixture
def _box_recordinfo():
    """Register Box[T] with `data: T` (a bare TypeParamRef field).

    At registration, the disjunction in sema/registration.py accepts the
    TypeParamRef-typed field as conservatively-OK, so `is_send` /
    `is_sync` would be True. We set those flags manually here because
    the unit test bypasses sema.
    """
    info = RecordInfo(
        name="Box",
        fields=[FieldInfo(name="data", type=TypeParamRef("T"))],
        type_params=["T"],
        is_send=True,
        is_sync=True,
    )
    attach_dynamic_type_def("Box", TypeCategory.RECORD, record=info)
    try:
        yield info
    finally:
        clear_dynamic_type_defs()


@pytest.fixture
def _wrapper_with_send_int_field():
    """Register Wrapper[T] with a `tag: int32` field plus `data: T`.

    Exercises that concrete-typed fields keep their definition-time
    answer (int32 is Send) while the TypeParamRef field is re-checked
    under substitution.
    """
    info = RecordInfo(
        name="Wrapper",
        fields=[
            FieldInfo(name="tag", type=INT32),
            FieldInfo(name="data", type=TypeParamRef("T")),
        ],
        type_params=["T"],
        is_send=True,
        is_sync=True,
    )
    attach_dynamic_type_def("Wrapper", TypeCategory.RECORD, record=info)
    try:
        yield info
    finally:
        clear_dynamic_type_defs()


def _box(arg) -> NominalType:
    return NominalType("Box", type_args=(arg,), _module_qname="Box")


def _wrapper(arg) -> NominalType:
    return NominalType("Wrapper", type_args=(arg,), _module_qname="Wrapper")


def _buf() -> NominalType:
    # Stand-in for a user record. No registration needed -- its is_send /
    # is_sync defaults (value-type fallback returning False for an
    # unregistered nominal) are fine; PtrType.is_send is unconditionally
    # False which is the only fact this test relies on.
    return NominalType("Buf", _module_qname="user.Buf")


class TestGenericRecordSendSync:
    def test_box_of_int_is_send(self, _box_recordinfo) -> None:
        """int32 is Send -> Box[int32] is Send (record bool stays True)."""
        assert _box(INT32).is_send() is True
        assert _box(INT32).is_sync() is True

    def test_box_of_ptr_is_not_send(self, _box_recordinfo) -> None:
        """Ptr[Buf] is not Send -> Box[Ptr[Buf]] is not Send despite the
        registration-time bool being True."""
        ptr_buf = PtrType(_buf())
        assert ptr_buf.is_send() is False
        assert _box(ptr_buf).is_send() is False

    def test_box_of_ptr_is_not_sync(self, _box_recordinfo) -> None:
        """Same shape for is_sync."""
        ptr_buf = PtrType(_buf())
        assert ptr_buf.is_sync() is False
        assert _box(ptr_buf).is_sync() is False

    def test_wrapper_concrete_field_preserved(
        self, _wrapper_with_send_int_field,
    ) -> None:
        """Concrete-typed fields that were Send at registration stay Send;
        only the TypeParamRef-typed field flips under substitution."""
        assert _wrapper(INT32).is_send() is True
        assert _wrapper(PtrType(_buf())).is_send() is False

    def test_concrete_non_send_field_stays_false(self) -> None:
        """A generic record whose concrete-typed field is non-Send fails
        the walker regardless of the type_args. The cached bool from
        registration is False, but the walker reproduces the same answer
        by walking the concrete field and finding it non-Send."""
        info = RecordInfo(
            name="Holder",
            fields=[FieldInfo(name="p", type=PtrType(_buf()))],
            type_params=["T"],
            is_send=False,
            is_sync=False,
        )
        attach_dynamic_type_def("Holder", TypeCategory.RECORD, record=info)
        try:
            holder = NominalType("Holder", type_args=(INT32,),
                                 _module_qname="Holder")
            assert holder.is_send() is False
            assert holder.is_sync() is False
        finally:
            clear_dynamic_type_defs()

    def test_non_generic_record_unaffected(self) -> None:
        """Non-generic records (no type_params) take the same path but
        the substitution helper no-ops; the record-level bool wins."""
        info = RecordInfo(
            name="Plain",
            fields=[FieldInfo(name="x", type=INT32)],
            is_send=True,
            is_sync=True,
        )
        attach_dynamic_type_def("Plain", TypeCategory.RECORD, record=info)
        try:
            plain = NominalType("Plain", _module_qname="Plain")
            assert plain.is_send() is True
            assert plain.is_sync() is True
        finally:
            clear_dynamic_type_defs()

    def test_list_of_typeparam_flips_true(self) -> None:
        """`class W[T]: c: list[T]` -- at registration `list[T].is_send()`
        evaluates to False because the TypeDef callable propagates
        `T.is_send() == False`. The use-site walker substitutes the
        concrete `type_args`, so W[int32] correctly reports True."""
        info = RecordInfo(
            name="W",
            fields=[FieldInfo(
                name="c", type=make_list(element_type=TypeParamRef("T")),
            )],
            type_params=["T"],
        )
        attach_dynamic_type_def("W", TypeCategory.RECORD, record=info)
        try:
            assert NominalType(
                "W", type_args=(INT32,), _module_qname="W",
            ).is_send() is True
            assert NominalType(
                "W", type_args=(PtrType(_buf()),), _module_qname="W",
            ).is_send() is False
            # Mirror for is_sync to exercise _evaluating_sync.
            # `list[T].is_sync()` is always False (mutable container), so
            # the answer for W[anything] is False regardless of the arg.
            assert NominalType(
                "W", type_args=(INT32,), _module_qname="W",
            ).is_sync() is False
        finally:
            clear_dynamic_type_defs()

    def test_self_recursive_generic(self) -> None:
        """`class Tree[T]: value: T; children: list[Tree[T]]` is the
        canonical self-recursive generic. The cycle guard returns True
        (greatest fixed point) on re-entry, so the answer is decided by
        the non-recursive value field: Tree[int32] is Send (int32 is
        Send), Tree[Ptr[Buf]] is not."""
        info = RecordInfo(
            name="Tree",
            fields=[
                FieldInfo(name="value", type=TypeParamRef("T")),
                FieldInfo(
                    name="children",
                    type=make_list(element_type=NominalType(
                        "Tree", type_args=(TypeParamRef("T"),),
                        _module_qname="Tree",
                    )),
                ),
            ],
            type_params=["T"],
        )
        attach_dynamic_type_def("Tree", TypeCategory.RECORD, record=info)
        try:
            assert NominalType(
                "Tree", type_args=(INT32,), _module_qname="Tree",
            ).is_send() is True
            assert NominalType(
                "Tree", type_args=(PtrType(_buf()),), _module_qname="Tree",
            ).is_send() is False
        finally:
            clear_dynamic_type_defs()

    def test_self_recursive_generic_sync_via_optional(self) -> None:
        """Counterpart to test_self_recursive_generic for is_sync.

        `list[T]` short-circuits is_sync to False (mutable container), so
        the prior test never re-enters the walker for is_sync and
        `_evaluating_sync` never fires. Optional[T] passes is_sync
        through to T, so a Tree whose recursive field is Optional[Tree[T]]
        forces the walker to re-enter Tree[int32].is_sync() recursively,
        exercising the greatest-fixed-point branch."""
        info = RecordInfo(
            name="Tree",
            fields=[
                FieldInfo(name="value", type=TypeParamRef("T")),
                FieldInfo(
                    name="child",
                    type=OptionalType(NominalType(
                        "Tree", type_args=(TypeParamRef("T"),),
                        _module_qname="Tree",
                    )),
                ),
            ],
            type_params=["T"],
        )
        attach_dynamic_type_def("Tree", TypeCategory.RECORD, record=info)
        try:
            assert NominalType(
                "Tree", type_args=(INT32,), _module_qname="Tree",
            ).is_sync() is True
            assert NominalType(
                "Tree", type_args=(PtrType(_buf()),), _module_qname="Tree",
            ).is_sync() is False
        finally:
            clear_dynamic_type_defs()

    def test_generic_parent_substituted(self) -> None:
        """A child record inheriting from a generic parent must also have
        its parent re-walked under substitution -- otherwise Child[T]'s
        registration True latches in even when Parent[T] would be False
        at the use site."""
        parent = RecordInfo(
            name="Parent",
            fields=[FieldInfo(name="val", type=TypeParamRef("U"))],
            type_params=["U"],
            is_send=True,
            is_sync=True,
        )
        attach_dynamic_type_def("Parent", TypeCategory.RECORD, record=parent)
        child = RecordInfo(
            name="Child",
            fields=[],
            type_params=["T"],
            parents=[NominalType("Parent", type_args=(TypeParamRef("T"),),
                                 _module_qname="Parent")],
            is_send=True,
            is_sync=True,
        )
        attach_dynamic_type_def("Child", TypeCategory.RECORD, record=child)
        try:
            assert NominalType(
                "Child", type_args=(INT32,), _module_qname="Child",
            ).is_send() is True
            assert NominalType(
                "Child", type_args=(PtrType(_buf()),), _module_qname="Child",
            ).is_send() is False
        finally:
            clear_dynamic_type_defs()
