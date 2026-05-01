"""
Per-qname TypeDef registry.

Each builtin qname has one `TypeDef` describing its intrinsic behaviour
(formatter, value-type status, Send/Sync, subscript semantics, per-
category payloads for records / protocols / enums / factories).  Sema
and codegen dispatch on qname via `type_def_of(t)` plus the `is_*`
predicates in this module, rather than `isinstance` on specific
subclasses.  See docs/ARCHITECTURE.md for the design rationale.

Only per-qname intrinsic data lives here.  Args-dependent behaviour
(e.g. `is_send` for `list[T]` depends on `T`) is expressed via callable
fields (`is_send: bool | Callable[[type_args], bool]`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

if TYPE_CHECKING:
    from tpyc.typesys import TpyType, RecordInfo, ProtocolInfo, TypeParamKind


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
    # Structural wrappers that need factory / arity lookup alongside the
    # nominal TypeDefs. Currently used only by `tpy.Ptr` -- Ptr is a
    # structural wrapper (instances are PtrType, not NominalType), but
    # the parser/sema resolver still needs to find its param_kinds and
    # construct PtrType via a factory, so the entry lives here to keep
    # all factory-table data in a single registry. No `_is_cat` predicate
    # tests this category; Ptr values never hit NominalType dispatch.
    STRUCTURAL_WRAPPER = auto()


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
class EnumInfo:
    """Enum payload attached to TypeDef.enum.

    Carries the members / member_values / underlying_type / is_int_enum
    fields that previously lived on the EnumType / IntEnumType subclasses.
    Both enum flavors share TypeCategory.ENUM; `is_int_enum` gates the
    int-arithmetic surface.
    """
    members: tuple[str, ...]
    member_values: tuple[tuple[str, int], ...]
    underlying_type: "TpyType"
    is_int_enum: bool
    module_name: str | None = None

    @property
    def member_value_map(self) -> dict[str, int]:
        return dict(self.member_values)


@dataclass
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

    TypeDef is non-frozen so that category-specific payloads (`record`,
    `protocol`) can be attached after construction during sema registration.
    Static (module-init) TypeDefs for builtins are populated once by
    `_populate()` and then treated as effectively read-only; dynamic
    attachments go through `attach_dynamic_type_def()` and are reset by
    `clear_dynamic_type_defs()` between compilations.
    """
    qname: str
    category: TypeCategory
    is_value_type: bool = False
    subscript_borrows: bool = False
    is_send: Optional[Union[bool, Callable[[tuple], bool]]] = None
    is_sync: Optional[Union[bool, Callable[[tuple], bool]]] = None
    cpp_formatter: Optional[Callable[[tuple], str]] = None
    param_cpp_formatter: Optional[Callable[[tuple], str]] = None
    # Mutable param form. Used by to_cpp_param when the param is detected
    # mutated. Only needed when param_cpp_formatter encodes the const form
    # (e.g. bytearray's `const std::vector<uint8_t>&`); otherwise the
    # mutable form falls back to param_cpp_formatter.
    param_mut_cpp_formatter: Optional[Callable[[tuple], str]] = None
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
    # Category-specific payloads attached at sema-registration time.
    # `record` carries RecordInfo for RECORD-category TypeDefs; `protocol`
    # carries ProtocolInfo for PROTOCOL-category TypeDefs. Builtin
    # container / primitive categories (LIST, DICT, FIXED_INT, ...) may
    # also gain a `record` payload when their @builtin_type stub registers
    # a RecordInfo (e.g. list, dict -- the stub contributes methods).
    # `enum` carries EnumInfo for ENUM-category TypeDefs (populated when
    # an Enum or IntEnum is registered). The IntEnum/Enum distinction is
    # `enum.is_int_enum`; both share TypeCategory.ENUM.
    record: Optional[Any] = None
    protocol: Optional[Any] = None
    enum: Optional[EnumInfo] = None
    # Generic-instantiation payload.
    #
    # `param_kinds` is the arity + kind (TYPE vs INT) of each type
    # parameter, stored as a tuple so static registry entries are
    # effectively immutable. An empty tuple means "no type args expected"
    # (primitive singleton); a non-empty tuple means "exactly this many
    # args, of these kinds, required". The parser/sema arity-validation
    # path and the resolver's generic instantiation both consult this
    # field.
    #
    # `type_factory` constructs a TpyType instance from resolved type args
    # (e.g. `lambda t: make_list(t)` for `builtins.list`). Primitive
    # entries take zero args and return the singleton; structural wrappers
    # (tpy.Ptr) build a fresh instance. None means "no factory" -- used
    # for TypeDefs that are only conformance entries (CopyIter, OwnIter)
    # or for dynamic RECORD/PROTOCOL/ENUM entries whose construction goes
    # through sema registration, not a factory callable.
    param_kinds: tuple["TypeParamKind", ...] = ()
    type_factory: Optional[Callable[..., "TpyType"]] = None


