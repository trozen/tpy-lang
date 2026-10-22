"""Tests for ValueForm / TpyType.value_form() and the TupleType predicates
expressed through it (has_ref_elements, has_pointer_repr_optional_element,
has_pointer_repr_element).

The reference predicates below pin the boolean semantics independently of
value_form(), including the force_pointer_repr value-inner Optional corner
where is_value_type() and uses_pointer_repr() disagree.
"""

from .typesys import (
    ValueForm, TupleType, OptionalType, OwnType, TypeParamRef, TypeParamKind,
    RefType, NominalType, ReadonlyType, UnionType, INT32, INT64, BOOL, STR,
    CHAR, BIGINT,
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


class TestNestedPointerReprElement:
    """has_nested_pointer_repr_element: the nested borrow-form question."""

    def test_direct_element_still_counts(self):
        assert TupleType((INT32, RECORD)).has_nested_pointer_repr_element()

    def test_nested_element_counts(self):
        tt = TupleType((INT32, TupleType((INT32, RECORD))))
        assert tt.has_nested_pointer_repr_element()
        # The form question stays shallow: the outer tuple has no pointer slot
        # of its own, so codegen must keep rendering it as plain storage.
        assert not tt.has_pointer_repr_element()

    def test_three_levels(self):
        tt = TupleType((INT32, TupleType((INT32, TupleType((INT32, RECORD))))))
        assert tt.has_nested_pointer_repr_element()

    def test_nested_values_only_is_not(self):
        tt = TupleType((INT32, TupleType((INT32, STR))))
        assert not tt.has_nested_pointer_repr_element()

    def test_nested_own_is_not(self):
        # An Own element is moved into the slot, never copied out of a borrow.
        tt = TupleType((INT32, TupleType((INT32, OwnType(RECORD)))))
        assert not tt.has_nested_pointer_repr_element()


class TestPointerOrProxyElement:
    """has_pointer_or_proxy_element: a direct element whose borrow form is a
    pointer or an open-T proxy, so a binding takes its source's form."""

    VALUE_T = TypeParamRef("T", bound=NominalType(
        "ValueType", is_protocol=True, _module_qname="tpy.ValueType"))

    def test_open_t_counts(self):
        assert TupleType((TypeParamRef("T"), INT32)).has_pointer_or_proxy_element()
        assert TupleType((TypeParamRef("T"),)).has_pointer_or_proxy_element()

    def test_readonly_and_ref_open_t_count(self):
        t = TypeParamRef("T")
        assert TupleType((ReadonlyType(t), INT32)).has_pointer_or_proxy_element()
        assert TupleType((RefType(t), INT32)).has_pointer_or_proxy_element()

    def test_pointer_repr_counts(self):
        assert TupleType((RECORD, INT32)).has_pointer_or_proxy_element()
        assert TupleType((OptionalType(RECORD),)).has_pointer_or_proxy_element()

    def test_value_bounded_and_int_params_do_not(self):
        # One form at every instantiation.
        assert not TupleType((self.VALUE_T, INT32)).has_pointer_or_proxy_element()
        n = TypeParamRef("N", kind=TypeParamKind.INT)
        assert not TupleType((n, INT32)).has_pointer_or_proxy_element()

    def test_nested_union_own_and_values_do_not(self):
        t = TypeParamRef("T")
        assert not TupleType((TupleType((t, INT32)), INT32)).has_pointer_or_proxy_element()
        assert not TupleType((OwnType(t), INT32)).has_pointer_or_proxy_element()
        assert not TupleType((INT32, STR)).has_pointer_or_proxy_element()
        assert not TupleType(
            (UnionType((RECORD, NominalType("Other"))), INT32)
        ).has_pointer_or_proxy_element()

    def test_open_t_beside_an_owning_element_counts(self):
        # The borrow form differs from storage whatever else the tuple
        # holds; whether a binding takes it is has_copy_free_borrow_form's.
        assert TupleType((TypeParamRef("T"), STR)).has_pointer_or_proxy_element()
        assert not TupleType((TypeParamRef("T"), STR)).has_copy_free_borrow_form()

    def test_superset_of_pointer_repr(self):
        for elems in TestPredicateEquivalence.CASES:
            tt = TupleType(elems)
            if tt.has_pointer_repr_element():
                assert tt.has_pointer_or_proxy_element(), elems


class TestCopyFreeBorrowForm:
    """has_copy_free_borrow_form: every element a concrete pointer or a
    bufferless scalar, or every element an open-T proxy or a bufferless
    scalar -- a copy costs nothing beyond each element as a scalar local."""

    def test_open_t_beside_scalars(self):
        t = TypeParamRef("T")
        assert TupleType((t, INT32)).has_copy_free_borrow_form()
        assert TupleType((t, t, BOOL)).has_copy_free_borrow_form()
        assert TupleType((ReadonlyType(t), INT64)).has_copy_free_borrow_form()
        assert TupleType((t, CHAR)).has_copy_free_borrow_form()

    def test_concrete_pointer_tuple(self):
        assert TupleType((RECORD, INT32)).has_copy_free_borrow_form()
        assert TupleType((RECORD, OptionalType(RECORD))).has_copy_free_borrow_form()
        assert TupleType((RECORD, OptionalType(INT32), CHAR)).has_copy_free_borrow_form()
        # An `int` element copies its limbs exactly as an `int` local does,
        # so the tuple copy costs nothing the scalar would not.
        assert TupleType((RECORD, BIGINT)).has_copy_free_borrow_form()
        assert not TupleType((INT32, STR)).has_copy_free_borrow_form()
        assert not TupleType((INT32, BOOL)).has_copy_free_borrow_form()

    def test_concrete_pointer_beside_a_buffer_is_outside(self):
        # A copy of `std::tuple<A*, std::string>` copies the string.
        assert not TupleType((RECORD, STR)).has_copy_free_borrow_form()
        assert not TupleType((RECORD, OwnType(RECORD))).has_copy_free_borrow_form()

    def test_open_t_beside_a_pointer_or_buffer_is_outside(self):
        t = TypeParamRef("T")
        assert not TupleType((RECORD, t)).has_copy_free_borrow_form()
        assert not TupleType((RECORD, t, INT32)).has_copy_free_borrow_form()
        assert not TupleType((t, STR)).has_copy_free_borrow_form()
