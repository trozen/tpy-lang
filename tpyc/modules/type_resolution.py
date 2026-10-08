"""Method resolution, iterator/span helpers, extends-arg parsing.

Shared utilities that don't belong on a single TpyType subclass:
extends-clause string parsing, type-param substitution for method
signatures, and the iter/span element-type discovery used by sema.  The
generic-type-factory table that used to live here now lives on
`TypeDef`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tpyc.typesys import TypeRegistry, RecordInfo, FunctionInfo

from tpyc.typesys import (
    canonical_readonly, make_readonly,
    TypeParamRef, NominalType, PtrType, TupleType,
    TpyType, CHAR, OwnType, GenExprType, ReadonlyType,
    bound_as_spelled, is_any_str_type, is_protocol_type, unwrap_ref_type, unwrap_qualifiers, unwrap_readonly,
)
from tpyc.type_def_registry import is_span, is_span_iter, is_copy_iter, is_own_iter, is_iterator_adapter, protocol_info_of
from tpyc.modules.defs import ParamDef, MethodDef


def _compose_parent_subst(
    parent: "NominalType", parent_info: "RecordInfo",
    type_subst: "dict[str, TpyType]",
) -> "dict[str, TpyType]":
    """Extend type_subst with parent's type-parameter bindings from parent.type_args.

    Each parent_info.type_params[i] maps to parent.type_args[i], composed through
    the existing subst so TypeParamRefs from the child level get resolved first.
    """
    result = dict(type_subst)
    if parent.type_args and parent_info.type_params:
        for param_name, arg in zip(parent_info.type_params, parent.type_args):
            if isinstance(arg, TypeParamRef) and arg.name in type_subst:
                result[param_name] = type_subst[arg.name]
            else:
                result[param_name] = arg
    return result


# ---------------------------------------------------------------------------
# Concrete type name resolution
# ---------------------------------------------------------------------------

def _resolve_concrete_type_name(name: str) -> "TpyType | None":
    """Resolve a concrete type name (like 'char', 'int32') to its TpyType singleton.

    Used for resolving extends declarations like extends=["NativeIterable[char]"].
    """
    from tpyc.typesys import (
        CHAR, BOOL, STR, STRING, STRVIEW, VOID, BIGINT, FLOAT, FLOAT32,
        BASIC_SLICE, SLICE, BYTES, BYTEARRAY, BYTESVIEW,
        INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64,
    )

    # Map of simple type names to their singleton instances
    type_map = {
        "bool": BOOL,
        "char": CHAR,
        "int8": INT8, "int16": INT16, "int32": INT32, "int64": INT64,
        "uint8": UINT8, "uint16": UINT16, "uint32": UINT32, "uint64": UINT64,
        "float32": FLOAT32,
        "str": STR, "String": STRING, "StrView": STRVIEW,
        "int": BIGINT,
        "float": FLOAT,
        "bytes": BYTES, "bytearray": BYTEARRAY, "BytesView": BYTESVIEW,
        "basic_slice": BASIC_SLICE,
        "slice": SLICE,
        "None": VOID,
    }
    return type_map.get(name)


def _resolve_extends_type_arg(type_str: str, type_params: dict[str, "TpyType"]) -> "TpyType | None":
    """Resolve an extends type arg string to a TpyType.

    Handles:
    - Simple type param refs: "K", "V", "T"
    - Concrete type names: "char", "int32"
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
# Type param extraction and method resolution
# ---------------------------------------------------------------------------

def extract_type_params(tpy_type: "TpyType") -> dict[str, "TpyType"]:
    """Extract type parameters from a concrete type instance.

    For list[int32], returns {"T": int32}.
    For dict[str, int32], returns {"K": str, "V": int32}.
    For Container[Point, 10], returns {"T": Point}.
    For Ptr[Point], returns {"T": Point}.

    Note: Only type parameters that are themselves types are extracted.
    Integer parameters like N in Container[T, N] are not included.
    """
    from tpyc.type_def_registry import is_dict_view, is_dict
    tpy_type = bound_as_spelled(tpy_type, lists=True)
    if is_dict(tpy_type) or is_dict_view(tpy_type):
        return {"K": tpy_type.type_args[0], "V": tpy_type.type_args[1]}
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
    - NominalType (protocol) with TypeParamRef args -> resolved NominalType
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

    # Handle NominalType (protocol) with TypeParamRef in type_args
    if isinstance(t, NominalType) and t.is_protocol and t.type_args:
        resolved_args = tuple(
            _resolve_type_or_param(arg, type_params) for arg in t.type_args
        )
        return NominalType(t.name, resolved_args, is_protocol=True)

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
        resolved = resolve_method(method, {"T": int32})
        # resolved.params[0].type == int32, resolved.returns == int32
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