_type_defs: dict[str, TypeDef] = {}

# Qnames whose TypeDef was created or mutated by a dynamic attachment
# (sema-time `attach_dynamic_type_def`). On `clear_dynamic_type_defs()`
# purely-dynamic TypeDefs are removed and pre-existing static ones get
# their record/protocol payload reset. This keeps the static registry
# (populated once at module load by `_populate()`) independent from per-
# compilation state, without requiring two separate dicts.
_dynamic_created_qnames: set[str] = set()
_dynamic_attached_qnames: set[str] = set()


def register(td: TypeDef) -> None:
    if td.qname in _type_defs:
        raise ValueError(f"Duplicate TypeDef registration: {td.qname}")
    _type_defs[td.qname] = td


def get_type_def(qname: str) -> Optional[TypeDef]:
    return _type_defs.get(qname)


def attach_dynamic_type_def(
    qname: str,
    category: "TypeCategory",
    *,
    record: Optional["RecordInfo"] = None,
    protocol: Optional["ProtocolInfo"] = None,
    enum: Optional[EnumInfo] = None,
    is_value_type: Optional[bool] = None,
) -> TypeDef:
    """Attach a RecordInfo / ProtocolInfo / EnumInfo payload to the TypeDef
    for qname.

    Creates a new TypeDef with the given category if none exists (purely
    dynamic case: user records/protocols/enums whose qname isn't in the
    static registry). If a TypeDef already exists (e.g. `builtins.list`
    whose @builtin_type stub also produces a RecordInfo), the existing
    category is preserved and only the payloads are set.

    `is_value_type` overrides the default (False) when creating a new
    TypeDef -- needed for enums (which are value types) because the base
    NominalType.is_value_type() consults TypeDef.is_value_type.

    Returns the TypeDef for convenience.
    """
    td = _type_defs.get(qname)
    if td is None:
        td = TypeDef(
            qname=qname, category=category,
            record=record, protocol=protocol, enum=enum,
            is_value_type=(is_value_type if is_value_type is not None else False),
        )
        _type_defs[qname] = td
        _dynamic_created_qnames.add(qname)
    else:
        if record is not None:
            td.record = record
        if protocol is not None:
            td.protocol = protocol
        if enum is not None:
            td.enum = enum
        _dynamic_attached_qnames.add(qname)
    return td


def clear_dynamic_type_defs() -> None:
    """Reset dynamic attachments. Called from `clear_all_compilation_state`
    so per-compilation RECORD/PROTOCOL/ENUM TypeDefs don't bleed across runs."""
    for qname in _dynamic_created_qnames:
        _type_defs.pop(qname, None)
    _dynamic_created_qnames.clear()
    for qname in _dynamic_attached_qnames:
        td = _type_defs.get(qname)
        if td is not None:
            td.record = None
            td.protocol = None
            td.enum = None
    _dynamic_attached_qnames.clear()


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


def find_factory_by_simple_name(name: str) -> Optional[TypeDef]:
    """Scan the default search roots (`builtins`, then `tpy`) for a TypeDef
    with the given simple name and a registered `type_factory`. Returns the
    first match or None. Mirrors the old
    `modules.type_resolution.lookup_generic_type` behavior."""
    for module in ("builtins", "tpy"):
        td = _type_defs.get(f"{module}.{name}")
        if td is not None and td.type_factory is not None:
            return td
    return None


def find_factory_in_module(name: str, module: str) -> Optional[TypeDef]:
    """Lookup a TypeDef by simple name within a specific module. Mirrors
    the old `modules.type_resolution.lookup_generic_type_in_module`."""
    td = _type_defs.get(f"{module}.{name}")
    if td is not None and td.type_factory is not None:
        return td
    return None


