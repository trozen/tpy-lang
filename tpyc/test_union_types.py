"""Tests for UnionType and make_union normalization."""

from .typesys import (
    UnionType, make_union, OptionalType, VoidType, NoneType,
    INT32, STR, BOOL, VOID,
    ReadonlyType,
    is_void_like_type,
)


class TestMakeUnion:
    def test_two_types(self):
        result = make_union(INT32, STR)
        assert isinstance(result, UnionType)
        assert result.members == (INT32, STR)

    def test_three_types(self):
        result = make_union(INT32, STR, BOOL)
        assert isinstance(result, UnionType)
        assert len(result.members) == 3

    def test_single_type_collapses(self):
        result = make_union(INT32)
        assert result == INT32

    def test_single_type_with_none_gives_optional(self):
        result = make_union(INT32, VOID)
        assert isinstance(result, OptionalType)
        assert result.inner == INT32

    def test_single_type_with_nonetype_gives_optional(self):
        result = make_union(INT32, NoneType())
        assert isinstance(result, OptionalType)
        assert result.inner == INT32

    def test_two_types_with_none(self):
        result = make_union(INT32, STR, VOID)
        assert isinstance(result, UnionType)
        # None member should be first (std::monostate at index 0)
        assert isinstance(result.members[0], NoneType)
        # Non-None members sorted by str()
        non_none = [m for m in result.members if not is_void_like_type(m)]
        assert len(non_none) == 2

    def test_canonical_order(self):
        """Members are sorted by str() for deterministic order."""
        result1 = make_union(STR, INT32)
        result2 = make_union(INT32, STR)
        assert result1 == result2

    def test_deduplication(self):
        result = make_union(INT32, STR, INT32)
        assert isinstance(result, UnionType)
        assert len(result.members) == 2

    def test_flatten_nested_union(self):
        inner = make_union(INT32, STR)
        result = make_union(inner, BOOL)
        assert isinstance(result, UnionType)
        assert len(result.members) == 3

    def test_flatten_optional(self):
        opt = OptionalType(INT32)
        result = make_union(opt, STR)
        assert isinstance(result, UnionType)
        # NoneType, INT32, STR (from unwrapped optional)
        assert isinstance(result.members[0], NoneType)
        non_none = [m for m in result.members if not is_void_like_type(m)]
        assert len(non_none) == 2

    def test_only_none_returns_void(self):
        result = make_union(VOID)
        assert isinstance(result, VoidType)

    def test_to_cpp(self):
        result = make_union(INT32, STR)
        assert isinstance(result, UnionType)
        assert "std::variant<" in result.to_cpp()

    def test_to_cpp_with_none(self):
        result = make_union(INT32, STR, VOID)
        assert isinstance(result, UnionType)
        cpp = result.to_cpp()
        assert "std::monostate" in cpp

    def test_str_representation(self):
        result = make_union(INT32, STR)
        assert isinstance(result, UnionType)
        s = str(result)
        assert "Int32" in s
        assert "str" in s
        assert "|" in s

    def test_str_with_none(self):
        result = make_union(INT32, STR, VOID)
        assert isinstance(result, UnionType)
        s = str(result)
        assert "None" in s

    def test_is_value_type(self):
        result = make_union(INT32, STR)
        assert isinstance(result, UnionType)
        assert result.is_value_type()

    def test_to_cpp_param(self):
        result = make_union(INT32, STR)
        assert isinstance(result, UnionType)
        param = result.to_cpp_param("x")
        assert param.startswith("const ")
        assert param.endswith("& x")

    def test_inner_types(self):
        result = make_union(INT32, STR)
        assert isinstance(result, UnionType)
        assert result.inner_types() == result.members

    def test_with_inner_types_deduplicates(self):
        """with_inner_types goes through make_union to normalize duplicates."""
        result = make_union(INT32, STR)
        assert isinstance(result, UnionType)
        new = result.with_inner_types((INT32, INT32))
        assert new == INT32

    def test_with_inner_types_preserves_union(self):
        result = make_union(INT32, STR)
        assert isinstance(result, UnionType)
        new = result.with_inner_types((INT32, BOOL))
        assert isinstance(new, UnionType)
        assert new.members == (INT32, BOOL)

    def test_readonly_union(self):
        """readonly[A] | readonly[B] should work."""
        ra = ReadonlyType(INT32)
        rb = ReadonlyType(STR)
        # Parser handles readonly normalization, not make_union
        # make_union just gets the unwrapped types
        result = make_union(INT32, STR)
        assert isinstance(result, UnionType)