# Protocols whose single type argument is the iteration element type.
ITERABLE_PROTOCOL_QNAMES = frozenset({
    "typing.Iterator", "typing.Iterable",
    "tpy.NativeIterable", "tpy.Spannable",
})

def is_native_iterable(tpy_type: "TpyType", registry: "TypeRegistry") -> bool:
    """Check if type extends NativeIterable (uses range-based for in C++)."""
    record = registry.get_record_for_type(tpy_type)
    if record is None:
        return False
    # Builtin types: check extends_protocols strings
    if record.extends_protocols:
        if any(ext.startswith("NativeIterable") for ext in record.extends_protocols):
            return True
    # User records: check explicitly declared implemented_protocols
    for proto in record.implemented_protocols:
        if proto.name == "NativeIterable":
            return True
    return False


def get_extends_protocol_type_arg(
    tpy_type: "TpyType", protocol_name: str,
    registry: "TypeRegistry | None" = None,
) -> "TpyType | None":
    """Extract the first type arg from a type's extends declaration for a protocol.

    For example, Ptr[int32] extends Deref[T] with T=int32, so
    get_extends_protocol_type_arg(Ptr[int32], "Deref") returns int32.

    Checks builtin type extends strings first, then user record
    implemented_protocols if a registry is provided.
    """
    from tpyc.typesys import impl_proto_matches_name, get_protocol_qname

    # Mirror classify_protocol_conformance: conformance is on the
    # underlying record, not its Ref/readonly/Own wrapper.
    tpy_type = unwrap_qualifiers(tpy_type)

    type_params = extract_type_params(tpy_type)

    # Check RecordInfo extends_protocols
    if registry is not None:
        record_info = registry.get_record_for_type(tpy_type)
        if record_info is not None:
            for ext in record_info.extends_protocols:
                match = re.match(r"(\w+)\[(.+)\]", ext)
                if match and match.group(1) == protocol_name:
                    return _resolve_extends_type_arg(match.group(2), type_params)

            # Fallback: check implemented_protocols (NominalType objects)
            target_qname = get_protocol_qname(protocol_name)
            for impl_proto in record_info.implemented_protocols:
                if impl_proto_matches_name(impl_proto, protocol_name, target_qname) and impl_proto.type_args:
                    return impl_proto.type_args[0]

    return None


def get_error_return_next_element_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    """If type has __next__() with @error_return(StopIteration), return element type T."""
    from tpyc.typesys import NominalType, unwrap_ref_type

    tpy_type = unwrap_ref_type(tpy_type)
    if not isinstance(tpy_type, NominalType) or not tpy_type.is_user_record:
        return None
    return _find_error_return_next_element(tpy_type.name, tpy_type.type_args, registry)


def _find_error_return_next_element(
    record_name: str, type_args: "list[TpyType] | None", registry: "TypeRegistry",
) -> "TpyType | None":
    """Walk a record's method table (and parent chain) looking for __next__() with error_return."""
    from tpyc.typesys import NominalType, OwnType, TypeParamRef, unwrap_ref_type

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

    # Walk each parent chain (first match wins; diamond-free guarantees uniqueness)
    for parent in record.parents:
        if not (isinstance(parent, NominalType) and parent.is_user_record):
            continue
        parent_args = list(parent.type_args) if parent.type_args else None
        if type_subst and parent_args:
            parent_args = [
                type_subst[a.name] if isinstance(a, TypeParamRef) and a.name in type_subst else a
                for a in parent_args
            ]
        result = _find_error_return_next_element(parent.name, parent_args, registry)
        if result is not None:
            return result

    return None