def factory_qnames_in_module(module: str) -> list[str]:
    """Enumerate qnames with a registered `type_factory` whose module
    prefix matches. Replaces `modules.type_resolution.get_type_factory_names`."""
    prefix = f"{module}."
    return [qn for qn, td in _type_defs.items()
            if qn.startswith(prefix) and td.type_factory is not None]


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
# Qname-based replacements for the pre-migration `isinstance(t, FooType)`
# dispatch.  All container / primitive / enum subclasses have been
# collapsed into `NominalType`, so these predicates go through TypeDef.

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


# Primitive predicates.  Prefer these over ad-hoc `isinstance` on the
# primitive subclasses (which no longer exist) or direct qname
# comparisons; they go through the TypeDef registry uniformly.

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


# Value types that carry an interior pointer into source storage.
# Returning one that borrows from a local would dangle, so these participate
# in dangling-reference and provenance tracking alongside reference types.
def is_borrowing_view_type(t: "TpyType") -> bool:
    return (is_str_view_type(t) or is_bytes_view_type(t)
            or is_span(t) or is_span_iter(t))


# Trait accessors. Return the dataclass or None if the type isn't in the
# corresponding category. Callers should prefer these over reading .bits /
# .signed / .min_value / .max_value from subclasses directly.

def int_traits_of(t: "TpyType") -> Optional[IntTraits]:
    td = type_def_of(t)
    return td.int_traits if td is not None else None


def float_traits_of(t: "TpyType") -> Optional[FloatTraits]:
    td = type_def_of(t)
    return td.float_traits if td is not None else None


def record_info_of(t: "TpyType") -> Optional["RecordInfo"]:
    """Return the RecordInfo payload attached to this type's TypeDef, if any.

    Returns None for types without a TypeDef or without an attached record
    (structural wrappers, primitives, user records with no public qname, ...)."""
    td = type_def_of(t)
    return td.record if td is not None else None


def protocol_info_of(t: "TpyType") -> Optional["ProtocolInfo"]:
    """Return the ProtocolInfo payload attached to this type's TypeDef, if any."""
    td = type_def_of(t)
    return td.protocol if td is not None else None


def enum_info_of(t: "TpyType") -> Optional[EnumInfo]:
    """Return the EnumInfo payload attached to this type's TypeDef, if any.

    Every enum registered by sema has a TypeDef entry (sema/registration.py
    assigns each enum a qname, including `__main__.<name>` for entry-point
    enums). Returns None for non-enum types."""
    td = type_def_of(t)
    return td.enum if td is not None else None


def is_enum_type(t: "TpyType") -> bool:
    """True for any enum -- both plain Enum and IntEnum."""
    return _is_cat(t, TypeCategory.ENUM)


def is_int_enum_type(t: "TpyType") -> bool:
    """True for IntEnum specifically; False for plain Enum and non-enums.

    IntEnum shares TypeCategory.ENUM with plain Enum; the distinction is
    `enum.is_int_enum`."""
    info = enum_info_of(t)
    return info is not None and info.is_int_enum


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
        param_mut_cpp_formatter=lambda args: "std::vector<uint8_t>&",
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
    register(TypeDef(
        "builtins.Range", TC.RANGE, is_value_type=True,
        cpp_formatter=lambda args: f"::tpy::Range<{args[0].to_cpp()}>",
    ))

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

    _populate_factories()


