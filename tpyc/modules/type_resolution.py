"""Type resolution: factories, generic type lookup, method resolution, iterator/span helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from tpyc.typesys import TpyType, TypeRegistry, RecordInfo, SpanType, FunctionInfo

from tpyc.typesys import TypeParamRef, NamedType, PtrType, TupleType, TypeParamKind
from tpyc.modules.defs import ParamDef, MethodDef, BuiltinTypeDef, GenericTypeLookup


# ---------------------------------------------------------------------------
# Concrete type name resolution
# ---------------------------------------------------------------------------

def _resolve_concrete_type_name(name: str) -> "TpyType | None":
    """Resolve a concrete type name (like 'Char', 'Int32') to its TpyType singleton.

    Used for resolving extends declarations like extends=["NativeIterable[Char]"].
    """
    from tpyc.typesys import (
        CHAR, BOOL, STR, VOID, BIGINT, FLOAT, FLOAT32, BASIC_SLICE, SLICE,
        INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64,
    )

    # Map of simple type names to their singleton instances
    type_map = {
        "Char": CHAR,
        "Int8": INT8, "Int16": INT16, "Int32": INT32, "Int64": INT64,
        "UInt8": UINT8, "UInt16": UINT16, "UInt32": UINT32, "UInt64": UINT64,
        "Float32": FLOAT32,
        "str": STR,
        "int": BIGINT,
        "float": FLOAT,
        "basic_slice": BASIC_SLICE,
        "slice": SLICE,
        "None": VOID,
    }
    return type_map.get(name)


def _resolve_extends_type_arg(type_str: str, type_params: dict[str, "TpyType"]) -> "TpyType | None":
    """Resolve an extends type arg string to a TpyType.

    Handles:
    - Simple type param refs: "K", "V", "T"
    - Concrete type names: "Char", "Int32"
    - Tuple types: "tuple[K, V]"
    """
    # Handle tuple[...] pattern
    tuple_match = re.match(r"tuple\[(.+)\]$", type_str)
    if tuple_match:
        inner = tuple_match.group(1)
        parts = [p.strip() for p in inner.split(",")]
        resolved = []
        for part in parts:
            if part in type_params:
                resolved.append(type_params[part])
            else:
                concrete = _resolve_concrete_type_name(part)
                if concrete is None:
                    return None
                resolved.append(concrete)
        return TupleType(tuple(resolved))

    # Simple name: type param or concrete type
    if type_str in type_params:
        return type_params[type_str]
    return _resolve_concrete_type_name(type_str)


# ---------------------------------------------------------------------------
# Type factory system
# ---------------------------------------------------------------------------

# Type factories: the bridge between .py type definitions and compiler-internal
# type classes. For types fully defined in .py, this is the only hardcoded piece.
# Keyed by qualified name. param_kinds is needed by the parser to validate
# type arguments (TYPE vs INT).
_type_factories: dict[str, tuple[list[TypeParamKind], "Callable[..., TpyType]"]] | None = None


def _get_type_factories() -> dict[str, tuple[list[TypeParamKind], "Callable[..., TpyType]"]]:
    """Lazily initialize the type factory mapping (avoids circular imports)."""
    global _type_factories
    if _type_factories is None:
        from tpyc.typesys import (
            ListType, DictType, DictKeysViewType, DictValuesViewType,
            DictItemsViewType, SetType, ArrayType, SpanType, SpanIterType,
            PtrType, RangeType, FLOAT32, FLOAT, BIGINT, BOOL, CHAR, STR, STRING, STRVIEW, FSTR, BYTES, BYTEARRAY, BYTESVIEW, BASIC_SLICE, SLICE,
            ALL_FIXED_INTS,
        )
        TYPE = TypeParamKind.TYPE
        INT = TypeParamKind.INT
        _type_factories = {
            "builtins.list": ([TYPE], lambda t: ListType(t)),
            "builtins.dict": ([TYPE, TYPE], lambda k, v: DictType(k, v)),
            "builtins.dict_keys": ([TYPE, TYPE], lambda k, v: DictKeysViewType(k, v)),
            "builtins.dict_values": ([TYPE, TYPE], lambda k, v: DictValuesViewType(k, v)),
            "builtins.dict_items": ([TYPE, TYPE], lambda k, v: DictItemsViewType(k, v)),
            "builtins.set": ([TYPE], lambda t: SetType(t)),
            "builtins.Range": ([TYPE], lambda t: RangeType(t)),
            "tpy.Array": ([TYPE, INT], lambda t, n: ArrayType(t, n)),
            "tpy.Span": ([TYPE], lambda t: SpanType(t)),
            "tpy.SpanIter": ([TYPE], lambda t: SpanIterType(t)),
            "tpy.Ptr": ([TYPE], lambda t: PtrType(t)),
            "tpy.Float32": ([], lambda: FLOAT32),
            "tpy.Char": ([], lambda: CHAR),
            "tpy.String": ([], lambda: STRING),
            "tpy.StrView": ([], lambda: STRVIEW),
            "tpy.FStr": ([], lambda: FSTR),
            "builtins.int": ([], lambda: BIGINT),
            "builtins.float": ([], lambda: FLOAT),
            "builtins.bool": ([], lambda: BOOL),
            "builtins.str": ([], lambda: STR),
            "builtins.bytes": ([], lambda: BYTES),
            "builtins.bytearray": ([], lambda: BYTEARRAY),
            "tpy.BytesView": ([], lambda: BYTESVIEW),
            "tpy.basic_slice": ([], lambda: BASIC_SLICE),
            "builtins.slice": ([], lambda: SLICE),
            **{f"tpy.{t}": ([], (lambda typ: lambda: typ)(t)) for t in ALL_FIXED_INTS},
        }
    return _type_factories


def get_type_factory(qname: str) -> "Callable[..., TpyType] | None":
    """Get the type factory for a qualified type name."""
    entry = _get_type_factories().get(qname)
    return entry[1] if entry else None


def get_type_factory_names(module_prefix: str) -> list[str]:
    """Get qualified names of all type factories for a module prefix."""
    prefix = f"{module_prefix}."
    return [k for k in _get_type_factories() if k.startswith(prefix)]


def _make_factory_type_def(param_kinds: list[TypeParamKind], factory: "Callable[..., TpyType]") -> BuiltinTypeDef:
    """Create a minimal BuiltinTypeDef from the type factory mapping.

    Used for types fully defined in .py that have no hardcoded BuiltinTypeDef.
    """
    # Placeholder names -- only param_kinds matters to the parser caller
    type_params = [chr(ord('A') + i) if len(param_kinds) > 1 else "T"
                   for i in range(len(param_kinds))]
    return BuiltinTypeDef(
        type_obj=None,
        cpp_type="",
        type_params=type_params,
        param_kinds=param_kinds,
        type_factory=factory,
    )


def lookup_generic_type(name: str) -> GenericTypeLookup | None:
    """Lookup a parameterized type by its simple name (e.g., 'list', 'Array').

    Only returns types that have type parameters and a type factory defined.
    Returns first match across default modules (builtins + tpy) only.
    Types from other modules (tpy.mem, tpy.unsafe, etc.) require explicit import
    and are resolved via lookup_generic_type_in_module() instead.
    """
    for module_name in ("builtins", "tpy"):
        qualified = f"{module_name}.{name}"
        if entry := _get_type_factories().get(qualified):
            return GenericTypeLookup(_make_factory_type_def(*entry), qualified)
    return None


def lookup_generic_type_in_module(name: str, module_name: str) -> GenericTypeLookup | None:
    """Lookup a parameterized type by simple name within a specific module.

    Used when resolving imported names (e.g., 'from tpy.mem import UninitArrayStorage').
    """
    qualified = f"{module_name}.{name}"
    if entry := _get_type_factories().get(qualified):
        return GenericTypeLookup(_make_factory_type_def(*entry), qualified)
    return None


# ---------------------------------------------------------------------------
# Type param extraction and method resolution
# ---------------------------------------------------------------------------

def extract_type_params(tpy_type: "TpyType") -> dict[str, "TpyType"]:
    """Extract type parameters from a concrete type instance.

    For list[Int32], returns {"T": Int32}.
    For dict[str, Int32], returns {"K": str, "V": Int32}.
    For Container[Point, 10], returns {"T": Point}.
    For Ptr[Point], returns {"T": Point}.

    Note: Only type parameters that are themselves types are extracted.
    Integer parameters like N in Container[T, N] are not included.
    """
    from tpyc.typesys import PtrType, DictType, DictKeysViewType, DictValuesViewType, DictItemsViewType
    if isinstance(tpy_type, (DictType, DictKeysViewType, DictValuesViewType, DictItemsViewType)):
        return {"K": tpy_type.key_type, "V": tpy_type.value_type}
    # Pointer types: use the full pointee (preserving readonly if present).
    # For Ptr[readonly[T]], T maps to readonly[T] so that methods like
    # span() -> Span[T] correctly produce Span[readonly[T]].
    # The deref chains in expressions.py and methods.py handle unwrapping
    # ReadonlyType from __deref__() results for field/method access.
    if isinstance(tpy_type, PtrType):
        return {"T": tpy_type.pointee}
    if (elem_type := tpy_type.get_element_type()) is not None:
        return {"T": elem_type}
    return {}


def _resolve_type_or_param(t: "TpyType", type_params: dict[str, "TpyType"]) -> "TpyType":
    """Resolve TypeParamRef instances to concrete types.

    Handles:
    - TypeParamRef("T") -> type_params["T"]
    - SelfType -> type_params["Self"] if available, else SELF
    - NamedType (protocol) with TypeParamRef args -> resolved NamedType
    - PtrType with TypeParamRef pointee -> resolved pointer type
    - Other TpyType -> returned as-is (uses map_inner_types for nested resolution)
    """
    from tpyc.typesys import SelfType, SELF

    # Handle SelfType
    if isinstance(t, SelfType):
        if "Self" in type_params:
            return type_params["Self"]
        return SELF

    # Handle TypeParamRef directly
    if isinstance(t, TypeParamRef):
        if t.name not in type_params:
            raise ValueError(f"Unresolved type parameter: {t.name}")
        return type_params[t.name]

    # Handle NamedType (protocol) with TypeParamRef in type_args
    if isinstance(t, NamedType) and t.is_protocol and t.type_args:
        resolved_args = tuple(
            _resolve_type_or_param(arg, type_params) for arg in t.type_args
        )
        return NamedType(t.name, resolved_args, is_protocol=True)

    # Handle PtrType with TypeParamRef pointee
    if isinstance(t, PtrType):
        resolved_pointee = _resolve_type_or_param(t.pointee, type_params)
        return PtrType(resolved_pointee, is_readonly=t.is_readonly)

    # For other types, use map_inner_types for recursive substitution
    return t.map_inner_types(lambda inner: _resolve_type_or_param(inner, type_params))


def resolve_method(method: MethodDef, type_params: dict[str, "TpyType"]) -> MethodDef:
    """Resolve TypeParamRef instances in a method signature to concrete types.

    Given a method with TypeParamRef placeholders and a dict mapping
    param names to concrete types, returns a new MethodDef with all types resolved.

    Example:
        method = MethodDef(params=[ParamDef("value", TypeParamRef("T"))],
                           returns=TypeParamRef("T"), cpp=...)
        resolved = resolve_method(method, {"T": Int32})
        # resolved.params[0].type == Int32, resolved.returns == Int32
    """
    resolved_params = [
        ParamDef(name=p.name, type=_resolve_type_or_param(p.type, type_params),
                 requires_mutable_lvalue=p.requires_mutable_lvalue)
        for p in method.params
    ]
    resolved_returns = _resolve_type_or_param(method.returns, type_params)
    return MethodDef(
        params=resolved_params,
        returns=resolved_returns,
        cpp=method.cpp,
        is_noalloc=method.is_noalloc,
        is_readonly=method.is_readonly,
        is_pure=method.is_pure,
        type_params=method.type_params,
        type_param_bounds=method.type_param_bounds,
    )


# ---------------------------------------------------------------------------
# Iterator / span helpers
# ---------------------------------------------------------------------------

def is_native_iterable(tpy_type: "TpyType", registry: "TypeRegistry") -> bool:
    """Check if type extends NativeIterable (uses range-based for in C++)."""
    record = registry.get_record_for_type(tpy_type)
    if record is None:
        return False
    # Builtin types: check extends_protocols strings
    if record.extends_protocols:
        if any(ext.startswith("NativeIterable") for ext in record.extends_protocols):
            return True
    # User records: check implemented_protocols (includes auto-derived from __span__)
    for proto in record.implemented_protocols:
        if proto.name == "NativeIterable":
            return True
    return False


def get_extends_protocol_type_arg(
    tpy_type: "TpyType", protocol_name: str,
    registry: "TypeRegistry | None" = None,
) -> "TpyType | None":
    """Extract the first type arg from a type's extends declaration for a protocol.

    For example, Ptr[Int32] extends Deref[T] with T=Int32, so
    get_extends_protocol_type_arg(Ptr[Int32], "Deref") returns Int32.

    Checks builtin type extends strings first, then user record
    implemented_protocols if a registry is provided.
    """
    from tpyc.typesys import impl_proto_matches_name, get_protocol_qname

    type_params = extract_type_params(tpy_type)

    # Check RecordInfo extends_protocols
    if registry is not None:
        record_info = registry.get_record_for_type(tpy_type)
        if record_info is not None:
            for ext in record_info.extends_protocols:
                match = re.match(r"(\w+)\[(.+)\]", ext)
                if match and match.group(1) == protocol_name:
                    return _resolve_extends_type_arg(match.group(2), type_params)

            # Fallback: check implemented_protocols (NamedType objects)
            target_qname = get_protocol_qname(protocol_name)
            for impl_proto in record_info.implemented_protocols:
                if impl_proto_matches_name(impl_proto, protocol_name, target_qname) and impl_proto.type_args:
                    return impl_proto.type_args[0]

    return None


def get_error_return_next_element_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    """If type has __next__() with @error_return(StopIteration), return element type T."""
    from tpyc.typesys import NamedType, unwrap_ref_type

    tpy_type = unwrap_ref_type(tpy_type)
    if not isinstance(tpy_type, NamedType) or not tpy_type.is_user_record:
        return None
    return _find_error_return_next_element(tpy_type.name, tpy_type.type_args, registry)


def _find_error_return_next_element(
    record_name: str, type_args: "list[TpyType] | None", registry: "TypeRegistry",
) -> "TpyType | None":
    """Walk a record's method table (and parent chain) looking for __next__() with error_return."""
    from tpyc.typesys import NamedType, OwnType, TypeParamRef, unwrap_ref_type

    record = registry.find_record(record_name)
    if record is None:
        return None

    type_subst: dict[str, "TpyType"] = {}
    if record.type_params and type_args:
        type_subst = dict(zip(record.type_params, type_args))

    for method in record.get_method_overloads("__next__"):
        if method.error_return_type == "builtins.StopIteration" and len(method.params) == 0:
            inner = unwrap_ref_type(method.return_type)
            if isinstance(inner, OwnType):
                inner = inner.wrapped
            if isinstance(inner, TypeParamRef) and inner.name in type_subst:
                return type_subst[inner.name]
            return inner

    # Walk parent chain
    if record.parent and isinstance(record.parent, NamedType) and record.parent.is_user_record:
        parent_args = list(record.parent.type_args) if record.parent.type_args else None
        if type_subst and parent_args:
            parent_args = [
                type_subst[a.name] if isinstance(a, TypeParamRef) and a.name in type_subst else a
                for a in parent_args
            ]
        return _find_error_return_next_element(record.parent.name, parent_args, registry)

    return None


