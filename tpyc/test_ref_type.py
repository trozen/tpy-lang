"""Tests for RefType and make_ref."""

from .typesys import (
    RefType, make_ref, unwrap_ref_type, is_ref_type,
    OwnType, ReadonlyType, OptionalType, UnionType,
    VoidType, NoneType, TypeParamRef, TypeParamKind,
    INT32, STR, BOOL, VOID, FLOAT,
    NamedType, ListType,
)


class TestRefTypeConstruction:
    def test_basic(self):
        point = NamedType("Point")
        ref = RefType(point)
        assert ref.wrapped is point
        assert str(ref) == "Ref[Point]"

    def test_is_value_type(self):
        ref = RefType(NamedType("Point"))
        assert not ref.is_value_type()

    def test_is_ref_param(self):
        ref = RefType(NamedType("Point"))
        assert ref.is_ref_param()

    def test_inner_types(self):
        point = NamedType("Point")
        ref = RefType(point)
        assert ref.inner_types() == (point,)

    def test_with_inner_types(self):
        point = NamedType("Point")
        dog = NamedType("Dog")
        ref = RefType(point)
        ref2 = ref.with_inner_types((dog,))
        assert isinstance(ref2, RefType)
        assert ref2.wrapped is dog

    def test_equality(self):
        a = RefType(NamedType("Point"))
        b = RefType(NamedType("Point"))
        assert a == b

    def test_inequality(self):
        a = RefType(NamedType("Point"))
        b = RefType(NamedType("Dog"))
        assert a != b


class TestRefTypeToCpp:
    """Verify RefType produces the same C++ as the current non-value type methods."""

    def test_to_cpp_concrete(self):
        ref = RefType(NamedType("Point"))
        assert ref.to_cpp() == "Point&"

    def test_to_cpp_return(self):
        ref = RefType(NamedType("Point"))
        assert ref.to_cpp_return() == "Point&"

    def test_to_cpp_return_const(self):
        ref = RefType(NamedType("Point"))
        assert ref.to_cpp_return_const() == "const Point&"

    def test_to_cpp_param_type(self):
        ref = RefType(NamedType("Point"))
        assert ref.to_cpp_param_type() == "Point&"

    def test_to_cpp_param(self):
        ref = RefType(NamedType("Point"))
        assert ref.to_cpp_param("x") == "Point& x"

    def test_to_cpp_const_param(self):
        ref = RefType(NamedType("Point"))
        assert ref.to_cpp_const_param("x") == "const Point& x"

    def test_to_cpp_stored(self):
        ref = RefType(NamedType("Point"))
        assert ref.to_cpp_stored() == "::tpy::val_or_ref<Point>"

    def test_to_cpp_stored_list(self):
        ref = RefType(ListType(INT32))
        assert ref.to_cpp_stored() == "::tpy::val_or_ref<std::vector<int32_t>>"


class TestRefTypeParamRef:
    """Verify RefType with TypeParamRef uses C++ trait aliases."""

    def test_to_cpp(self):
        ref = RefType(TypeParamRef("T"))
        assert ref.to_cpp() == "::tpy::val_or_ref_t<T>"

    def test_to_cpp_return(self):
        ref = RefType(TypeParamRef("T"))
        assert ref.to_cpp_return() == "::tpy::val_or_ref_t<T>"

    def test_to_cpp_return_const(self):
        ref = RefType(TypeParamRef("T"))
        assert ref.to_cpp_return_const() == "::tpy::val_or_cref_t<T>"

    def test_to_cpp_param_type(self):
        ref = RefType(TypeParamRef("T"))
        assert ref.to_cpp_param_type() == "::tpy::param_val_or_ref_t<T>"

    def test_to_cpp_stored(self):
        ref = RefType(TypeParamRef("T"))
        assert ref.to_cpp_stored() == "::tpy::val_or_ref<T>"


class TestMakeRef:
    """Test the smart constructor that decides when to wrap."""

    def test_non_value_type_wrapped(self):
        point = NamedType("Point")
        result = make_ref(point)
        assert isinstance(result, RefType)
        assert result.wrapped is point

    def test_list_wrapped(self):
        lst = ListType(INT32)
        result = make_ref(lst)
        assert isinstance(result, RefType)
        assert result.wrapped is lst

    def test_value_type_not_wrapped(self):
        assert make_ref(INT32) is INT32
        assert make_ref(STR) is STR
        assert make_ref(BOOL) is BOOL
        assert make_ref(FLOAT) is FLOAT

    def test_own_not_wrapped(self):
        own = OwnType(NamedType("Point"))
        assert make_ref(own) is own

    def test_readonly_not_wrapped(self):
        ro = ReadonlyType(NamedType("Point"))
        assert make_ref(ro) is ro

    def test_optional_not_wrapped(self):
        opt = OptionalType(NamedType("Point"))
        assert make_ref(opt) is opt

    def test_union_not_wrapped(self):
        union = UnionType((NamedType("Dog"), NamedType("Cat")))
        assert make_ref(union) is union

    def test_void_not_wrapped(self):
        assert make_ref(VOID) is VOID

    def test_none_not_wrapped(self):
        none = NoneType()
        assert make_ref(none) is none

    def test_ref_not_double_wrapped(self):
        ref = RefType(NamedType("Point"))
        assert make_ref(ref) is ref

    def test_type_param_ref_wrapped(self):
        tpr = TypeParamRef("T")
        result = make_ref(tpr)
        assert isinstance(result, RefType)
        assert result.wrapped is tpr

    def test_type_param_ref_int_kind_not_wrapped(self):
        tpr = TypeParamRef("N", kind=TypeParamKind.INT)
        assert make_ref(tpr) is tpr

    def test_type_param_ref_value_bound_not_wrapped(self):
        bound = NamedType("ValueType", is_protocol=True,
                          _module_qname="tpy.ValueType")
        tpr = TypeParamRef("T", bound=bound)
        # is_value_type() returns True when bounded to tpy.ValueType
        assert tpr.is_value_type()
        assert make_ref(tpr) is tpr


class TestUnwrapRefType:
    def test_unwrap_ref(self):
        point = NamedType("Point")
        ref = RefType(point)
        assert unwrap_ref_type(ref) is point

    def test_unwrap_non_ref(self):
        point = NamedType("Point")
        assert unwrap_ref_type(point) is point

    def test_unwrap_value_type(self):
        assert unwrap_ref_type(INT32) is INT32


class TestIsRefType:
    def test_ref(self):
        assert is_ref_type(RefType(NamedType("Point")))

    def test_non_ref(self):
        assert not is_ref_type(NamedType("Point"))
        assert not is_ref_type(INT32)
        assert not is_ref_type(OwnType(NamedType("Point")))


class TestToCppStored:
    """Test base class to_cpp_stored() default behavior."""

    def test_value_type_stored_same_as_to_cpp(self):
        assert INT32.to_cpp_stored() == INT32.to_cpp()
        assert STR.to_cpp_stored() == STR.to_cpp()

    def test_non_value_type_stored_same_as_to_cpp(self):
        point = NamedType("Point")
        assert point.to_cpp_stored() == point.to_cpp()

    def test_ref_type_stored_uses_val_or_ref(self):
        ref = RefType(NamedType("Point"))
        assert ref.to_cpp_stored() == "::tpy::val_or_ref<Point>"
        assert ref.to_cpp_stored() != ref.to_cpp()