def _populate_factories() -> None:
    """Attach `param_kinds` + `type_factory` to the TypeDefs registered
    above, and register the one structural-wrapper entry that has no
    other TypeDef (`tpy.Ptr`).

    Typesys is imported lazily here because `typesys.py` imports
    `TypeCategory` from this module at its top level.  By the time
    `_populate_factories` runs, typesys is either fully loaded or
    mid-load with its singletons / `make_*` factories already defined
    (the `type_def_registry` import sits on typesys's last page)."""
    from tpyc.typesys import (
        TypeParamKind,
        PtrType, ReadonlyType,
        make_list, make_dict, make_dict_keys_view, make_dict_values_view,
        make_dict_items_view, make_set, make_array, make_span,
        make_span_iter, make_range,
        FLOAT32, FLOAT, BIGINT, BOOL, CHAR, STR, STRING, STRVIEW, FSTR,
        BYTES, BYTEARRAY, BYTESVIEW, BASIC_SLICE, SLICE,
        INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64,
    )
    TYPE = TypeParamKind.TYPE
    INT = TypeParamKind.INT
    TC = TypeCategory

    def _ptr_factory(t):
        # Normalize readonly[T] arg: Ptr[readonly[T]] stores ReadonlyType(T)
        # as the pointee, matching the resolver's current construction path.
        if isinstance(t, ReadonlyType):
            return PtrType(t.wrapped, is_readonly=True)
        return PtrType(t)

    # tpy.Ptr is the only factory-reachable structural wrapper. Register
    # it with category STRUCTURAL_WRAPPER so the registry is the single
    # source of factory truth. is_value_type mirrors PtrType.is_value_type()
    # for the conformance check that iterates over canonical instances.
    #
    # `_ptr_factory` is effectively runtime-dead in normal parse flow:
    # the type_resolver's `tpy:Ptr` structural-wrapper branch (canonical
    # walker-tagged name) fires before generic-factory resolution, so
    # the factory is reached only via `find_factory_by_simple_name`
    # existence checks (parser subscript-as-value, sema "requires: from
    # tpy import Ptr" hint) and the FACTORY_SNAPSHOT conformance tests.
    # The ReadonlyType normalization above matches the resolver's own
    # Ptr handling so both paths stay semantically interchangeable if
    # the structural branch ever goes away.
    register(TypeDef(
        "tpy.Ptr", TC.STRUCTURAL_WRAPPER,
        is_value_type=True,
        param_kinds=(TYPE,),
        type_factory=_ptr_factory,
    ))

    # Nominal-type entries: attach param_kinds + type_factory onto the
    # TypeDefs already registered by `_populate()`. Order mirrors the
    # former `_get_type_factories` table so reviewers can diff the two.
    _factories: list[tuple[str, tuple, Callable[..., "TpyType"]]] = [
        ("builtins.list",        (TYPE,),      lambda t: make_list(t)),
        ("builtins.dict",        (TYPE, TYPE), make_dict),
        ("builtins.dict_keys",   (TYPE, TYPE), make_dict_keys_view),
        ("builtins.dict_values", (TYPE, TYPE), make_dict_values_view),
        ("builtins.dict_items",  (TYPE, TYPE), make_dict_items_view),
        ("builtins.set",         (TYPE,),      make_set),
        ("builtins.Range",       (TYPE,),      make_range),
        ("tpy.Array",            (TYPE, INT),  lambda t, n: make_array(t, n)),
        ("tpy.Span",             (TYPE,),      lambda t: make_span(t)),
        ("tpy.SpanIter",         (TYPE,),      make_span_iter),
        ("tpy.Float32",          (),           lambda: FLOAT32),
        ("tpy.Char",             (),           lambda: CHAR),
        ("tpy.String",           (),           lambda: STRING),
        ("tpy.StrView",          (),           lambda: STRVIEW),
        ("tpy.FStr",             (),           lambda: FSTR),
        ("builtins.int",         (),           lambda: BIGINT),
        ("builtins.float",       (),           lambda: FLOAT),
        ("builtins.bool",        (),           lambda: BOOL),
        ("builtins.str",         (),           lambda: STR),
        ("builtins.bytes",       (),           lambda: BYTES),
        ("builtins.bytearray",   (),           lambda: BYTEARRAY),
        ("tpy.BytesView",        (),           lambda: BYTESVIEW),
        ("tpy.basic_slice",      (),           lambda: BASIC_SLICE),
        ("builtins.slice",       (),           lambda: SLICE),
    ]
    for qn, kinds, fac in _factories:
        td = _type_defs[qn]
        td.param_kinds = kinds
        td.type_factory = fac

    for singleton in (INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64):
        qn = singleton.qualified_name()
        td = _type_defs[qn]
        td.param_kinds = ()
        # Bind `singleton` by default-argument so each lambda closes over
        # its own value instead of the loop variable's final value.
        td.type_factory = (lambda s=singleton: s)


_populate()