@dataclass
class IterInfo:
    """Result of checking __iter__() on a type."""
    element_type: "TpyType"
    iter_is_native: bool  # True if __iter__ returns a NativeIterable (can use begin/end directly)


def get_iter_info(tpy_type: "TpyType", registry: "TypeRegistry") -> "IterInfo | None":
    """If type has __iter__() returning a concrete iterator, return element type and dispatch info."""
    from tpyc.typesys import NamedType, unwrap_ref_type

    tpy_type = unwrap_ref_type(tpy_type)
    # Try user records (walks parent chain).
    # allow_protocol_return=True: user __iter__ returning Iterator[T] is recognized.
    if isinstance(tpy_type, NamedType) and tpy_type.is_user_record:
        record = registry.get_record(tpy_type.name)
        if record is not None:
            type_subst: dict[str, "TpyType"] = {"Self": tpy_type}
            if record.type_params and tpy_type.type_args:
                type_subst.update(zip(record.type_params, tpy_type.type_args))
            result = _find_record_iter_info(record, type_subst, registry,
                                            allow_protocol_return=True)
            if result is not None:
                return result

    # Try builtin types via registry.
    # allow_protocol_return=True: builtin __iter__() stubs return Iterator[T]
    # (e.g. list, dict, set, Span, Array, Range, str) and sema recognizes them
    # structurally -- this is the single iteration-protocol entry point under
    # the iterator overhaul (see docs/ITERATOR_OVERHAUL.md).
    record = registry.get_record_for_type(tpy_type)
    if record is not None:
        # extract_type_params handles NamedType.type_args as well as
        # specialized subclasses (RangeType.elem, DictType.key_type/value_type,
        # PtrType.pointee, etc.) via each type's get_element_type() hook.
        # Include Self so that __iter__(self) -> Self substitutes to the
        # record's own type (used by SpanIter and similar self-iterator types).
        type_subst_b = extract_type_params(tpy_type)
        type_subst_b["Self"] = tpy_type
        result = _find_record_iter_info(record, type_subst_b, registry,
                                        allow_protocol_return=True)
        if result is not None:
            return result

    return None


