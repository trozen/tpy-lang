"""Tests for ValueForm / TpyType.value_form() and the TupleType predicates
expressed through it (has_ref_elements, has_pointer_repr_optional_element,
has_pointer_repr_element).

The reference predicates below pin the boolean semantics independently of
value_form(), including the force_pointer_repr value-inner Optional corner
where is_value_type() and uses_pointer_repr() disagree.
"""

from .typesys import (
    ValueForm, TupleType, OptionalType, OwnType, TypeParamRef, TypeParamKind,
    RefType, NominalType, INT32, STR,
)

RECORD = NominalType("Point")  # unregistered nominal -> non-value


def _old_has_ref_elements(tt: TupleType) -> bool:
    return any(
        not et.is_value_type() and not isinstance(et, (OwnType, TypeParamRef))
        for et in tt.element_types
    )


def _old_has_pointer_repr_optional_element(tt: TupleType) -> bool:
    return any(
        isinstance(et, OptionalType) and et.uses_pointer_repr()
        for et in tt.element_types
    )


class TestValueForm:
    def test_value(self):
        assert INT32.value_form() is ValueForm.VALUE
        assert STR.value_form() is ValueForm.VALUE

    def test_borrow_ref_record(self):
        assert RECORD.value_form() is ValueForm.BORROW_REF

    def test_borrow_ref_via_reftype(self):
        assert RefType(RECORD).value_form() is ValueForm.BORROW_REF

    def test_own(self):
        assert OwnType(RECORD).value_form() is ValueForm.OWN

    def test_type_param_unbounded(self):
        tp = TypeParamRef("T")
        assert not tp.is_value_type()
        assert tp.value_form() is ValueForm.TYPE_PARAM

    def test_type_param_int_is_value(self):
        tp = TypeParamRef("N", kind=TypeParamKind.INT)
        assert tp.value_form() is ValueForm.VALUE

    def test_optional_nonvalue_is_ptr(self):
        assert OptionalType(RECORD).value_form() is ValueForm.PTR_OPTIONAL

    def test_optional_value_is_value(self):
        assert OptionalType(INT32).value_form() is ValueForm.VALUE

    def test_force_pointer_repr_value_inner_is_ptr(self):
        # Value-semantics inner but pointer-shaped: the form must key on
        # uses_pointer_repr, not is_value_type, or this corner misclassifies
        # as VALUE.
        f = OptionalType(INT32, force_pointer_repr=True)
        assert f.is_value_type()
        assert f.uses_pointer_repr()
        assert f.value_form() is ValueForm.PTR_OPTIONAL


class TestPredicateEquivalence:
    CASES = [
        (INT32, STR),
        (INT32, RECORD),
        (INT32, OptionalType(RECORD)),
        (INT32, OptionalType(INT32)),
        (INT32, OwnType(RECORD)),
        (INT32, TypeParamRef("T")),
        (INT32, OptionalType(INT32, force_pointer_repr=True)),
        (RECORD, OptionalType(RECORD)),
    ]

    def test_has_ref_elements_matches_old(self):
        for elems in self.CASES:
            tt = TupleType(elems)
            assert tt.has_ref_elements() == _old_has_ref_elements(tt), elems

    def test_has_pointer_repr_optional_element_matches_old(self):
        for elems in self.CASES:
            tt = TupleType(elems)
            assert (tt.has_pointer_repr_optional_element()
                    == _old_has_pointer_repr_optional_element(tt)), elems

    def test_force_value_corner_split(self):
        # The force-value Optional: counted by has_pointer_repr_optional_element
        # (pointer-shaped) but NOT by has_ref_elements (value-semantics).
        tt = TupleType((INT32, OptionalType(INT32, force_pointer_repr=True)))
        assert tt.has_pointer_repr_optional_element()
        assert not tt.has_ref_elements()


class TestPointerReprElement:
    """has_pointer_repr_element: the unified `T*`-borrow-form predicate."""

    def test_record_is_pointer_repr(self):
        assert TupleType((INT32, RECORD)).has_pointer_repr_element()

    def test_ref_wrapped_record_is_pointer_repr(self):
        assert TupleType((INT32, RefType(RECORD))).has_pointer_repr_element()

    def test_ptr_optional_is_pointer_repr(self):
        assert TupleType((INT32, OptionalType(RECORD))).has_pointer_repr_element()

    def test_value_only_is_not(self):
        assert not TupleType((INT32, STR)).has_pointer_repr_element()

    def test_own_is_not(self):
        # Own elements are stored/moved by value, never a borrow pointer.
        assert not TupleType((INT32, OwnType(RECORD))).has_pointer_repr_element()

    def test_type_param_is_not(self):
        # Generic elements use the val_or_ptr_t proxy, decided at
        # instantiation -- not a bare T* at the source level.
        assert not TupleType((INT32, TypeParamRef("T"))).has_pointer_repr_element()
        assert not TupleType(
            (INT32, RefType(TypeParamRef("T")))).has_pointer_repr_element()

    def test_superset_of_optional_predicate(self):
        for elems in TestPredicateEquivalence.CASES:
            tt = TupleType(elems)
            if tt.has_pointer_repr_optional_element():
                assert tt.has_pointer_repr_element(), elems

    def test_mixed_elements_are_pointer_repr(self):
        # A tuple mixing a nullable Optional slot with a plain reference slot
        # is one pointer-repr tuple; the single dest-shape-dispatching
        # tuple_to_storage converter handles both slot kinds.
        tt = TupleType((OptionalType(RECORD), RECORD))
        assert tt.has_pointer_repr_element()