def get_iter_element_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    """If type has __iter__() returning a concrete iterator, return element type T.

    A readonly container is iterable through its inner type; callers that need
    the element readonly-projected (for-loop, the unified accessor) reproject
    themselves, so unwrap here and return the bare element.
    """
    from tpyc.typesys import NominalType, unwrap_ref_type

    tpy_type = unwrap_readonly(unwrap_ref_type(tpy_type))
    # Try user records (walks parent chain).
    # allow_protocol_return=True: user __iter__ returning Iterator[T] is recognized.
    if isinstance(tpy_type, NominalType) and tpy_type.is_user_record:
        # Qname-first (get_record_for_type), not the local-only get_record(name):
        # a type reached through a cross-module return that the current module
        # never imported by name is absent from the local `records` dict, but
        # its RecordInfo -- carrying the type_params needed to bind a generic
        # __iter__() -> Iterator[T] element -- is reachable via its qname.
        record = registry.get_record_for_type(tpy_type)
        if record is not None:
            type_subst: dict[str, "TpyType"] = {"Self": tpy_type}
            if record.type_params and tpy_type.type_args:
                type_subst.update(zip(record.type_params, tpy_type.type_args))
            result = _find_record_iter_element(record, type_subst, registry,
                                               allow_protocol_return=True)
            if result is not None:
                return result

    # Try builtin types via registry.
    # allow_protocol_return=True: builtin __iter__() stubs return Iterator[T]
    # (e.g. list, dict, set, Span, Array, Range, str) and sema recognizes them
    # structurally -- this is the single iteration-protocol entry point (see
    # docs/ITERATOR_DESIGN.md).
    record = registry.get_record_for_type(tpy_type)
    if record is not None:
        # extract_type_params handles NominalType.type_args as well as
        # structural wrappers (PtrType.pointee, etc.) via each type's
        # get_element_type() hook.
        # Include Self so that __iter__(self) -> Self substitutes to the
        # record's own type (used by SpanIter and similar self-iterator types).
        type_subst_b = extract_type_params(tpy_type)
        type_subst_b["Self"] = tpy_type
        result = _find_record_iter_element(record, type_subst_b, registry,
                                           allow_protocol_return=True)
        if result is not None:
            return result

    return None


def _ancestor_iter_element(
    proto: "NominalType", visited: "set[str] | None" = None,
) -> "TpyType | None":
    """Walk parent protocols looking for an ancestor whose qname is in
    ITERABLE_PROTOCOL_QNAMES; return the inherited element type with the
    inheritance binding chain applied.

    For `class Counted[T](Iterable[T], Protocol)` instantiated as
    `Counted[int32]`, the element type is `int32` -- read off Iterable's
    type_args after substituting Counted's own T->int32 binding.
    """
    if visited is None:
        visited = set()
    info = protocol_info_of(proto)
    if info is None:
        return None
    qname = proto.qualified_name() or proto.name
    if qname in visited:
        return None
    visited.add(qname)

    self_subst: dict[str, TpyType] = {}
    if info.type_params and proto.type_args:
        for name, arg in zip(info.type_params, proto.type_args):
            if isinstance(arg, TpyType):
                self_subst[name] = arg

    for parent in info.parent_protocols:
        resolved_args = tuple(
            self_subst.get(arg.name, arg) if isinstance(arg, TypeParamRef) else arg
            for arg in parent.type_args
        )
        resolved_parent = NominalType(
            name=parent.name,
            type_args=resolved_args,
            is_protocol=True,
            _module_qname=parent._module_qname,
        )
        if resolved_parent.qualified_name() in ITERABLE_PROTOCOL_QNAMES:
            if resolved_args:
                first = resolved_args[0]
                return first if isinstance(first, TpyType) else None
            return None
        result = _ancestor_iter_element(resolved_parent, visited)
        if result is not None:
            return result
    return None


def get_iterable_element_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    """Unified element-type accessor for any iterable type.

    Single source of truth for "what element type does iterating this produce?"
    Covers all iterable categories:
    - Compiler-internal adapters (CopyIter, OwnIter, GenExpr, SpanIter)
    - Protocol-typed params (Iterator[T], Iterable[T], NativeIterable[T], Spannable[T])
    - String types -> char
    - error_return __next__ iterators
    - __iter__() method (built-in containers + user records)

    Returns None if the type is not iterable.

    A readonly iterable (readonly[list[T]], readonly[dict[K,V]], ...) is
    iterable: unwrap to inspect the container, then reproject readonly onto a
    non-value element (the const protecting the aliased element survives; value
    elements are copies), so membership / non-loop callers stay readonly-correct.
    The element is a settled type (`canonical_readonly`): a view's
    `readonly[V]` element at a value `V` is the copy `V`.
    """
    base = unwrap_ref_type(tpy_type)
    if isinstance(base, OwnType):
        base = base.wrapped
    if isinstance(base, ReadonlyType):
        elem = _iterable_element_type_inner(base.wrapped, registry)
        return make_readonly(elem) if elem is not None else None
    elem = _iterable_element_type_inner(base, registry)
    return canonical_readonly(elem) if elem is not None else None