def get_iter_element_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    """If type has __iter__() returning a concrete iterator, return element type T."""
    info = get_iter_info(tpy_type, registry)
    return info.element_type if info is not None else None


def _find_record_iter_info(
    record: "RecordInfo", type_subst: "dict[str, TpyType]", registry: "TypeRegistry",
    *, allow_protocol_return: bool = False,
) -> "IterInfo | None":
    """Check if record (or its parents) has __iter__() returning a concrete iterator."""
    from tpyc.typesys import NamedType, TypeParamRef

    result = _find_iter_method_info(record.get_method_overloads("__iter__"), type_subst, registry,
                                    allow_protocol_return=allow_protocol_return)
    if result is not None:
        return result

    # Walk parent chain (recursive)
    if record.parent and isinstance(record.parent, NamedType) and record.parent.is_user_record:
        parent_info = registry.get_record(record.parent.name)
        if parent_info:
            parent_subst = dict(type_subst)
            if record.parent.type_args and parent_info.type_params:
                for param_name, arg in zip(parent_info.type_params, record.parent.type_args):
                    if isinstance(arg, TypeParamRef) and arg.name in type_subst:
                        parent_subst[param_name] = type_subst[arg.name]
                    else:
                        parent_subst[param_name] = arg
            return _find_record_iter_info(parent_info, parent_subst, registry,
                                          allow_protocol_return=allow_protocol_return)

    return None


