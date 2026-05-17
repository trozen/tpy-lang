"""Unit tests for typesys.contains_type_param.

Canonical "does this type tree mention a type parameter" predicate used
at sema gates (overload resolution, protocol arg matching, value-type
field validation, generic method registration). Supports an optional
`names` filter.
"""

from .typesys import (
    INT32, NominalType, OptionalType, PtrType, TupleType, TypeParamKind,
    TypeParamRef, UnionType, contains_type_param, make_list,
)


class TestUnfiltered:
    """`names=None` -- any TypeParamRef counts."""

    def test_bare_type_param(self) -> None:
        assert contains_type_param(TypeParamRef("T")) is True

    def test_concrete_type(self) -> None:
        assert contains_type_param(INT32) is False

    def test_nested_in_optional(self) -> None:
        assert contains_type_param(OptionalType(TypeParamRef("T"))) is True

    def test_nested_in_list(self) -> None:
        assert contains_type_param(make_list(element_type=TypeParamRef("T"))) is True

    def test_nested_in_tuple(self) -> None:
        assert contains_type_param(TupleType((INT32, TypeParamRef("T")))) is True

    def test_nested_in_union(self) -> None:
        assert contains_type_param(UnionType((INT32, TypeParamRef("T")))) is True


class TestNamesFilter:
    """`names={"T"}` -- only TypeParamRefs whose name is in the set count."""

    def test_match_hit(self) -> None:
        assert contains_type_param(TypeParamRef("T"), names={"T"}) is True

    def test_match_miss(self) -> None:
        assert contains_type_param(TypeParamRef("U"), names={"T"}) is False

    def test_nested_match_hit(self) -> None:
        assert contains_type_param(
            OptionalType(TypeParamRef("T")), names={"T"},
        ) is True

    def test_nested_match_miss(self) -> None:
        assert contains_type_param(
            OptionalType(TypeParamRef("U")), names={"T"},
        ) is False

    def test_one_of_many(self) -> None:
        # Type tree contains both T and U; filter on {"T"} matches via T.
        assert contains_type_param(
            TupleType((TypeParamRef("U"), TypeParamRef("T"))),
            names={"T"},
        ) is True


class TestIntKindHandling:
    """INT-kind TypeParamRefs (e.g. `N` in `Array[T, N: int]`) live in
    `NominalType.type_args` but `inner_types()` filters them out for
    substitution traversals. `contains_type_param` walks `type_args`
    directly so INT-kind refs still count."""

    def test_bare_int_kind_param(self) -> None:
        n = TypeParamRef("N", kind=TypeParamKind.INT)
        assert contains_type_param(n) is True

    def test_int_kind_inside_nominal(self) -> None:
        n = TypeParamRef("N", kind=TypeParamKind.INT)
        # NominalType wrapping an INT-kind param. inner_types() would
        # filter N out; type_args walk catches it.
        wrapper = NominalType("SomeArr", type_args=(INT32, n))
        assert contains_type_param(wrapper) is True

    def test_int_kind_with_names_filter(self) -> None:
        n = TypeParamRef("N", kind=TypeParamKind.INT)
        wrapper = NominalType("SomeArr", type_args=(INT32, n))
        assert contains_type_param(wrapper, names={"N"}) is True
        assert contains_type_param(wrapper, names={"T"}) is False


class TestCompositeWrappers:
    """Multi-level wrapper chains."""

    def test_optional_of_list_of_param(self) -> None:
        t = OptionalType(make_list(element_type=TypeParamRef("T")))
        assert contains_type_param(t) is True

    def test_ptr_of_optional_of_param(self) -> None:
        t = PtrType(OptionalType(TypeParamRef("T")))
        assert contains_type_param(t) is True

    def test_deep_concrete(self) -> None:
        t = PtrType(OptionalType(make_list(element_type=INT32)))
        assert contains_type_param(t) is False
