"""Tests for the syntactic type-ref walker (Phase F.3b.1).

Verifies that `Parser._parse_type_ref` produces the expected TypeRefNode
shape for representative Python type annotations. The resolver
(F.3b.2+) is out of scope for this test -- here we only pin the
syntactic output.
"""

from __future__ import annotations
import ast

import pytest

from .parse import (
    Parser, ParseError,
    TpyTypeRef, TpyUnionRef, TpyCallableRef, TpyLiteralRef,
)
from .typesys import LiteralValue, LiteralTag


def _make_parser() -> Parser:
    """Create a parser with imports that enable all structural-wrapper
    recognition paths plus primitive names."""
    parser = Parser()
    parser.parse(
        "from tpy import Ptr, Own, readonly, auto_readonly, auto_own, Fn\n"
        "from tpy import (int8, int16, int32, int64, uint8, uint16, uint32, uint64,\n"
        "                 char, float32, String, StrView, BytesView,\n"
        "                 basic_slice, slice, Array, Span)\n"
        "from typing import Optional, Final, Callable, Literal, Self\n"
        "pass\n",
        module_name="test_module",
    )
    return parser


def _parse_ann(parser: Parser, annotation_src: str):
    """Parse a type annotation source fragment into a TypeRefNode."""
    tree = ast.parse(f"x: {annotation_src}\n")
    ann_node = tree.body[0].annotation
    return parser._parse_type_ref(ann_node)


class TestLeafNames:
    def test_bare_name(self):
        ref = _parse_ann(_make_parser(), "int32")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "int32"
        assert ref.args == ()

    def test_user_record_name(self):
        ref = _parse_ann(_make_parser(), "MyRecord")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "MyRecord"
        assert ref.args == ()

    def test_none_constant(self):
        ref = _parse_ann(_make_parser(), "None")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "None"

    def test_dotted_attribute(self):
        ref = _parse_ann(_make_parser(), "Outer.Inner")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "Outer.Inner"
        assert ref.args == ()

    def test_deep_dotted(self):
        ref = _parse_ann(_make_parser(), "a.b.c.D")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "a.b.c.D"