def _find_iter_method_info(
    methods: "list[MethodDef | FunctionInfo]", type_subst: "dict[str, TpyType]", registry: "TypeRegistry",
    *, allow_protocol_return: bool = False,
) -> "IterInfo | None":
    """Check __iter__() methods for a concrete iterator return type and extract element type."""
    from tpyc.typesys import FunctionInfo, NamedType, OwnType, SelfType, SpanIterType, TypeParamRef, is_protocol_type, unwrap_ref_type

    for method in methods:
        if len(method.params) != 0:
            continue
        if isinstance(method, FunctionInfo):
            ret = unwrap_ref_type(method.return_type)
        else:
            ret = method.returns
        # __iter__(self) -> Self handling. SelfType returns substitute
        # to the record's own type. User iterators with Self return
        # resolve to their NamedType (is_user_record=True) and are
        # handled by the error_return __next__ branch below.
        # Note: builtins like SpanIter whose __iter__ returns Self end
        # up as plain NamedType("SpanIter", ...) after parser resolution
        # and are NOT matched here; they rely on the compiler-internal
        # adapter list in IterableHelper. See ITERATOR_OVERHAUL.md
        # follow-up "Parser produces plain NamedType for Self ...".
        if isinstance(ret, SelfType) and "Self" in type_subst:
            ret = type_subst["Self"]
        if isinstance(ret, TypeParamRef) and ret.name in type_subst:
            ret = type_subst[ret.name]
        if isinstance(ret, OwnType):
            ret = ret.wrapped

        # SpanIter[T] -- known NativeIterable with element type T
        if isinstance(ret, SpanIterType):
            elem = ret.element_type
            if isinstance(elem, TypeParamRef) and elem.name in type_subst:
                elem = type_subst[elem.name]
            return IterInfo(elem, iter_is_native=True)

        # Iterator[T] protocol return type (not Iterable -- codegen emits
        # .__next__() directly, which requires Iterator, not Iterable).
        # Recursively substitute type params in compound element types so
        # that e.g. dict_items.__iter__() -> Iterator[tuple[K, V]] yields
        # tuple[str, Point] when instantiated as dict_items[str, Point].
        if (allow_protocol_return
                and is_protocol_type(ret) and isinstance(ret, NamedType)
                and ret.qualified_name() == "typing.Iterator" and ret.type_args):
            elem = ret.type_args[0]
            if type_subst:
                try:
                    elem = _resolve_type_or_param(elem, type_subst)
                except ValueError:
                    # Unresolved TypeParamRef (top-level or nested via
                    # map_inner_types). Skip this branch -- elem is still
                    # a TypeParamRef and the guard below rejects it.
                    pass
            if not isinstance(elem, TypeParamRef):
                return IterInfo(elem, iter_is_native=False)

        # User-defined iterator with error_return __next__
        if isinstance(ret, NamedType) and ret.is_user_record:
            iter_type_args = ret.type_args
            if iter_type_args and type_subst:
                iter_type_args = [
                    type_subst.get(a.name, a) if isinstance(a, TypeParamRef) else a
                    for a in iter_type_args
                ]
            elem = _find_error_return_next_element(ret.name, iter_type_args, registry)
            if elem is not None:
                iter_native = is_native_iterable(ret, registry)
                return IterInfo(elem, iter_is_native=iter_native)

    return None