def _iterable_element_type_inner(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    tpy_type = unwrap_ref_type(tpy_type)
    if isinstance(tpy_type, OwnType):
        tpy_type = tpy_type.wrapped

    # Compiler-internal iterator adapters (no stubs) plus SpanIter
    if is_iterator_adapter(tpy_type) or isinstance(tpy_type, GenExprType):
        return tpy_type.element_type if isinstance(tpy_type, GenExprType) else tpy_type.type_args[0]

    # Protocol-typed iterables: single type_arg is T
    if is_protocol_type(tpy_type) and tpy_type.qualified_name() in ITERABLE_PROTOCOL_QNAMES:
        if tpy_type.type_args:
            first = tpy_type.type_args[0]
            return first if isinstance(first, TpyType) else None
        return None

    # Protocol-typed iterables that inherit from one of the above (e.g.
    # `class Counted[T](Iterable[T], Protocol)`). Walks parent protocols and
    # resolves the inherited element type via the inheritance binding chain.
    if is_protocol_type(tpy_type) and isinstance(tpy_type, NominalType):
        ancestor_elem = _ancestor_iter_element(tpy_type)
        if ancestor_elem is not None:
            return ancestor_elem

    # String types -> char
    if is_any_str_type(tpy_type):
        return CHAR

    # error_return __next__ (user-defined iterators)
    er_elem = get_error_return_next_element_type(tpy_type, registry)
    if er_elem is not None:
        return er_elem

    # __iter__() method (built-in containers + user records)
    iter_elem = get_iter_element_type(tpy_type, registry)
    if iter_elem is not None:
        return iter_elem

    return None


def _find_record_iter_element(
    record: "RecordInfo", type_subst: "dict[str, TpyType]", registry: "TypeRegistry",
    *, allow_protocol_return: bool = False,
) -> "TpyType | None":
    """Check if record (or its parents) has __iter__() returning a concrete iterator; return element type."""
    from tpyc.typesys import NominalType, TypeParamRef

    result = _find_iter_method_element(record.get_method_overloads("__iter__"), type_subst, registry,
                                       allow_protocol_return=allow_protocol_return)
    if result is not None:
        return result

    # Walk each parent chain (first match wins)
    for parent in record.parents:
        if not (isinstance(parent, NominalType) and parent.is_user_record):
            continue
        # Qname-first: an inherited member from a base declared in a module the
        # current one never imported by name is still reachable by qname.
        parent_info = registry.get_record_for_type(parent)
        if parent_info is None:
            continue
        parent_subst = _compose_parent_subst(parent, parent_info, type_subst)
        result = _find_record_iter_element(parent_info, parent_subst, registry,
                                           allow_protocol_return=allow_protocol_return)
        if result is not None:
            return result

    return None


def _find_iter_method_element(
    methods: "list[MethodDef | FunctionInfo]", type_subst: "dict[str, TpyType]", registry: "TypeRegistry",
    *, allow_protocol_return: bool = False,
) -> "TpyType | None":
    """Check __iter__() methods for a concrete iterator return type and extract element type."""
    from tpyc.typesys import FunctionInfo, NominalType, OwnType, SelfType, TypeParamRef, is_protocol_type, unwrap_ref_type

    for method in methods:
        if len(method.params) != 0:
            continue
        if isinstance(method, FunctionInfo):
            ret = unwrap_ref_type(method.return_type)
        else:
            ret = method.returns
        # __iter__(self) -> Self handling. SelfType returns substitute
        # to the record's own type. User iterators with Self return
        # resolve to their NominalType (is_user_record=True) and are
        # handled by the error_return __next__ branch below.
        # Note: builtins like SpanIter whose __iter__ returns Self end
        # up as plain NominalType("SpanIter", ...) after parser resolution
        # and are NOT matched here; they rely on the compiler-internal
        # adapter list in IterableHelper. The parser doesn't resolve Self
        # to the SpanIterType typesys subclass; low-priority follow-up
        # while the adapter list stays tiny.
        if isinstance(ret, SelfType) and "Self" in type_subst:
            ret = type_subst["Self"]
        if isinstance(ret, TypeParamRef) and ret.name in type_subst:
            ret = type_subst[ret.name]
        if isinstance(ret, OwnType):
            ret = ret.wrapped

        # SpanIter[T] -- known NativeIterable with element type T
        if is_span_iter(ret):
            elem = ret.type_args[0]
            if isinstance(elem, TypeParamRef) and elem.name in type_subst:
                elem = type_subst[elem.name]
            return elem

        # Iterator[T] protocol return type (not Iterable -- codegen emits
        # .__next__() directly, which requires Iterator, not Iterable).
        # Recursively substitute type params in compound element types so
        # that e.g. dict_items.__iter__() -> Iterator[tuple[K, V]] yields
        # tuple[str, Point] when instantiated as dict_items[str, Point].
        if (allow_protocol_return
                and is_protocol_type(ret) and isinstance(ret, NominalType)
                and ret.qualified_name() == "typing.Iterator" and ret.type_args):
            elem = ret.type_args[0]
            if type_subst:
                try:
                    # Successful resolution settles the element type even when
                    # it resolves to another type param: iterating Span[T] in
                    # a generic body yields T, a valid in-scope element type
                    # (parallel to args[0] indexing). Only an unresolved param
                    # (ValueError) means the element is genuinely unknown here.
                    return _resolve_type_or_param(elem, type_subst)
                except ValueError:
                    pass
            elif not isinstance(elem, TypeParamRef):
                # No substitution context: a bare type param is meaningless.
                return elem

        # User-defined iterator with error_return __next__
        if isinstance(ret, NominalType) and ret.is_user_record:
            iter_type_args = ret.type_args
            if iter_type_args and type_subst:
                iter_type_args = [
                    type_subst.get(a.name, a) if isinstance(a, TypeParamRef) else a
                    for a in iter_type_args
                ]
            elem = _find_error_return_next_element(ret.name, iter_type_args, registry)
            if elem is not None:
                return elem

    return None


def get_span_element_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "TpyType | None":
    """If type has __span__() -> Span[T] or Span[readonly[T]], return element type T."""
    span_type = get_span_return_type(tpy_type, registry)
    if span_type is not None:
        from tpyc.typesys import unwrap_readonly
        return unwrap_readonly(span_type.type_args[0])
    return None


def get_span_return_type(tpy_type: "TpyType", registry: "TypeRegistry") -> "NominalType | None":
    """If type has __span__() -> Span[T] or Span[readonly[T]], return the full Span NominalType."""
    from tpyc.typesys import NominalType, TypeParamRef

    # Qname-first (like get_iter_element_type): one lookup resolves both a user
    # record reached cross-module without a name-import and builtins (list,
    # Array, Span, ...). The old local-only get_record(name) missed a
    # __span__-bearing record that the current module never imported by name.
    record = registry.get_record_for_type(tpy_type)
    if record is None:
        return None
    type_subst: dict[str, "TpyType"] = {}
    if record.type_params and isinstance(tpy_type, NominalType) and tpy_type.type_args:
        type_subst = dict(zip(record.type_params, tpy_type.type_args))
    return _find_span_method_return_type(record, type_subst, registry)


def _find_span_method_return_type(
    record: "RecordInfo", type_subst: "dict[str, TpyType]", registry: "TypeRegistry",
) -> "NominalType | None":
    """Check if record has __span__() returning Span[T]/Span[readonly[T]], and return the Span NominalType."""
    from tpyc.typesys import NominalType, TypeParamRef, make_span

    for method in record.get_method_overloads("__span__"):
        if len(method.params) != 0:
            continue
        ret = method.return_type
        if isinstance(ret, TypeParamRef) and ret.name in type_subst:
            ret = type_subst[ret.name]
        if is_span(ret):
            # Use inner element (unwrapped) for TypeParamRef resolution,
            # then reconstruct with the original readonly-ness.
            from tpyc.typesys import unwrap_readonly, is_readonly_span
            elem = unwrap_readonly(ret.type_args[0])
            if isinstance(elem, TypeParamRef) and elem.name in type_subst:
                elem = type_subst[elem.name]
            return make_span(elem, is_readonly=is_readonly_span(ret))

    # Walk each parent chain (first match wins)
    for parent in record.parents:
        if not (isinstance(parent, NominalType) and parent.is_user_record):
            continue
        # Qname-first: an inherited member from a base declared in a module the
        # current one never imported by name is still reachable by qname.
        parent_info = registry.get_record_for_type(parent)
        if parent_info is None:
            continue
        parent_subst = _compose_parent_subst(parent, parent_info, type_subst)
        result = _find_span_method_return_type(parent_info, parent_subst, registry)
        if result is not None:
            return result

    return None
