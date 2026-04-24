"""
TurboPython Protocol Checking

Protocol conformance checking and method/field lookups.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

import re

from ..typesys import (
    TpyType, NominalType, TypeParamRef, SelfType, OwnType, ReadonlyType, RefType,
    MethodSignature, FunctionInfo, FieldInfo, RecordInfo, PropertyInfo, is_protocol_type,
    ListRepeatType, GenExprType, make_list, TupleType, OptionalType, IntLiteralType, FloatLiteralType, PendingListType, UnknownElementType, BIGINT, FLOAT,
    impl_proto_matches_name, get_protocol_qname,
)
from ..coercions import is_protocol_safe_coercion, is_protocol_type_arg_widening

if TYPE_CHECKING:
    from ..typesys import TypeRegistry
    from .context import SemanticContext
    from .type_ops import TypeOperations

from tpyc import modules as builtin_modules
from ..modules import get_dunder_cpp_template
from .. import qnames
from ..type_def_registry import (
    is_copy_iter, is_own_iter, is_set, is_dict, is_array, is_span, is_list,
    is_fixed_int_type, is_big_int_type, is_char_type, is_str_category,
    is_enum_type, protocol_info_of,
)
from ..typesys import is_numeric_type


def record_extends_any(actual: TpyType, protocol_name: str, registry: 'TypeRegistry') -> bool:
    """Check if a type extends any variant of a protocol (ignoring type args).

    Standalone utility for callers without ProtocolChecker access.
    Checks both implemented_protocols (user records) and extends_protocols (builtins).
    """
    record_info = registry.get_record_for_type(actual)
    if record_info is None:
        return False
    target_qname = get_protocol_qname(protocol_name)
    for impl_proto in record_info.implemented_protocols:
        if impl_proto_matches_name(impl_proto, protocol_name, target_qname):
            return True
    # Extends strings are unqualified (only set on builtin types)
    for ext in record_info.extends_protocols:
        match = re.match(r"(\w+)(?:\[.+\])?$", ext)
        if match and match.group(1) == protocol_name:
            return True
    return False


class ProtocolChecker:
    """Protocol conformance checking and method/field lookups."""

    def __init__(self, ctx: SemanticContext, type_ops: TypeOperations):
        self.ctx = ctx
        self.type_ops = type_ops

    def is_all_readonly(self, proto_info: 'ProtocolInfo') -> bool:
        """Check if all methods (including inherited) are readonly."""
        if proto_info.is_readonly:
            return True
        if not all(m.is_readonly for m in proto_info.methods):
            return False
        for parent_name in proto_info.parent_protocols:
            parent = self.ctx.registry.scan_by_short_name(parent_name)
            if parent is None:
                return False  # unknown parent -- conservatively non-readonly
            if not self.is_all_readonly(parent):
                return False
        return True  # all methods (if any) are readonly, all parents (if any) are readonly

    def type_conforms_to_protocol(self, actual: TpyType, protocol: NominalType) -> bool:
        """Check if actual type conforms to a protocol.

        Two conformance mechanisms work in parallel:
        1. Explicit extends: type declares extends=["Protocol[T]"]
        2. Structural: type has all methods required by the protocol

        For marker protocols (no methods/fields), only explicit extends works.
        For protocols with methods, either mechanism suffices.

        For generic protocols like Sequence[Int32]:
        - Build type substitution map: {"T": Int32}
        - Resolve each method signature with substitutions
        - Check if actual type has the resolved methods

        For Self type in protocols:
        - Self is substituted with the actual type being checked
        - e.g., checking Int32 against Addable with __add__(Self) -> Self
          expects __add__(Int32) -> Int32
        """
        # Unwrap ownership/const/ref wrappers -- readonly[T], Own[T], Ref[T]
        # conform to any protocol that T conforms to
        if isinstance(actual, (ReadonlyType, OwnType, RefType)):
            return self.type_conforms_to_protocol(actual.wrapped, protocol)

        # IntLiteralType: check if default int type conforms
        if isinstance(actual, IntLiteralType):
            return self.type_conforms_to_protocol(BIGINT, protocol)
        # FloatLiteralType: check if float64 conforms
        if isinstance(actual, FloatLiteralType):
            return self.type_conforms_to_protocol(FLOAT, protocol)

        # PendingListType: delegate to list[T] (resolves to list or Array, both conform).
        # Empty literal (UNKNOWN_ELEMENT) with a single-type-arg protocol trivially
        # conforms -- no elements to check. The pending-list coercion slot needed
        # to finalize the element type is written by the caller AFTER overload
        # resolution picks a winner (see CallAnalyzer._maybe_coerce_empty_list_to_protocol
        # in sema/calls.py); doing it here would cement the first-probed overload's
        # element type even if that overload is later rejected.
        if isinstance(actual, PendingListType):
            if (isinstance(actual.element_type, UnknownElementType)
                    and protocol.type_args and len(protocol.type_args) == 1):
                return True
            return self.type_conforms_to_protocol(make_list(actual.element_type), protocol)

        # GenExprType: satisfies Iterable[T] and Iterator[T]
        if isinstance(actual, GenExprType):
            if protocol.qualified_name() in (qnames.ITERABLE, qnames.ITERATOR):
                if protocol.type_args and len(protocol.type_args) == 1:
                    return self.type_ops.types_match_for_inference(actual.element_type, protocol.type_args[0])
                return True
            return False

        # CopyIter / OwnIter: satisfies Iterable[T] only (not Iterator).
        # Both have C++ begin/end but are not exposed as Iterator to prevent
        # passing to functions that call __next__() after a for-loop consumes them.
        if is_copy_iter(actual) or is_own_iter(actual):
            if protocol.qualified_name() == qnames.ITERABLE:
                if protocol.type_args and len(protocol.type_args) == 1:
                    return self.type_ops.types_match_for_inference(actual.type_args[0], protocol.type_args[0])
                return True
            return False

        # Enum/IntEnum: hashable, comparable, and equatable at C++ level
        if is_enum_type(actual):
            if protocol.qualified_name() in (qnames.HASHABLE, qnames.COMPARABLE, qnames.EQUATABLE):
                return True

        # Tuple: hashable if all element types are hashable
        if isinstance(actual, TupleType):
            if protocol.qualified_name() == qnames.HASHABLE:
                return all(
                    self.type_conforms_to_protocol(et, protocol)
                    for et in actual.element_types
                )

        # Bounded type parameter: T: Sized conforms to Sized (and any protocol its bound conforms to)
        if isinstance(actual, TypeParamRef):
            if protocol.qualified_name() in (qnames.STRINGABLE, qnames.REPRESENTABLE):
                return True
            bound = self.type_ops.get_type_param_bound(actual.name)
            if bound is not None and is_protocol_type(bound):
                return self.type_conforms_to_protocol(bound, protocol)

        # Unified lookup - all protocols (builtin and user) are in the registry
        protocol_info = protocol_info_of(protocol)
        if protocol_info is None:
            return False

        # Protocol-to-protocol: check if actual inherits from protocol (or is same protocol)
        if is_protocol_type(actual):
            if actual.name == protocol.name:
                return actual.type_args == protocol.type_args
            # Check if actual protocol inherits from the required protocol
            if self.protocol_inherits_from(actual.name, protocol.name):
                return True

        # Marker protocols require explicit extends declaration
        if protocol_info.is_marker:
            # ValueType: any type with value semantics conforms implicitly
            if protocol.qualified_name() == "tpy.ValueType" and actual.is_value_type():
                return True
            # Default: types that support default construction
            if protocol.qualified_name() == "tpy.Default" and self._is_default_constructible(actual):
                return True
            return self._check_record_extends(actual, protocol)

        # For builtin types, extends_protocols strings are maintained by the compiler
        # and are always consistent with the actual C++ implementation. Treat as
        # authoritative to avoid re-checking methods we know exist.
        if self._check_builtin_extends(actual, protocol):
            return True

        # Build type substitution map
        type_subst: dict[str, TpyType] = {"Self": actual}
        if protocol_info.type_params and protocol.type_args:
            if len(protocol_info.type_params) != len(protocol.type_args):
                return False
            type_subst.update(dict(zip(protocol_info.type_params, protocol.type_args)))

        # Collect all required methods (including inherited)
        all_methods = self.collect_protocol_methods(protocol.name)
        for method_sig in all_methods:
            expected_params = [
                self.type_ops.substitute_types(ptype, type_subst)
                for _, ptype in method_sig.params
            ]
            expected_return = self.type_ops.substitute_types(method_sig.return_type, type_subst)
            method_readonly = method_sig.is_readonly or protocol_info.is_readonly

            if not self.type_has_method_with_signature(
                actual, method_sig.name, expected_params, expected_return,
                require_readonly=method_readonly,
            ):
                return False

        # Collect all required fields (including inherited)
        all_fields = self.collect_protocol_fields(protocol.name)
        for field_name, field_type in all_fields:
            expected_type = self.type_ops.substitute_types(field_type, type_subst)
            if not self.type_has_field_with_type(actual, field_name, expected_type):
                return False

        return True

    def _check_record_extends(self, actual: TpyType, protocol: NominalType) -> bool:
        """Check if a type extends a protocol via record-level declarations.

        Unified check for both user records (implemented_protocols) and
        builtin types (extends_protocols strings). Used for marker protocols
        where explicit declaration is the only conformance path.
        """
        record_info = self.ctx.registry.get_record_for_type(actual)
        if record_info is None:
            return False

        # User records: check implemented_protocols (concrete NominalTypes)
        # Build substitution map for generic records (e.g., ArrayList[T, N] instantiated as ArrayList[Int32, 8])
        type_subst: dict[str, TpyType] = {}
        if record_info.type_params and isinstance(actual, NominalType) and actual.type_args:
            for param_name, arg in zip(record_info.type_params, actual.type_args):
                if isinstance(arg, TpyType):  # skip int-kind params (e.g. N: int)
                    type_subst[param_name] = arg

        target_qname = protocol.qualified_name() or get_protocol_qname(protocol.name)
        for impl_proto in record_info.implemented_protocols:
            if impl_proto_matches_name(impl_proto, protocol.name, target_qname):
                if len(protocol.type_args) == 0:
                    return True
                # Substitute type params in impl_proto's type_args before comparing
                resolved_args = tuple(
                    self.type_ops.substitute_types(a, type_subst) if type_subst else a
                    for a in impl_proto.type_args
                )
                if resolved_args == protocol.type_args:
                    return True

        # Builtin types: check extends_protocols strings
        return self._match_extends_protocols(record_info, actual, protocol)

    def _check_builtin_extends(self, actual: TpyType, protocol: NominalType) -> bool:
        """Check extends_protocols for builtin types only (non-marker protocols)."""
        record_info = self.ctx.registry.get_record_for_type(actual)
        if record_info is None or not record_info.extends_protocols:
            return False
        return self._match_extends_protocols(record_info, actual, protocol)

    def _match_extends_protocols(
        self, record_info: RecordInfo, actual: TpyType, protocol: NominalType,
    ) -> bool:
        """Match extends_protocols strings against an expected protocol."""
        if not record_info.extends_protocols:
            return False
        type_params = builtin_modules.extract_type_params(actual)
        for ext in record_info.extends_protocols:
            match = re.match(r"(\w+)\[(.+)\]$", ext)
            if match:
                ext_protocol = match.group(1)
                ext_type_str = match.group(2)
                if ext_protocol == protocol.name and len(protocol.type_args) == 1:
                    actual_type_arg = builtin_modules._resolve_extends_type_arg(ext_type_str, type_params)
                    if actual_type_arg is None:
                        continue
                    if actual_type_arg == protocol.type_args[0]:
                        return True
                    # Widening-only at the element level: list[Int16] satisfies
                    # Iterable[Int32], list[Int32] satisfies Iterable[BigInt],
                    # but list[Int32] does NOT satisfy Iterable[UInt8].
                    if is_protocol_type_arg_widening(
                        actual_type_arg, protocol.type_args[0], self.ctx.default_int_type,
                    ):
                        return True
            elif ext == protocol.name and not protocol.type_args:
                return True
        return False

    def _is_default_constructible(self, actual: TpyType) -> bool:
        """Check if a type supports default construction (zero-arg init)."""
        # All primitive value types are default-constructible
        if (is_numeric_type(actual) or is_char_type(actual)
                or is_str_category(actual)):
            return True
        # Empty containers are default-constructible
        if is_list(actual) or is_span(actual) or is_dict(actual) or is_set(actual):
            return True
        # Optional[T] is default-constructible (std::nullopt)
        if isinstance(actual, OptionalType):
            return True
        # Array[T, N] is default-constructible if element T is
        if is_array(actual):
            elem = actual.get_element_type()
            if elem is not None:
                default_proto = NominalType("Default", (), is_protocol=True)
                return self.type_conforms_to_protocol(elem, default_proto)
            return False
        # Tuple types: default-constructible if all element types are
        if isinstance(actual, TupleType):
            default_proto = NominalType("Default", (), is_protocol=True)
            return all(
                self.type_conforms_to_protocol(et, default_proto)
                for et in actual.element_types
            )
        # User records: default-constructible if __init__ has no required params.
        # Tolerate unresolved parser placeholders (make_default[Point]() type_args
        # may not have flowed through resolve_type) by looking up the registry
        # directly and filtering out @builtin_type stubs.
        if isinstance(actual, NominalType) and not actual.is_protocol:
            record = self.ctx.registry.get_record(actual.name)
            if record is None or record.builtin_type_key:
                return False
            if not record.has_init:
                return True
            required = sum(1 for _, _, default in record.init_params if default is None)
            return required == 0
        # TypeParamRef: only if it has an explicit Default bound (checked via
        # the standard bound-propagation path in type_conforms_to_protocol)
        return False

    # Protocols that ListRepeatType conforms to (lazy repeat range)
    _LIST_REPEAT_PROTOCOLS = {"Iterable", "Sized", "NativeIterable", "NativeRangeConstructible"}
    _GENEXPR_PROTOCOLS = {"Iterable", "Iterator"}
    _ITER_ADAPTER_PROTOCOLS = {"Iterable"}

    def type_extends_any_protocol(self, actual: TpyType, protocol_name: str) -> bool:
        """Check if a type extends any variant of a protocol (ignoring type args).

        Unified check for both user records and builtin types.
        Replaces builtin_modules.type_extends_any().
        """
        # ListRepeatType conforms to iteration/sizing protocols
        if isinstance(actual, ListRepeatType):
            return protocol_name in self._LIST_REPEAT_PROTOCOLS
        if isinstance(actual, GenExprType):
            return protocol_name in self._GENEXPR_PROTOCOLS
        if is_copy_iter(actual) or is_own_iter(actual):
            return protocol_name in self._ITER_ADAPTER_PROTOCOLS

        record_info = self.ctx.registry.get_record_for_type(actual)
        if record_info is None:
            return False

        # User records: check implemented_protocols
        target_qname = get_protocol_qname(protocol_name)
        for impl_proto in record_info.implemented_protocols:
            if impl_proto_matches_name(impl_proto, protocol_name, target_qname):
                return True

        # Extends strings are unqualified (only set on builtin types)
        for ext in record_info.extends_protocols:
            match = re.match(r"(\w+)(?:\[.+\])?$", ext)
            if match and match.group(1) == protocol_name:
                return True

        return False

    def type_has_method_with_signature(
        self,
        actual: TpyType,
        method_name: str,
        expected_params: list[TpyType],
        expected_return: TpyType,
        require_readonly: bool = False,
    ) -> bool:
        """Check if a type has a method with the expected signature.

        Works for user records (via RecordInfo), protocol types, and builtin types (via module system).

        Note: For builtin types with generic methods (e.g., list.append(value: T)), the type
        parameter comparison uses direct equality, which doesn't resolve type variables.
        This is fine for Phase 1 protocols (only Sized with __len__() -> Int32), but would
        need type parameter resolution for generic protocols like Iterable[T].
        """
        if is_protocol_type(actual):
            protocol_info = protocol_info_of(actual)
            if protocol_info is None:
                return False

            # Build type substitution map for generic protocols.
            # Include Self so that methods returning Self (e.g. Iterator.__iter__)
            # resolve to the concrete protocol type (e.g. Iterator[Int32]).
            type_subst: dict[str, TpyType] = {"Self": actual}
            if protocol_info.type_params and actual.type_args:
                type_subst.update(dict(zip(protocol_info.type_params, actual.type_args)))

            for method_sig in protocol_info.methods:
                if method_sig.name == method_name:
                    if require_readonly and not (method_sig.is_readonly or protocol_info.is_readonly):
                        return False
                    # Resolve types with substitution
                    resolved_return = self.type_ops.substitute_types(method_sig.return_type, type_subst)
                    if not self._protocol_type_matches(resolved_return, expected_return):
                        return False
                    if len(method_sig.params) != len(expected_params):
                        return False
                    for (_, actual_ptype), expected_ptype in zip(method_sig.params, expected_params):
                        resolved_ptype = self.type_ops.substitute_types(actual_ptype, type_subst)
                        # Params use equality (not _protocol_type_matches which is
                        # covariant). Only unwrap Own/Readonly/Ref wrappers.
                        cmp_actual = resolved_ptype.wrapped if isinstance(resolved_ptype, (OwnType, ReadonlyType, RefType)) else resolved_ptype
                        cmp_expected = expected_ptype.wrapped if isinstance(expected_ptype, (OwnType, ReadonlyType, RefType)) else expected_ptype
                        if cmp_actual != cmp_expected:
                            return False
                    return True
            return False
        else:
            # Record or builtin type - unified lookup via get_record_for_type
            record_info = self.ctx.registry.get_record_for_type(actual)
            if record_info is None:
                return False

            overloads, type_subst = self.lookup_record_method_overloads(record_info, method_name)
            if not overloads:
                return False

            # Build instance-level type substitution
            instance_subst = self.type_ops.build_type_substitution(actual)
            if instance_subst:
                if type_subst:
                    # Merge: resolve inherited params through instance params
                    combined = {**instance_subst}
                    for k, v in type_subst.items():
                        combined[k] = self.type_ops.substitute_type_params(v, instance_subst) if isinstance(v, TpyType) else v
                    type_subst = combined
                else:
                    type_subst = instance_subst

            # Transitively resolve TypeParamRef chains (e.g., Base.T->Mid.U->Child.V->Int32)
            if type_subst:
                changed = True
                while changed:
                    changed = False
                    for k, v in type_subst.items():
                        if isinstance(v, TypeParamRef) and v.name in type_subst and type_subst[v.name] is not v:
                            type_subst[k] = type_subst[v.name]
                            changed = True

            # Check if any overload matches the expected signature
            for method in overloads:
                resolved = self.type_ops.substitute_method_type_params(method, type_subst) if type_subst else method
                # Check readonly requirement.
                # Allow non-readonly methods when the return type has a protocol-safe
                # coercion to the expected return (e.g. __span__() -> Span[T] satisfies
                # a readonly protocol expecting Span[readonly[T]], since the compiler
                # auto-generates a const overload).
                if require_readonly and not resolved.is_readonly:
                    if not is_protocol_safe_coercion(resolved.return_type, expected_return):
                        continue
                # Check return type (Own[T] in impl matches T in protocol)
                if not self._protocol_type_matches(resolved.return_type, expected_return):
                    continue
                # Check parameter count and types (account for defaults)
                required_count = resolved.min_args
                if not (required_count <= len(expected_params) <= len(resolved.params)):
                    continue
                params_match = True
                for (_, actual_ptype), expected_ptype in zip(resolved.params, expected_params):
                    if not self._protocol_type_matches(actual_ptype, expected_ptype):
                        params_match = False
                        break
                if params_match:
                    return True
            return False

    def type_has_field_with_type(self, actual: TpyType, field_name: str, expected_type: TpyType) -> bool:
        """Check if a type has a field with the expected type."""
        if isinstance(actual, NominalType) and actual.is_record:
            record = self.ctx.registry.get_record_for_type(actual)
            if record:
                type_subst = self.type_ops.build_type_substitution(actual)
                for fld in record.fields:
                    if fld.name == field_name:
                        field_type = fld.type
                        if type_subst:
                            field_type = self.type_ops.substitute_type_params(field_type, type_subst)
                        return field_type == expected_type
        return False

    def _protocol_type_matches(self, actual: TpyType, expected: TpyType) -> bool:
        """Check if an actual method type matches the expected protocol type.

        Exact match, Own[T] unwrapping, protocol-safe coercion (e.g. StrView -> str),
        or conformance to a protocol return type.
        """
        if actual == expected:
            return True
        # SelfType in actual matches when expected is the record type (Self -> MyType)
        if isinstance(actual, SelfType) and isinstance(expected, NominalType):
            return True
        # Generic Self: MyType[T] matches bare MyType (unparameterized Self substitution)
        if (isinstance(actual, NominalType) and isinstance(expected, NominalType)
                and actual.name == expected.name and not expected.type_args):
            return True
        # Unwrap ownership/const/ref wrappers: Own[T], readonly[T], and Ref[T]
        # all satisfy protocol -> T. Ref[T] arises from make_ref on non-value
        # return types; the reference is transparent for protocol conformance.
        if isinstance(actual, (OwnType, ReadonlyType, RefType)):
            unwrapped = actual.wrapped
        else:
            unwrapped = actual
        if unwrapped != actual and unwrapped == expected:
            return True
        if is_protocol_safe_coercion(actual, expected):
            return True
        # Allow BigInt where a fixed int is expected (e.g. __len__() -> int
        # satisfies Sized which expects -> Int32). The C++ side uses
        # std::convertible_to<int32_t> so the implicit conversion is safe.
        if is_fixed_int_type(expected) and is_big_int_type(unwrapped):
            return True
        # Inherited method return type: actual is a parent of expected.
        # e.g. Counter.__iter__() -> Counter inherited by DoubleCounter,
        # checked against Iterator[T] which expects __iter__() -> Self
        # (= DoubleCounter). Counter is a valid supertype.
        if self.ctx.registry.is_subclass_of(expected, unwrapped):
            return True
        # If expected is a protocol, check if actual conforms to it
        if is_protocol_type(expected) and isinstance(expected, NominalType):
            return self.type_conforms_to_protocol(unwrapped, expected)
        return False

    def collect_protocol_methods(self, protocol_name: str, visited: set[str] | None = None) -> list[MethodSignature]:
        """Collect methods from a protocol and all its parents.

        Avoids duplicates by name (direct methods take precedence over inherited).
        """
        if visited is None:
            visited = set()
        if protocol_name in visited:
            return []  # Prevent cycles
        visited.add(protocol_name)

        protocol_info = self.ctx.registry.scan_by_short_name(protocol_name)
        if protocol_info is None:
            return []

        # Start with direct methods
        methods_by_name: dict[str, MethodSignature] = {}
        for method in protocol_info.methods:
            methods_by_name[method.name] = method

        # Add inherited methods (only if not already defined directly)
        for parent_name in protocol_info.parent_protocols:
            for method in self.collect_protocol_methods(parent_name, visited):
                if method.name not in methods_by_name:
                    methods_by_name[method.name] = method

        return list(methods_by_name.values())

    def collect_protocol_fields(self, protocol_name: str, visited: set[str] | None = None) -> list[tuple[str, TpyType]]:
        """Collect fields from a protocol and all its parents.

        Avoids duplicates by name (direct fields take precedence over inherited).
        """
        if visited is None:
            visited = set()
        if protocol_name in visited:
            return []  # Prevent cycles
        visited.add(protocol_name)

        protocol_info = self.ctx.registry.scan_by_short_name(protocol_name)
        if protocol_info is None:
            return []

        # Start with direct fields
        fields_by_name: dict[str, tuple[str, TpyType]] = {}
        for field_name, field_type in protocol_info.fields:
            fields_by_name[field_name] = (field_name, field_type)

        # Add inherited fields (only if not already defined directly)
        for parent_name in protocol_info.parent_protocols:
            for field_name, field_type in self.collect_protocol_fields(parent_name, visited):
                if field_name not in fields_by_name:
                    fields_by_name[field_name] = (field_name, field_type)

        return list(fields_by_name.values())

    def protocol_inherits_from(self, protocol_name: str, ancestor_name: str, visited: set[str] | None = None) -> bool:
        """Check if a protocol inherits from another protocol (directly or indirectly)."""
        if visited is None:
            visited = set()
        if protocol_name in visited:
            return False
        if protocol_name == ancestor_name:
            return True
        visited.add(protocol_name)

        protocol_info = self.ctx.registry.scan_by_short_name(protocol_name)
        if protocol_info is None:
            return False

        for parent_name in protocol_info.parent_protocols:
            if self.protocol_inherits_from(parent_name, ancestor_name, visited):
                return True
        return False

    def get_protocol_method_signature(
        self,
        protocol: NominalType,
        method_name: str,
        self_type: TpyType | None = None,
    ) -> tuple[list[tuple[str, TpyType]], TpyType, str | None] | None:
        """Get a method's signature from a protocol.

        Returns (params, return_type, cpp_template) with Self substituted, or None if not found.
        Searches the protocol and all its parent protocols.

        Args:
            protocol: The protocol to look up the method in
            method_name: Name of the method to find
            self_type: Type to substitute for Self (defaults to protocol if None).
                       For bounded type params like T: Sized, pass T so that
                       Self in signatures resolves to T, not the protocol.
        """
        # Default Self to the protocol type if not specified
        actual_self = self_type if self_type is not None else protocol

        protocol_info = protocol_info_of(protocol)
        if protocol_info is None:
            return None

        # Find the method (including inherited methods)
        all_methods = self.collect_protocol_methods(protocol.name)
        for method_sig in all_methods:
            if method_sig.name == method_name:
                # Build type substitution: Self -> actual_self, plus protocol type params
                type_subst: dict[str, TpyType] = {"Self": actual_self}
                if protocol_info.type_params and protocol.type_args:
                    type_subst.update(dict(zip(protocol_info.type_params, protocol.type_args)))

                # Substitute Self and type params in params and return type
                params = [
                    (pname, self.type_ops.substitute_types(ptype, type_subst))
                    for pname, ptype in method_sig.params
                ]
                return_type = self.type_ops.substitute_types(method_sig.return_type, type_subst)
                cpp_template = method_sig.cpp_template
                # .py protocols don't have cpp templates; use dunder map
                if cpp_template is None:
                    cpp_template = get_dunder_cpp_template(method_name)
                return (params, return_type, cpp_template)

        return None

    def lookup_protocol_method_return(
        self,
        protocol: NominalType,
        method_name: str,
        arg_types: list[TpyType],
    ) -> TpyType | None:
        """Look up a method's return type in a protocol, checking argument compatibility.

        For protocol-typed values, we use the protocol's method signatures.
        Self in the protocol is bound to the protocol itself (not a concrete type).

        Returns the method's return type if found and args match, None otherwise.
        """
        protocol_info = protocol_info_of(protocol)
        if protocol_info is None:
            return None

        # Build type substitution: Self -> the protocol type itself, plus type params
        type_subst: dict[str, TpyType] = {"Self": protocol}
        if protocol_info.type_params and protocol.type_args:
            type_subst.update(dict(zip(protocol_info.type_params, protocol.type_args)))

        for method_sig in protocol_info.methods:
            if method_sig.name == method_name:
                # Check argument count
                if len(method_sig.params) != len(arg_types):
                    return None

                # Check argument types (recursively substituting Self and type params)
                for (_, ptype), arg_type in zip(method_sig.params, arg_types):
                    expected_type = self.type_ops.substitute_types(ptype, type_subst)
                    if expected_type != arg_type:
                        return None

                # Return type (recursively substituting Self and type params)
                return self.type_ops.substitute_types(method_sig.return_type, type_subst)

        return None

    def lookup_record_field(self, record_info: RecordInfo, field_name: str) -> FieldInfo | None:
        """Look up a field in a record, including inherited fields.

        For generic parent classes, substitutes type parameters with concrete types.
        E.g., if Container[T] has field `value: T` and IntContainer extends Container[Int32],
        looking up `value` on IntContainer returns FieldInfo with type Int32.
        """
        # Check this record's own fields first
        for fld in record_info.fields:
            if fld.name == field_name:
                return fld
        # Walk each direct parent's chain (source / MRO order; diamond-free invariant
        # guarantees no ancestor contributes twice).
        for parent_type in record_info.parents:
            parent_info = self.ctx.registry.get_record_for_type(parent_type)
            if parent_info is None:
                continue
            inherited = self.lookup_record_field(parent_info, field_name)
            if inherited:
                type_subst = self._get_parent_type_subst(parent_type, parent_info)
                if type_subst:
                    substituted_type = self.type_ops.substitute_type_params(inherited.type, type_subst)
                    return FieldInfo(
                        name=inherited.name,
                        type=substituted_type,
                        default_value=inherited.default_value
                    )
                return inherited
        return None

    def lookup_record_method(self, record_info: RecordInfo, method_name: str) -> FunctionInfo | None:
        """Look up a method in a record, including inherited methods.

        For generic parent classes, substitutes type parameters with concrete types.
        E.g., if Container[T] has method `get() -> T` and IntContainer extends Container[Int32],
        looking up `get` on IntContainer returns FunctionInfo with return type Int32.

        Supports inheritance from both user-defined classes and builtin types.
        Delegates to lookup_record_method_overloads and returns the first overload.
        """
        overloads, type_subst = self.lookup_record_method_overloads(record_info, method_name)
        if not overloads:
            return None
        method = overloads[0]
        return self.type_ops.substitute_method_type_params(method, type_subst) if type_subst else method

    def lookup_record_property(self, record_info: RecordInfo, prop_name: str) -> 'PropertyInfo | None':
        """Look up a property by name, including inherited properties."""
        prop = record_info.properties.get(prop_name)
        if prop:
            return prop
        for parent_type in record_info.parents:
            parent_info = self.ctx.registry.get_record_for_type(parent_type)
            if parent_info is None:
                continue
            inherited = self.lookup_record_property(parent_info, prop_name)
            if inherited:
                return inherited
        return None

    def lookup_record_method_overloads(
        self, record_info: RecordInfo, method_name: str
    ) -> tuple[list[FunctionInfo], dict[str, TpyType | int]]:
        """Look up all overloads for a method in a record, including inherited methods.

        Returns (overloads, type_subst) where type_subst should be applied to
        substitute parent type parameters with concrete types.

        For inherited methods from builtin types with multiple overloads (like pop()),
        this returns all overloads so proper overload resolution can be done.
        """
        # Check this record's own methods first
        overloads = record_info.get_method_overloads(method_name)
        if overloads:
            return (overloads, {})

        # Check parents (user-defined or builtin) -- unified recursive path over each base.
        # Multi-base conflict-check in registration ensures we don't silently pick between
        # two conflicting definitions here, so first-match-wins is safe.
        for parent_type in record_info.parents:
            parent_info = self.ctx.registry.get_record_for_type(parent_type)
            if parent_info is None:
                continue
            inherited, parent_subst = self.lookup_record_method_overloads(parent_info, method_name)
            if inherited:
                type_subst = self._get_parent_type_subst(parent_type, parent_info)
                combined_subst = {**parent_subst, **type_subst}
                return (inherited, combined_subst)

        return ([], {})

    def _get_parent_type_subst(
        self, parent_type: TpyType, parent_info: RecordInfo
    ) -> dict[str, TpyType | int]:
        """Build substitution map from parent's type parameters to concrete type args.

        For NominalType parents (user or module), extracts type args directly.
        For non-NominalType parents (etc.), uses extract_type_params.
        """
        if not parent_info.type_params:
            return {}

        if isinstance(parent_type, NominalType):
            if not parent_type.type_args:
                return {}
            return dict(zip(parent_info.type_params, parent_type.type_args))
        return builtin_modules.extract_type_params(parent_type)

    def get_protocol_conformance_issues(self, record_type: NominalType, protocol: NominalType) -> list[str]:
        """Get human-readable list of conformance issues for a record against a protocol.

        Distinguishes genuinely-absent methods from methods that exist with a
        wrong signature (wrong return type, wrong params, or missing @readonly).
        Each entry is a short phrase describing one reason the record does not
        conform.
        """
        record_info = self.ctx.registry.get_record_for_type(record_type)
        if record_info is None:
            return []

        protocol_info = protocol_info_of(protocol)
        if protocol_info is None:
            return []

        issues: list[str] = []
        # Build type substitution map
        type_subst: dict[str, TpyType] = {"Self": record_type}
        if protocol_info.type_params and protocol.type_args:
            type_subst.update(dict(zip(protocol_info.type_params, protocol.type_args)))

        # Collect all required methods (including inherited)
        all_methods = self.collect_protocol_methods(protocol.name)
        for method_sig in all_methods:
            expected_params = [
                self.type_ops.substitute_types(ptype, type_subst)
                for _, ptype in method_sig.params
            ]
            expected_return = self.type_ops.substitute_types(method_sig.return_type, type_subst)
            method_readonly = method_sig.is_readonly or protocol_info.is_readonly

            if self.type_has_method_with_signature(
                record_type, method_sig.name, expected_params, expected_return,
                require_readonly=method_readonly,
            ):
                continue
            issues.append(self._describe_method_mismatch(
                record_type, method_sig.name, expected_params, expected_return,
                method_readonly,
            ))

        # Check fields
        all_fields = self.collect_protocol_fields(protocol.name)
        for field_name, field_type in all_fields:
            expected_type = self.type_ops.substitute_types(field_type, type_subst)
            if not self.type_has_field_with_type(record_type, field_name, expected_type):
                issues.append(f"field '{field_name}: {expected_type}' missing or has wrong type")

        return issues

    def _describe_method_mismatch(
        self, actual: TpyType, method_name: str,
        expected_params: list[TpyType], expected_return: TpyType,
        require_readonly: bool,
    ) -> str:
        """Explain why type_has_method_with_signature returned False for one method."""
        expected_sig = self._format_method_signature(
            method_name, expected_params, expected_return, require_readonly)

        record_info = self.ctx.registry.get_record_for_type(actual)
        if record_info is None:
            return f"missing method '{expected_sig}'"
        overloads, inherited_subst = self.lookup_record_method_overloads(record_info, method_name)
        if not overloads:
            return f"missing method '{expected_sig}'"

        # Apply inherited-method substitution so that overloads inherited
        # from a generic parent (e.g. `Container[Int32]` giving a method
        # returning `T`) render the concrete `Int32` rather than the raw
        # `T` placeholder in the diagnostic.
        def _render(m: 'FunctionInfo') -> str:
            params = [
                self.type_ops.substitute_types(p.type, inherited_subst)
                if inherited_subst else p.type
                for p in m.params
            ]
            ret = (self.type_ops.substitute_types(m.return_type, inherited_subst)
                   if inherited_subst else m.return_type)
            return self._format_method_signature(method_name, params, ret, m.is_readonly)

        actual_sigs = [_render(m) for m in overloads]
        actual_desc = " / ".join(f"'{s}'" for s in actual_sigs)
        return (
            f"method '{method_name}' has wrong signature: expected '{expected_sig}', "
            f"got {actual_desc}"
        )

    def _format_method_signature(
        self, name: str, params: list[TpyType], return_type: TpyType, readonly: bool,
    ) -> str:
        """Short signature string for diagnostic messages."""
        prefix = "@readonly " if readonly else ""
        param_strs = ", ".join(str(p) for p in params)
        param_part = f"self, {param_strs}" if params else "self"
        return f"{prefix}def {name}({param_part}) -> {return_type}"