def get_span_element_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    """If type has __span__() -> Span[T] or Span[readonly[T]], return element type T."""
    span_type = get_span_return_type(tpy_type, registry)
    if span_type is not None:
        return span_type.element_type
    return None


def get_span_return_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "SpanType | None":
    """If type has __span__() -> Span[T] or Span[readonly[T]], return the full SpanType."""
    from tpyc.typesys import NamedType, SpanType, TypeParamRef

    if isinstance(tpy_type, NamedType) and tpy_type.is_user_record:
        record = registry.get_record(tpy_type.name)
    else:
        # Builtin types (list, Array, Span, etc.)
        record = registry.get_record_for_type(tpy_type)
    if record is None:
        return None
    type_subst: dict[str, "TpyType"] = {}
    if record.type_params and isinstance(tpy_type, NamedType) and tpy_type.type_args:
        type_subst = dict(zip(record.type_params, tpy_type.type_args))
    return _find_span_method_return_type(record, type_subst, registry)


def _find_span_method_return_type(
    record: "RecordInfo", type_subst: "dict[str, TpyType]", registry: "TypeRegistry",
) -> "SpanType | None":
    """Check if record has __span__() returning Span[T]/Span[readonly[T]], and return the SpanType."""
    from tpyc.typesys import NamedType, SpanType, TypeParamRef

    for method in record.get_method_overloads("__span__"):
        if len(method.params) != 0:
            continue
        ret = method.return_type
        if isinstance(ret, TypeParamRef) and ret.name in type_subst:
            ret = type_subst[ret.name]
        if isinstance(ret, SpanType):
            # Use inner_element_type (unwrapped) for TypeParamRef resolution,
            # then reconstruct with the original is_readonly.
            elem = ret.inner_element_type
            if isinstance(elem, TypeParamRef) and elem.name in type_subst:
                elem = type_subst[elem.name]
            return SpanType(elem, is_readonly=ret.is_readonly)

    # Walk parent chain
    if record.parent and isinstance(record.parent, NamedType) and record.parent.is_user_record:
        parent_info = registry.get_record(record.parent.name)
        if parent_info:
            parent_subst = dict(type_subst)
            if record.parent.type_args and parent_info.type_params:
                for param_name, arg in zip(parent_info.type_params, record.parent.type_args):
                    if isinstance(arg, TypeParamRef) and arg.name in type_subst:
                        parent_subst[param_name] = type_subst[arg.name]
                    else:
                        parent_subst[param_name] = arg
            return _find_span_method_return_type(parent_info, parent_subst, registry)

    return None