class TestGenerics:
    def test_list_of_int(self):
        ref = _parse_ann(_make_parser(), "list[int32]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "list"
        assert len(ref.args) == 1
        inner = ref.args[0]
        assert isinstance(inner, TpyTypeRef) and inner.name == "int32"

    def test_dict_of_str_int(self):
        ref = _parse_ann(_make_parser(), "dict[str, int32]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "dict"
        assert len(ref.args) == 2
        k, v = ref.args
        assert isinstance(k, TpyTypeRef) and k.name == "str"
        assert isinstance(v, TpyTypeRef) and v.name == "int32"

    def test_array_with_int_size(self):
        ref = _parse_ann(_make_parser(), "Array[int32, 8]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "Array"
        assert len(ref.args) == 2
        elem, size = ref.args
        assert isinstance(elem, TpyTypeRef) and elem.name == "int32"
        assert size == 8

    def test_nested_generic(self):
        ref = _parse_ann(_make_parser(), "list[dict[str, int32]]")
        assert isinstance(ref, TpyTypeRef) and ref.name == "list"
        outer_arg = ref.args[0]
        assert isinstance(outer_arg, TpyTypeRef) and outer_arg.name == "dict"
        k, v = outer_arg.args
        assert isinstance(k, TpyTypeRef) and k.name == "str"
        assert isinstance(v, TpyTypeRef) and v.name == "int32"

    def test_user_generic_record(self):
        ref = _parse_ann(_make_parser(), "Stack[int32]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "Stack"
        assert len(ref.args) == 1
        assert isinstance(ref.args[0], TpyTypeRef) and ref.args[0].name == "int32"


class TestStructuralWrappers:
    # Canonical structural wrappers use qualified names ("tpy:Ptr",
    # "typing:Optional", ...) so they don't collide with raw user-source
    # names like "Ptr" or "Optional" (the resolver's wrapper dispatch must
    # only fire when the walker confirmed module resolution).

    def test_ptr(self):
        ref = _parse_ann(_make_parser(), "Ptr[int32]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "tpy:Ptr"
        assert len(ref.args) == 1
        assert isinstance(ref.args[0], TpyTypeRef) and ref.args[0].name == "int32"

    def test_own(self):
        ref = _parse_ann(_make_parser(), "Own[MyRecord]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "tpy:Own"

    def test_readonly(self):
        ref = _parse_ann(_make_parser(), "readonly[int32]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "tpy:readonly"

    def test_auto_readonly(self):
        ref = _parse_ann(_make_parser(), "auto_readonly[int32]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "tpy:auto_readonly"

    def test_auto_own(self):
        ref = _parse_ann(_make_parser(), "auto_own[MyRecord]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "tpy:auto_own"

    def test_optional(self):
        ref = _parse_ann(_make_parser(), "Optional[int32]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "typing:Optional"

    def test_final(self):
        ref = _parse_ann(_make_parser(), "Final[int32]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "typing:Final"

    def test_ptr_of_readonly(self):
        # Ptr[readonly[int32]] -- readonly nested under Ptr.
        # The resolver applies Ptr.is_readonly; walker just records structure.
        ref = _parse_ann(_make_parser(), "Ptr[readonly[int32]]")
        assert isinstance(ref, TpyTypeRef) and ref.name == "tpy:Ptr"
        inner = ref.args[0]
        assert isinstance(inner, TpyTypeRef) and inner.name == "tpy:readonly"
        leaf = inner.args[0]
        assert isinstance(leaf, TpyTypeRef) and leaf.name == "int32"


class TestTuple:
    def test_empty_tuple_allowed_syntactically(self):
        # Parser-level: tuple with no args is syntactically valid here; the
        # resolver raises.
        ref = _parse_ann(_make_parser(), "tuple[int32]")
        assert isinstance(ref, TpyTypeRef) and ref.name == "builtins:tuple"
        assert len(ref.args) == 1

    def test_tuple_multiple(self):
        ref = _parse_ann(_make_parser(), "tuple[int32, str, bool]")
        assert isinstance(ref, TpyTypeRef) and ref.name == "builtins:tuple"
        assert len(ref.args) == 3
        assert all(isinstance(a, TpyTypeRef) for a in ref.args)


class TestUnion:
    def test_simple_union(self):
        ref = _parse_ann(_make_parser(), "int32 | str")
        assert isinstance(ref, TpyUnionRef)
        assert len(ref.members) == 2
        a, b = ref.members
        assert isinstance(a, TpyTypeRef) and a.name == "int32"
        assert isinstance(b, TpyTypeRef) and b.name == "str"

    def test_union_with_none(self):
        ref = _parse_ann(_make_parser(), "int32 | None")
        assert isinstance(ref, TpyUnionRef)
        assert len(ref.members) == 2

    def test_three_way_union(self):
        ref = _parse_ann(_make_parser(), "int32 | str | bool")
        assert isinstance(ref, TpyUnionRef)
        assert len(ref.members) == 3


class TestCallable:
    def test_callable(self):
        ref = _parse_ann(_make_parser(), "Callable[[int32, str], bool]")
        assert isinstance(ref, TpyCallableRef)
        assert ref.kind == "Callable"
        assert len(ref.params) == 2
        assert isinstance(ref.params[0], TpyTypeRef) and ref.params[0].name == "int32"
        assert isinstance(ref.params[1], TpyTypeRef) and ref.params[1].name == "str"
        assert isinstance(ref.return_type, TpyTypeRef)
        assert ref.return_type.name == "bool"

    def test_fn(self):
        ref = _parse_ann(_make_parser(), "Fn[[int32], bool]")
        assert isinstance(ref, TpyCallableRef)
        assert ref.kind == "Fn"
        assert len(ref.params) == 1

    def test_callable_no_params(self):
        ref = _parse_ann(_make_parser(), "Callable[[], None]")
        assert isinstance(ref, TpyCallableRef)
        assert ref.kind == "Callable"
        assert ref.params == ()

    def test_callable_malformed_missing_list(self):
        with pytest.raises(ParseError, match="parameter types must be a list"):
            _parse_ann(_make_parser(), "Callable[int32, bool]")


class TestLiteral:
    def test_str_literal(self):
        ref = _parse_ann(_make_parser(), 'Literal["a", "b"]')
        assert isinstance(ref, TpyLiteralRef)
        assert ref.values == (LiteralValue(LiteralTag.STR, "a"), LiteralValue(LiteralTag.STR, "b"))

    def test_int_literal(self):
        ref = _parse_ann(_make_parser(), "Literal[1, 2, 3]")
        assert isinstance(ref, TpyLiteralRef)
        assert ref.values == (
            LiteralValue(LiteralTag.INT, 1), LiteralValue(LiteralTag.INT, 2), LiteralValue(LiteralTag.INT, 3),
        )

    def test_bool_literal(self):
        ref = _parse_ann(_make_parser(), "Literal[True, False]")
        assert isinstance(ref, TpyLiteralRef)
        assert ref.values == (LiteralValue(LiteralTag.BOOL, True), LiteralValue(LiteralTag.BOOL, False))

    def test_negative_int_literal(self):
        ref = _parse_ann(_make_parser(), "Literal[-5]")
        assert isinstance(ref, TpyLiteralRef)
        assert ref.values == (LiteralValue(LiteralTag.INT, -5),)

    def test_mixed_types_rejected(self):
        with pytest.raises(ParseError, match="Literal cannot mix value types"):
            _parse_ann(_make_parser(), 'Literal["a", 1]')


class TestUnknownName:
    def test_unknown_name_does_not_raise(self):
        # The resolver is what raises "Unknown type". The walker is permissive.
        ref = _parse_ann(_make_parser(), "TotallyUnknownName")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "TotallyUnknownName"

    def test_unknown_generic_does_not_raise(self):
        ref = _parse_ann(_make_parser(), "UnknownGeneric[int32]")
        assert isinstance(ref, TpyTypeRef)
        assert ref.name == "UnknownGeneric"


class TestLoc:
    def test_loc_populated(self):
        ref = _parse_ann(_make_parser(), "int32")
        assert ref.loc is not None
        assert ref.loc.line == 1


# --- F.3b.2: equivalence of _resolve_type_ref_impl with _parse_type_annotation
# For every case below, walking + resolving a TypeRefNode must produce the
# same TpyType as the direct parse. The resolver is dead code at this point
# (no call sites), but this test pins equivalence before F.3b.3 flips the
# entry point.

def _ann_tree(annotation_src: str):
    tree = ast.parse(f"x: {annotation_src}\n")
    return tree.body[0].annotation


def _equiv(parser: Parser, annotation_src: str, type_param_scope=None):
    """Assert _parse_type_annotation == _resolve_type_ref_impl(_parse_type_ref)."""
    node = _ann_tree(annotation_src)
    expected = parser._parse_type_annotation(node, type_param_scope)
    ref = parser._parse_type_ref(node, type_param_scope)
    actual = parser._resolve_type_ref_impl(ref, type_param_scope)
    assert expected == actual, (
        f"Mismatch for {annotation_src!r}:\n  expected: {expected}\n  actual:   {actual}"
    )
    return expected


class TestResolverEquivalence:
    @pytest.mark.parametrize("src", [
        # Primitives
        "int32", "int8", "int16", "int64",
        "uint8", "uint16", "uint32", "uint64",
        "int", "float", "float32", "bool",
        "str", "bytes", "bytearray", "String", "StrView", "BytesView",
        "char", "None",
        # Slice types
        "basic_slice", "slice",
    ])
    def test_primitives(self, src):
        _equiv(_make_parser(), src)

    @pytest.mark.parametrize("src", [
        "list[int32]",
        "list[str]",
        "dict[str, int32]",
        "dict[int32, str]",
        "set[int32]",
        "tuple[int32]",
        "tuple[int32, str]",
        "tuple[int32, str, bool]",
        "Array[int32, 8]",
        "Array[str, 16]",
        "Span[int32]",
        "Span[readonly[int32]]",
    ])
    def test_generics(self, src):
        _equiv(_make_parser(), src)

    @pytest.mark.parametrize("src", [
        "Ptr[int32]",
        "Ptr[readonly[int32]]",
        "Own[list[int32]]",
        "readonly[int32]",
        "auto_readonly[int32]",
        "auto_own[list[int32]]",
        "Optional[int32]",
        "Optional[Ptr[int32]]",
        "Final[int32]",
    ])
    def test_structural_wrappers(self, src):
        _equiv(_make_parser(), src)

    @pytest.mark.parametrize("src", [
        "int32 | str",
        "int32 | None",
        "int32 | str | bool",
        "Ptr[int32] | None",
        "Optional[int32]",
        "list[int32] | None",
    ])
    def test_unions(self, src):
        _equiv(_make_parser(), src)

    @pytest.mark.parametrize("src", [
        "Callable[[int32], bool]",
        "Callable[[int32, str], bool]",
        "Callable[[], None]",
        "Fn[[int32], bool]",
        "Fn[[], None]",
    ])
    def test_callable(self, src):
        _equiv(_make_parser(), src)

    @pytest.mark.parametrize("src", [
        'Literal["a", "b"]',
        "Literal[1, 2, 3]",
        "Literal[-5]",
        "Literal[True, False]",
    ])
    def test_literal(self, src):
        _equiv(_make_parser(), src)

    @pytest.mark.parametrize("src", [
        # Nested generics + wrappers
        "list[Ptr[int32]]",
        "dict[str, list[int32]]",
        "Optional[list[int32]]",
        "list[tuple[int32, str]]",
        "Callable[[list[int32]], Optional[str]]",
    ])
    def test_nested(self, src):
        _equiv(_make_parser(), src)

    def test_type_param_ref(self):
        parser = _make_parser()
        from .typesys import TypeParamKind
        scope = {"T": TypeParamKind.TYPE}
        _equiv(parser, "T", scope)
        _equiv(parser, "list[T]", scope)
        _equiv(parser, "Ptr[T]", scope)
        _equiv(parser, "T | None", scope)
        # Type params in nested / variable-arity forms
        _equiv(parser, "dict[str, T]", scope)
        _equiv(parser, "Optional[T]", scope)
        _equiv(parser, "Fn[[T], bool]", scope)
        _equiv(parser, "auto_readonly[T]", scope)
        _equiv(parser, "Own[T]", scope)

    def test_int_type_param_ref(self):
        parser = _make_parser()
        from .typesys import TypeParamKind
        scope = {"T": TypeParamKind.TYPE, "N": TypeParamKind.INT}
        _equiv(parser, "Array[T, N]", scope)

    @pytest.mark.parametrize("src", [
        "Self",
        "Ptr[Self]",
        "Optional[Self]",
        "list[Self]",
    ])
    def test_self_type(self, src):
        _equiv(_make_parser(), src)


class TestResolverErrors:
    def test_unknown_type_raises(self):
        from .typesys import TypeParamKind
        parser = _make_parser()
        node = _ann_tree("TotallyUnknownName")
        ref = parser._parse_type_ref(node)
        with pytest.raises(ParseError, match="Unknown type"):
            parser._resolve_type_ref_impl(ref)

    def test_unknown_generic_raises(self):
        parser = _make_parser()
        node = _ann_tree("UnknownGeneric[int32]")
        ref = parser._parse_type_ref(node)
        with pytest.raises(ParseError, match="Unknown generic type"):
            parser._resolve_type_ref_impl(ref)

    def test_error_lineno_preserved(self):
        """Errors raised from the resolver use ref.loc to set lineno, matching
        the ast-based path."""
        parser = _make_parser()
        # Build annotation on line 3
        tree = ast.parse("pass\npass\nx: UnknownName\n")
        node = tree.body[2].annotation
        # Direct parse raises with lineno=3
        try:
            parser._parse_type_annotation(node)
            assert False, "should have raised"
        except ParseError as e:
            expected_lineno = e.lineno
        # Resolver path raises with same lineno
        ref = parser._parse_type_ref(node)
        try:
            parser._resolve_type_ref_impl(ref)
            assert False, "should have raised"
        except ParseError as e:
            assert e.lineno == expected_lineno


class TestResolverLenient:
    """Pin `TypeResolver.resolve_lenient` message-pattern fallback.

    The lenient resolver catches three specific error message patterns
    ("Unknown type", "Unknown generic type", "Unsupported qualified type")
    and falls back to a NominalType placeholder. The error sites carry
    LOAD-BEARING-MESSAGE markers; this test enforces the contract.
    """

    def test_lenient_unknown_bare_name(self):
        from .typesys import NominalType
        parser = _make_parser()
        node = _ann_tree("TotallyUnknownName")
        ref = parser._parse_type_ref(node)
        # Strict resolver raises.
        with pytest.raises(ParseError, match="Unknown type"):
            parser._resolve_type_ref_impl(ref)
        # Lenient resolver returns a NominalType placeholder.
        result = parser._resolver.resolve_lenient(ref)
        assert isinstance(result, NominalType)
        assert result.name == "TotallyUnknownName"
        assert result.type_args == ()

    def test_lenient_unknown_generic_with_args(self):
        from .typesys import NominalType, INT32
        parser = _make_parser()
        node = _ann_tree("UnknownGeneric[int32]")
        ref = parser._parse_type_ref(node)
        with pytest.raises(ParseError, match="Unknown generic type"):
            parser._resolve_type_ref_impl(ref)
        result = parser._resolver.resolve_lenient(ref)
        assert isinstance(result, NominalType)
        assert result.name == "UnknownGeneric"
        # Inner int32 resolves normally through strict path inside the
        # lenient recursion, so type_args carries the real singleton.
        assert result.type_args == (INT32,)

    def test_lenient_still_raises_structural_errors(self):
        """Structural errors (not name-resolution) still propagate."""
        parser = _make_parser()
        # Fn with wrong arity is a structural error raised by the walker,
        # not the resolver. The walker runs first, so it raises before
        # the lenient resolver even sees it.
        with pytest.raises(ParseError):
            node = _ann_tree("Fn[[int32]]")  # Fn requires [[params], return]
            parser._parse_type_ref(node)
