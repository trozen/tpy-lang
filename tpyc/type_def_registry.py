"""
Per-qname TypeDef registry.

Part of the Phase A scaffolding for the typesys migration (see
docs/TYPESYS_MIGRATION.md). Each builtin qname has one TypeDef capturing
behavior that today lives on a dedicated subclass (DictType,
SpanType, ...). Sema and codegen can read TypeDef instead of isinstance
/ downcasting, which unblocks subclass removal in Phase B.

TypeDef fields are intentionally minimal at this stage -- only properties
that are intrinsic to the qname (not dependent on type_args) are stored
here. Args-dependent behavior (e.g. is_send for list[T] depends on T)
stays on the current subclass methods until Phase B migrates it to
per-category logic on top of TypeDef.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable, Optional, Union

if TYPE_CHECKING:
    from tpyc.typesys import TpyType


class TypeCategory(Enum):
    """Coarse category for fast dispatch. Mirrors the natural grouping
    of current subclasses; refine granularity as sema/codegen call sites
    reveal what they actually need to distinguish."""
    FIXED_INT = auto()
    BIG_INT = auto()
    FLOAT = auto()
    BOOL = auto()
    CHAR = auto()
    STR = auto()          # str, String, StrView, FStr
    BYTES = auto()        # bytes, bytearray, BytesView
    SLICE = auto()        # slice, basic_slice
    VOID = auto()
    LIST = auto()
    DICT = auto()
    DICT_VIEW = auto()    # dict_keys, dict_values, dict_items
    SET = auto()
    ARRAY = auto()
    SPAN = auto()
    ITERATOR = auto()     # SpanIter, CopyIter, OwnIter
    RANGE = auto()
    RECORD = auto()
    PROTOCOL = auto()
    ENUM = auto()


@dataclass(frozen=True)
class IntTraits:
    """Fixed-width integer traits: width + signedness.

    `min_value` / `max_value` are computed from bits+signed; no need to
    store them."""
    bits: int
    signed: bool

    @property
    def min_value(self) -> int:
        return -(2 ** (self.bits - 1)) if self.signed else 0

    @property
    def max_value(self) -> int:
        if self.signed:
            return 2 ** (self.bits - 1) - 1
        return 2 ** self.bits - 1


@dataclass(frozen=True)
class FloatTraits:
    """Floating-point traits: width (32 or 64)."""
    bits: int


@dataclass(frozen=True)
class TypeDef:
    """Per-qname behavior record. All fields describe properties intrinsic
    to the qname (not to a particular instance's type_args).

    `is_send` / `is_sync` are Optional: None means "fall through to default
    NominalType behavior" (which matches is_value_type for most types).
    An explicit bool overrides with a fixed answer (dict views force False).
    A callable `(type_args) -> bool` computes per-instance (list, dict, set,
    Array -- Send/Sync depend on element types).

    `cpp_formatter` overrides NominalType.to_cpp's default `{name}<{args}>`
    rendering when the C++ type name differs from the Python qname (e.g.
    dict_keys -> ::tpy::dict_keys_view).

    `param_cpp_formatter` overrides the default to_cpp_param_type() computation
    (value types = to_cpp(); non-value = to_cpp() + "&"). Set for primitives
    whose parameter rendering differs from their value rendering -- e.g.
    builtins.str renders as std::string but is passed as std::string_view.
    """
    qname: str
    category: TypeCategory
    is_value_type: bool = False
    subscript_borrows: bool = False
    is_send: Optional[Union[bool, Callable[[tuple], bool]]] = None
    is_sync: Optional[Union[bool, Callable[[tuple], bool]]] = None
    cpp_formatter: Optional[Callable[[tuple], str]] = None
    param_cpp_formatter: Optional[Callable[[tuple], str]] = None
    # element_of(type_args) -> "element type produced by iterating this type".
    # Overrides NominalType.get_element_type's default (first TpyType arg).
    # Needed when the raw first type_arg carries decoration that iteration
    # strips -- e.g. SpanIter[readonly[T]] iterates T, not readonly[T].
    element_of: Optional[Callable[[tuple], "TpyType"]] = None
    # Array/Span need explicit type targets for literal initializers (see
    # NominalType.needs_explicit_element_target). Default is False.
    needs_explicit_element_target: bool = False
    # Primitive-specific: copy cost and reassign semantics. Matters for
    # parameter passing (const T& for expensive copies) and for the codegen
    # pattern that emits a local mutable copy when a const-ref parameter
    # is reassigned in the body.
    is_expensive_copy: bool = False
    param_needs_copy_for_reassign: bool = False
    # Compile-time-only types (FStr) have no runtime C++ representation.
    is_compile_time_only: bool = False
    # Category-specific trait payloads.
    int_traits: Optional[IntTraits] = None
    float_traits: Optional[FloatTraits] = None


_type_defs: dict[str, TypeDef] = {}


def register(td: TypeDef) -> None:
    if td.qname in _type_defs:
        raise ValueError(f"Duplicate TypeDef registration: {td.qname}")
    _type_defs[td.qname] = td


def get_type_def(qname: str) -> Optional[TypeDef]:
    return _type_defs.get(qname)


def type_def_of(t: "TpyType") -> Optional[TypeDef]:
    # type_args for parameterized types can contain plain ints (Array[T, N])
    # so predicates called in generic-matching code see non-TpyType values.
    # Mirror isinstance(t, XxxType)'s "quietly False" behavior.
    #
    # PERF TODO: called from every is_*_type predicate; each call does
    # hasattr + qualified_name() dispatch + dict.get. Since NominalType
    # singletons never change TypeDef, the lookup result could be cached on
    # the instance at construction (one lookup per singleton, O(1) attribute
    # thereafter), turning tag-based predicates back into a ~single-instruction
    # frozenset-style check. Consider once post-mypyc profiling identifies
    # predicate dispatch as a bottleneck.
    if t is None or not hasattr(t, "qualified_name"):
        return None
    qn = t.qualified_name()
    return _type_defs.get(qn) if qn else None


def resolve_send_sync(field, type_args: tuple) -> Optional[bool]:
    """Resolve a TypeDef.is_send / is_sync field to a concrete bool.

    Returns None if the field is None (caller should fall through to default
    NominalType behavior). Calls the callable when the field is a Callable.
    """
    if field is None:
        return None
    if isinstance(field, bool):
        return field
    return field(type_args)


# --- Predicates -----------------------------------------------------------
#
# These return the same answers as the current `isinstance(t, FooType)` checks
# but go through the TypeDef registry, so they continue to work after the
# subclasses are deleted in Phase B.

def _is_cat(t: "TpyType", cat: TypeCategory) -> bool:
    # LiteralType / PendingViewType delegate qualified_name() to a base/family,
    # so their TypeDef lookup hits the inner's category. Mirror isinstance
    # semantics which never matched those wrappers.
    from tpyc.typesys import LiteralType, PendingViewType
    if isinstance(t, (LiteralType, PendingViewType)):
        return False
    td = type_def_of(t)
    return td is not None and td.category is cat


def _is_concrete_cat(t: "TpyType", cat: TypeCategory) -> bool:
    """Narrower form of _is_cat: require a NominalType instance.

    For qnames shared with a Pending* subclass (list/dict/set -- the pending
    forms have `qualified_name()` returning the builtin qname), the caller
    almost always means the concrete NominalType, not the PendingListType /
    PendingDictType / PendingSetType sibling which has its own fields and
    hasn't been resolved yet."""
    from tpyc.typesys import NominalType
    return isinstance(t, NominalType) and _is_cat(t, cat)


def is_list(t: "TpyType") -> bool:           return _is_concrete_cat(t, TypeCategory.LIST)
def is_dict(t: "TpyType") -> bool:           return _is_concrete_cat(t, TypeCategory.DICT)
def is_set(t: "TpyType") -> bool:            return _is_concrete_cat(t, TypeCategory.SET)
def is_array(t: "TpyType") -> bool:          return _is_cat(t, TypeCategory.ARRAY)
def is_span(t: "TpyType") -> bool:           return _is_cat(t, TypeCategory.SPAN)
def is_dict_view(t: "TpyType") -> bool:      return _is_cat(t, TypeCategory.DICT_VIEW)
def is_range(t: "TpyType") -> bool:          return _is_cat(t, TypeCategory.RANGE)
def is_iterator_adapter(t: "TpyType") -> bool: return _is_cat(t, TypeCategory.ITERATOR)


def _is_qn(t: "TpyType", qname: str) -> bool:
    # LiteralType and PendingViewType delegate qualified_name() to an inner
    # base/family, so their TypeDef lookup would hit a primitive's qname.
    # Mirror `isinstance(t, PrimitiveSubclass)` which never matched them.
    from tpyc.typesys import LiteralType, PendingViewType
    if isinstance(t, (LiteralType, PendingViewType)):
        return False
    td = type_def_of(t)
    return td is not None and td.qname == qname


def is_span_iter(t: "TpyType") -> bool: return _is_qn(t, "tpy.SpanIter")
def is_copy_iter(t: "TpyType") -> bool: return _is_qn(t, "tpy.CopyIter")
def is_own_iter(t: "TpyType") -> bool:  return _is_qn(t, "tpy.OwnIter")


# Primitive predicates (Phase D). Prefer these over isinstance(t, FooType)
# so callers stop depending on the primitive subclass identity. Once the
# subclasses are deleted in Phase D step 4, primitives become NominalType
# instances with a TypeDef entry and these predicates still answer correctly.

def is_fixed_int_type(t: "TpyType") -> bool:  return _is_cat(t, TypeCategory.FIXED_INT)
def is_big_int_type(t: "TpyType") -> bool:    return _is_cat(t, TypeCategory.BIG_INT)
def is_bool_type(t: "TpyType") -> bool:       return _is_cat(t, TypeCategory.BOOL)
def is_char_type(t: "TpyType") -> bool:       return _is_cat(t, TypeCategory.CHAR)
def is_float_category(t: "TpyType") -> bool:  return _is_cat(t, TypeCategory.FLOAT)
def is_str_category(t: "TpyType") -> bool:    return _is_cat(t, TypeCategory.STR)
def is_bytes_category(t: "TpyType") -> bool:  return _is_cat(t, TypeCategory.BYTES)
def is_slice_category(t: "TpyType") -> bool:  return _is_cat(t, TypeCategory.SLICE)


# Single-qname primitive predicates.
def is_str_type(t: "TpyType") -> bool:     return _is_qn(t, "builtins.str")
def is_string_type(t: "TpyType") -> bool:  return _is_qn(t, "tpy.String")
def is_str_view_type(t: "TpyType") -> bool: return _is_qn(t, "tpy.StrView")
def is_fstr_type(t: "TpyType") -> bool:    return _is_qn(t, "tpy.FStr")
def is_float64_type(t: "TpyType") -> bool: return _is_qn(t, "builtins.float")
def is_float32_type(t: "TpyType") -> bool: return _is_qn(t, "tpy.Float32")
def is_bytes_type(t: "TpyType") -> bool:   return _is_qn(t, "builtins.bytes")
def is_bytearray_type(t: "TpyType") -> bool: return _is_qn(t, "builtins.bytearray")
def is_bytes_view_type(t: "TpyType") -> bool: return _is_qn(t, "tpy.BytesView")
def is_basic_slice_type(t: "TpyType") -> bool: return _is_qn(t, "tpy.basic_slice")
def is_slice_type(t: "TpyType") -> bool:   return _is_qn(t, "builtins.slice")


# Trait accessors. Return the dataclass or None if the type isn't in the
# corresponding category. Callers should prefer these over reading .bits /
# .signed / .min_value / .max_value from subclasses directly.

def int_traits_of(t: "TpyType") -> Optional[IntTraits]:
    td = type_def_of(t)
    return td.int_traits if td is not None else None


def float_traits_of(t: "TpyType") -> Optional[FloatTraits]:
    td = type_def_of(t)
    return td.float_traits if td is not None else None


# --- Population -----------------------------------------------------------

def _populate() -> None:
    TC = TypeCategory

    # Fixed-width integers (signed + unsigned). Value types. cpp_formatter
    # renders as int{bits}_t / uint{bits}_t derived from int_traits.
    def _int_cpp(bits: int, signed: bool):
        name = f"int{bits}_t" if signed else f"uint{bits}_t"
        return lambda args: name

    for bits in (8, 16, 32, 64):
        for signed in (True, False):
            prefix = "Int" if signed else "UInt"
            qn = f"tpy.{prefix}{bits}"
            register(TypeDef(
                qn, TC.FIXED_INT, is_value_type=True,
                cpp_formatter=_int_cpp(bits, signed),
                param_cpp_formatter=_int_cpp(bits, signed),
                int_traits=IntTraits(bits=bits, signed=signed),
            ))

    # BigInt: heap-backed, expensive to copy, passed by const reference.
    register(TypeDef(
        "builtins.int", TC.BIG_INT, is_value_type=True,
        cpp_formatter=lambda args: "::tpy::BigInt",
        param_cpp_formatter=lambda args: "const ::tpy::BigInt&",
        is_expensive_copy=True, param_needs_copy_for_reassign=True,
    ))

    # Floats.
    register(TypeDef(
        "builtins.float", TC.FLOAT, is_value_type=True,
        cpp_formatter=lambda args: "double",
        param_cpp_formatter=lambda args: "double",
        float_traits=FloatTraits(bits=64),
    ))
    register(TypeDef(
        "tpy.Float32", TC.FLOAT, is_value_type=True,
        cpp_formatter=lambda args: "float",
        param_cpp_formatter=lambda args: "float",
        float_traits=FloatTraits(bits=32),
    ))

    # Bool and Char.
    register(TypeDef(
        "builtins.bool", TC.BOOL, is_value_type=True,
        cpp_formatter=lambda args: "bool",
        param_cpp_formatter=lambda args: "bool",
    ))
    register(TypeDef(
        "tpy.Char", TC.CHAR, is_value_type=True,
        cpp_formatter=lambda args: "char",
        param_cpp_formatter=lambda args: "char",
    ))

    # String family. All flavors report is_value_type=True; String and str
    # are heap-backed and expensive to copy, StrView is a lightweight view,
    # FStr is compile-time-only.
    # element_of returns CHAR so iteration / for-each over any str-family
    # type yields Char.
    def _char_elem(args):
        from tpyc.typesys import CHAR
        return CHAR

    register(TypeDef(
        "builtins.str", TC.STR, is_value_type=True,
        cpp_formatter=lambda args: "std::string",
        param_cpp_formatter=lambda args: "std::string_view",
        is_expensive_copy=True, param_needs_copy_for_reassign=True,
        element_of=_char_elem,
    ))
    register(TypeDef(
        "tpy.String", TC.STR, is_value_type=True,
        cpp_formatter=lambda args: "std::string",
        param_cpp_formatter=lambda args: "const std::string&",
        is_expensive_copy=True, param_needs_copy_for_reassign=True,
        element_of=_char_elem,
    ))
    register(TypeDef(
        "tpy.StrView", TC.STR, is_value_type=True,
        is_send=False, is_sync=True,
        cpp_formatter=lambda args: "std::string_view",
        param_cpp_formatter=lambda args: "std::string_view",
        element_of=_char_elem,
    ))
    # FStr has no runtime representation; cpp_formatter/param_cpp_formatter
    # stay None so to_cpp() raises TypeError via the is_compile_time_only
    # check in TpyType.to_cpp / NominalType.to_cpp.
    register(TypeDef(
        "tpy.FStr", TC.STR, is_value_type=True,
        is_compile_time_only=True,
    ))

    # Bytes family. bytes/bytearray are heap-backed (std::vector<uint8_t>),
    # BytesView borrows (std::span<const uint8_t>). Element type is UInt8.
    def _u8_elem(args):
        from tpyc.typesys import UINT8
        return UINT8

    register(TypeDef(
        "builtins.bytes", TC.BYTES, is_value_type=True,
        cpp_formatter=lambda args: "std::vector<uint8_t>",
        param_cpp_formatter=lambda args: "std::span<const uint8_t>",
        is_expensive_copy=True, param_needs_copy_for_reassign=True,
        element_of=_u8_elem,
    ))
    register(TypeDef(
        "builtins.bytearray", TC.BYTES, is_value_type=True,
        cpp_formatter=lambda args: "std::vector<uint8_t>",
        param_cpp_formatter=lambda args: "const std::vector<uint8_t>&",
        is_expensive_copy=True, param_needs_copy_for_reassign=True,
        element_of=_u8_elem,
    ))
    register(TypeDef(
        "tpy.BytesView", TC.BYTES, is_value_type=True,
        is_send=False, is_sync=True,
        cpp_formatter=lambda args: "std::span<const uint8_t>",
        param_cpp_formatter=lambda args: "std::span<const uint8_t>",
        element_of=_u8_elem,
    ))

    # Slice types (value types, no subscript). basic_slice is tpy-specific
    # (no CPython analog); slice is the CPython built-in.
    register(TypeDef(
        "tpy.basic_slice", TC.SLICE, is_value_type=True,
        cpp_formatter=lambda args: "::tpy::BasicSlice",
        param_cpp_formatter=lambda args: "::tpy::BasicSlice",
    ))
    register(TypeDef(
        "builtins.slice", TC.SLICE, is_value_type=True,
        cpp_formatter=lambda args: "::tpy::Slice",
        param_cpp_formatter=lambda args: "::tpy::Slice",
    ))

    # Containers.
    # subscript_borrows reflects the current subclass .subscript_borrows().
    # Span itself does NOT set subscript_borrows=True in the current code
    # (its subscript returns the element by reference, not a borrow view);
    # see typesys.py SpanType -- inherits the TpyType default.
    # list[T]: is_send from element, is_sync always False (mutable); cpp
    # emits std::vector<T>; subscript_borrows (element view of container).
    register(TypeDef(
        "builtins.list", TC.LIST,
        subscript_borrows=True,
        is_send=lambda args: args[0].is_send(),
        is_sync=False,
        cpp_formatter=lambda args: f"std::vector<{args[0].to_cpp()}>",
    ))

    # dict[K, V]: send requires BOTH key and value Send; sync always False
    # (mutable container). Subscript d[k] returns V (type_args[1]); default
    # NominalType.get_element_type would return K (first type_arg), so use
    # element_of to override. cpp: ::tpy::ordered_map<K, V>.
    register(TypeDef(
        "builtins.dict", TC.DICT,
        subscript_borrows=True,
        is_send=lambda args: args[0].is_send() and args[1].is_send(),
        is_sync=False,
        cpp_formatter=lambda args: f"::tpy::ordered_map<{args[0].to_cpp()}, {args[1].to_cpp()}>",
        element_of=lambda args: args[1],
    ))

    # set[T]: is_send depends on element (set is safe to transfer across
    # threads only when its element is); is_sync always False (mutable
    # container). C++ name diverges: set[T] -> ::tpy::ordered_set<T>.
    register(TypeDef(
        "builtins.set", TC.SET,
        is_send=lambda args: args[0].is_send(),
        is_sync=False,
        cpp_formatter=lambda args: f"::tpy::ordered_set<{args[0].to_cpp()}>",
    ))

    # Dict views: value_type=True but is_send/is_sync forced False (they
    # borrow from the parent dict). cpp_formatter overrides default naming
    # because the C++ type is ::tpy::dict_*_view<K, V>, not dict_*<K, V>.
    def _dict_view_cpp(tag: str):
        def fmt(args: tuple) -> str:
            k, v = args[0], args[1]
            return f"::tpy::dict_{tag}_view<{k.to_cpp()}, {v.to_cpp()}>"
        return fmt

    register(TypeDef("builtins.dict_keys",   TC.DICT_VIEW, is_value_type=True,
                     is_send=False, is_sync=False,
                     cpp_formatter=_dict_view_cpp("keys")))
    register(TypeDef("builtins.dict_values", TC.DICT_VIEW, is_value_type=True,
                     is_send=False, is_sync=False,
                     cpp_formatter=_dict_view_cpp("values")))
    register(TypeDef("builtins.dict_items",  TC.DICT_VIEW, is_value_type=True,
                     is_send=False, is_sync=False,
                     cpp_formatter=_dict_view_cpp("items")))
    register(TypeDef("builtins.Range",       TC.RANGE,     is_value_type=True))

    # Array[T, N]: fixed-size, value-like (not heap-backed). type_args is
    # (element, size) where size is int or integer-kind TypeParamRef.
    # cpp: std::array<T, N>; is_send/is_sync propagate from element.
    def _array_cpp(args: tuple) -> str:
        elem, size = args
        size_str = str(size) if isinstance(size, int) else size.to_cpp()
        return f"std::array<{elem.to_cpp()}, {size_str}>"

    register(TypeDef(
        "tpy.Array", TC.ARRAY,
        subscript_borrows=True,
        is_send=lambda args: args[0].is_send(),
        is_sync=lambda args: args[0].is_sync(),
        cpp_formatter=_array_cpp,
        needs_explicit_element_target=True,
    ))
    # Span[T] / Span[readonly[T]]: value-type view; never Send (borrows);
    # Sync only for readonly variant whose element is Sync. cpp_formatter
    # handles the readonly[T] -> const T mapping.
    def _span_cpp(args: tuple) -> str:
        from tpyc.typesys import ReadonlyType, unwrap_readonly
        elem = args[0]
        if isinstance(elem, ReadonlyType):
            return f"std::span<const {unwrap_readonly(elem).to_cpp()}>"
        return f"std::span<{elem.to_cpp()}>"

    def _span_is_sync(args: tuple) -> bool:
        from tpyc.typesys import ReadonlyType, unwrap_readonly
        elem = args[0]
        if isinstance(elem, ReadonlyType):
            return unwrap_readonly(elem).is_sync()
        return False

    def _span_elem(args: tuple):
        from tpyc.typesys import unwrap_readonly
        return unwrap_readonly(args[0])

    register(TypeDef(
        "tpy.Span", TC.SPAN,
        is_value_type=True,
        is_send=False,
        is_sync=_span_is_sync,
        cpp_formatter=_span_cpp,
        element_of=_span_elem,
        needs_explicit_element_target=True,
    ))

    # Iterator adapters. SpanIter forces is_send/is_sync=False (borrows from
    # the underlying span); CopyIter/OwnIter own their data and inherit the
    # value-type defaults (is_send/is_sync follow is_value_type=True).
    # SpanIter cpp_formatter handles `readonly[T]` -> `const T` like SpanType;
    # CopyIter/OwnIter expand to `auto` (the concrete type is auto-deduced
    # by the C++ compiler, sema only tracks the element type).
    def _span_iter_cpp(args: tuple) -> str:
        from tpyc.typesys import ReadonlyType, unwrap_readonly
        elem = args[0]
        if isinstance(elem, ReadonlyType):
            return f"::tpy::SpanIter<const {unwrap_readonly(elem).to_cpp()}>"
        return f"::tpy::SpanIter<{elem.to_cpp()}>"

    def _span_iter_elem(args: tuple):
        from tpyc.typesys import unwrap_readonly
        return unwrap_readonly(args[0])

    register(TypeDef("tpy.SpanIter", TC.ITERATOR, is_value_type=True,
                     is_send=False, is_sync=False,
                     cpp_formatter=_span_iter_cpp,
                     element_of=_span_iter_elem))
    register(TypeDef("tpy.CopyIter", TC.ITERATOR, is_value_type=True,
                     cpp_formatter=lambda args: "auto"))
    register(TypeDef("tpy.OwnIter",  TC.ITERATOR, is_value_type=True,
                     cpp_formatter=lambda args: "auto"))


_populate()
