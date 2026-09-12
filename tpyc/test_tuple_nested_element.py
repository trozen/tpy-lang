"""Tests for TupleType's element-tree predicates.

Both walk the same tree through one private helper, but ask different
questions of an element -- ownership looks under the element's wrappers,
the borrow form looks at the element as written.
"""

from .typesys import (
    TupleType, OwnType, OptionalType, ReadonlyType, NominalType,
    INT32, STR, make_list,
)

P = NominalType("Point")


class TestHasNestedOwnElement:
    def test_direct(self):
        assert TupleType((OwnType(P), INT32)).has_nested_own_element()

    def test_under_optional(self):
        # `Own[Point] | None` still owns its payload -- the wrapper does not
        # make the element a borrow.
        assert TupleType(
            (OptionalType(OwnType(P)), INT32)).has_nested_own_element()

    def test_under_readonly(self):
        assert TupleType(
            (ReadonlyType(OwnType(P)), INT32)).has_nested_own_element()

    def test_in_nested_tuple(self):
        assert TupleType(
            (TupleType((OwnType(P), INT32)), INT32)).has_nested_own_element()

    def test_wrapped_own_in_nested_tuple(self):
        assert TupleType((TupleType((OptionalType(OwnType(P)), INT32)),
                          INT32)).has_nested_own_element()

    def test_own_container(self):
        assert TupleType(
            (OwnType(make_list(INT32)), INT32)).has_nested_own_element()

    def test_borrow_element_is_not_own(self):
        assert not TupleType((P, INT32)).has_nested_own_element()

    def test_optional_borrow_is_not_own(self):
        assert not TupleType((OptionalType(P), INT32)).has_nested_own_element()

    def test_values_only(self):
        assert not TupleType((INT32, STR)).has_nested_own_element()


class TestHasNestedPointerReprElement:
    """The ownership fix must not reach this twin: an `Own[T]` element is
    storage, not a bare `T*`, however deep the payload sits."""

    def test_plain_reference(self):
        assert TupleType((P, INT32)).has_nested_pointer_repr_element()

    def test_optional_reference(self):
        assert TupleType(
            (OptionalType(P), INT32)).has_nested_pointer_repr_element()

    def test_in_nested_tuple(self):
        assert TupleType(
            (TupleType((P, INT32)), INT32)).has_nested_pointer_repr_element()

    def test_own_element_is_not_pointer_repr(self):
        assert not TupleType(
            (OwnType(P), INT32)).has_nested_pointer_repr_element()

    def test_wrapped_own_element_is_not_pointer_repr(self):
        assert not TupleType(
            (OptionalType(OwnType(P)), INT32)
        ).has_nested_pointer_repr_element()

    def test_own_of_tuple_descends_to_its_elements(self):
        assert TupleType((OwnType(TupleType((P, INT32))),
                          INT32)).has_nested_pointer_repr_element()

    def test_values_only(self):
        assert not TupleType(
            (INT32, STR)).has_nested_pointer_repr_element()
