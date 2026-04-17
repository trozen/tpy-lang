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
    """
    qname: str
    category: TypeCategory
    is_value_type: bool = False
    subscript_borrows: bool = False
    is_send: Optional[Union[bool, Callable[[tuple], bool]]] = None
    is_sync: Optional[Union[bool, Callable[[tuple], bool]]] = None
    cpp_formatter: Optional[Callable[[tuple], str]] = None
    # element_of(type_args) -> "element type produced by iterating this type".
    # Overrides NominalType.get_element_type's default (first TpyType arg).
    # Needed when the raw first type_arg carries decoration that iteration
    # strips -- e.g. SpanIter[readonly[T]] iterates T, not readonly[T].
    element_of: Optional[Callable[[tuple], "TpyType"]] = None
    # Array/Span need explicit type targets for literal initializers (see
    # NominalType.needs_explicit_element_target). Default is False.
    needs_explicit_element_target: bool = False


_type_defs: dict[str, TypeDef] = {}


def register(td: TypeDef) -> None:
    if td.qname in _type_defs:
        raise ValueError(f"Duplicate TypeDef registration: {td.qname}")
    _type_defs[td.qname] = td


def get_type_def(qname: str) -> Optional[TypeDef]:
    return _type_defs.get(qname)


def type_def_of(t: "TpyType") -> Optional[TypeDef]:
    if t is None:
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
    td = type_def_of(t)
    return td is not None and td.qname == qname


def is_span_iter(t: "TpyType") -> bool: return _is_qn(t, "tpy.SpanIter")
def is_copy_iter(t: "TpyType") -> bool: return _is_qn(t, "tpy.CopyIter")
def is_own_iter(t: "TpyType") -> bool:  return _is_qn(t, "tpy.OwnIter")


# --- Population -----------------------------------------------------------

def _populate() -> None:
    TC = TypeCategory

    # Fixed-width integers (signed + unsigned). Value types.
    for qn in ("tpy.Int8", "tpy.Int16", "tpy.Int32", "tpy.Int64",
               "tpy.UInt8", "tpy.UInt16", "tpy.UInt32", "tpy.UInt64"):
        register(TypeDef(qn, TC.FIXED_INT, is_value_type=True))

    register(TypeDef("builtins.int",   TC.BIG_INT, is_value_type=True))
    register(TypeDef("builtins.float", TC.FLOAT,   is_value_type=True))
    register(TypeDef("tpy.Float32",    TC.FLOAT,   is_value_type=True))
    register(TypeDef("builtins.bool",  TC.BOOL,    is_value_type=True))
    register(TypeDef("tpy.Char",       TC.CHAR,    is_value_type=True))

    # String family. All flavors report is_value_type=True today; String is
    # heap-backed but still "passed around by value" semantically.
    register(TypeDef("builtins.str", TC.STR, is_value_type=True))
    register(TypeDef("tpy.String",   TC.STR, is_value_type=True))
    register(TypeDef("tpy.StrView",  TC.STR, is_value_type=True))
    register(TypeDef("tpy.FStr",     TC.STR, is_value_type=True))

    # Bytes family. All value types per current typesys; BytesView borrows.
    register(TypeDef("builtins.bytes",     TC.BYTES, is_value_type=True))
    register(TypeDef("builtins.bytearray", TC.BYTES, is_value_type=True))
    register(TypeDef("tpy.BytesView",      TC.BYTES, is_value_type=True))

    # Slice types (value types, no subscript). Note: BASIC_SLICE's
    # qualified_name returns "builtins.basic_slice" but the parser factory
    # table keys it as "tpy.basic_slice" -- pre-existing inconsistency
    # (see modules/type_resolution.py). Registry follows the class.
    register(TypeDef("builtins.basic_slice", TC.SLICE, is_value_type=True))
    register(TypeDef("builtins.slice",       TC.SLICE, is_value_type=True))

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
