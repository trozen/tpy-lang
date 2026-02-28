"""
TurboPython Protocol Checking

Protocol conformance checking and method/field lookups.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

import re

from ..typesys import (
    TpyType, NamedType, TypeParamRef, SelfType, OwnType,
    MethodSignature, FunctionInfo, FieldInfo, RecordInfo, is_protocol_type,
)
from ..coercions import is_protocol_safe_coercion, resolve_coercion, CoercionContext

if TYPE_CHECKING:
    from ..typesys import TypeRegistry
    from .context import SemanticContext
    from .type_ops import TypeOperations

from tpyc import modules as builtin_modules


def record_extends_any(actual: TpyType, protocol_name: str, registry: 'TypeRegistry') -> bool:
    """Check if a type extends any variant of a protocol (ignoring type args).

    Standalone utility for callers without ProtocolChecker access.
    Checks both implemented_protocols (user records) and extends_protocols (builtins).
    """
    record_info = registry.get_record_for_type(actual)
    if record_info is None:
        return False
    for impl_proto in record_info.implemented_protocols:
        if impl_proto.name == protocol_name:
            return True
    for ext in record_info.extends_protocols:
        match = re.match(r"(\w+)(?:\[\w+\])?", ext)
        if match and match.group(1) == protocol_name:
            return True
    return False


class ProtocolChecker:
    """Protocol conformance checking and method/field lookups."""

    def __init__(self, ctx: SemanticContext, type_ops: TypeOperations):
        self.ctx = ctx
        self.type_ops = type_ops

    def type_conforms_to_protocol(self, actual: TpyType, protocol: NamedType) -> bool:
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
        # Bounded type parameter: T: Sized conforms to Sized (and any protocol its bound conforms to)
        if isinstance(actual, TypeParamRef):
            bound = self.type_ops.get_type_param_bound(actual.name)
            if bound is not None and is_protocol_type(bound):
                return self.type_conforms_to_protocol(bound, protocol)

        # Unified lookup - all protocols (builtin and user) are in the registry
        protocol_info = self.ctx.registry.get_protocol(protocol.name)
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
            if protocol.name == "ValueType" and actual.is_value_type():
                return True
            return self._check_record_extends(actual, protocol)

        # Check extends_protocols for builtin types (e.g. NativeIterable[T])
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

    def _check_record_extends(self, actual: TpyType, protocol: NamedType) -> bool:
        """Check if a type extends a protocol via record-level declarations.

        Unified check for both user records (implemented_protocols) and
        builtin types (extends_protocols strings). Used for marker protocols
        where explicit declaration is the only conformance path.
        """
        record_info = self.ctx.registry.get_record_for_type(actual)
        if record_info is None:
            return False

        # User records: check implemented_protocols (concrete NamedTypes)
        for impl_proto in record_info.implemented_protocols:
            if impl_proto.name == protocol.name:
                if len(protocol.type_args) == 0 or impl_proto.type_args == protocol.type_args:
                    return True

        # Builtin types: check extends_protocols strings
        return self._match_extends_protocols(record_info, actual, protocol)

    def _check_builtin_extends(self, actual: TpyType, protocol: NamedType) -> bool:
        """Check extends_protocols for builtin types only (non-marker protocols)."""
        record_info = self.ctx.registry.get_record_for_type(actual)
        if record_info is None or not record_info.extends_protocols:
            return False
        return self._match_extends_protocols(record_info, actual, protocol)

    def _match_extends_protocols(
        self, record_info: RecordInfo, actual: TpyType, protocol: NamedType,
    ) -> bool:
        """Match extends_protocols strings against an expected protocol."""
        if not record_info.extends_protocols:
            return False
        type_params = builtin_modules.extract_type_params(actual)
        for ext in record_info.extends_protocols:
            match = re.match(r"(\w+)\[(\w+)\]", ext)
            if match:
                ext_protocol = match.group(1)
                ext_type_name = match.group(2)
                if ext_protocol == protocol.name and len(protocol.type_args) == 1:
                    if ext_type_name in type_params:
                        actual_type_arg = type_params[ext_type_name]
                    else:
                        actual_type_arg = builtin_modules._resolve_concrete_type_name(ext_type_name)
                        if actual_type_arg is None:
                            continue
                    if actual_type_arg == protocol.type_args[0]:
                        return True
                    if resolve_coercion(actual_type_arg, protocol.type_args[0], CoercionContext.RETURN) is not None:
                        return True
            elif ext == protocol.name and not protocol.type_args:
                return True
        return False

    def type_extends_any_protocol(self, actual: TpyType, protocol_name: str) -> bool:
        """Check if a type extends any variant of a protocol (ignoring type args).

        Unified check for both user records and builtin types.
        Replaces builtin_modules.type_extends_any().
        """
        record_info = self.ctx.registry.get_record_for_type(actual)
        if record_info is None:
            return False

        # User records: check implemented_protocols
        for impl_proto in record_info.implemented_protocols:
            if impl_proto.name == protocol_name:
                return True

        # Builtin types: check extends_protocols strings
        for ext in record_info.extends_protocols:
            match = re.match(r"(\w+)(?:\[\w+\])?", ext)
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
            # Protocol type - check methods in protocol definition via unified registry
            protocol_info = self.ctx.registry.get_protocol(actual.name)
            if protocol_info is None:
                return False

            # Build type substitution map for generic protocols
            type_subst: dict[str, TpyType] = {}
            if protocol_info.type_params and actual.type_args:
                type_subst = dict(zip(protocol_info.type_params, actual.type_args))

            for method_sig in protocol_info.methods:
                if method_sig.name == method_name:
                    if require_readonly and not (method_sig.is_readonly or protocol_info.is_readonly):
                        return False
                    # Resolve types with substitution
                    resolved_return = self.type_ops.substitute_types(method_sig.return_type, type_subst) if type_subst else method_sig.return_type
                    if resolved_return != expected_return:
                        return False
                    if len(method_sig.params) != len(expected_params):
                        return False
                    for (_, actual_ptype), expected_ptype in zip(method_sig.params, expected_params):
                        resolved_ptype = self.type_ops.substitute_types(actual_ptype, type_subst) if type_subst else actual_ptype
                        if resolved_ptype != expected_ptype:
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
                # Check readonly requirement
                if require_readonly and not resolved.is_readonly:
                    continue
                # Check return type (Own[T] in impl matches T in protocol)
                if not self._protocol_type_matches(resolved.return_type, expected_return):
                    continue
                # Check parameter count and types
                if len(resolved.params) != len(expected_params):
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
        if isinstance(actual, NamedType) and actual.is_record:
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

    @staticmethod
    def _protocol_type_matches(actual: TpyType, expected: TpyType) -> bool:
        """Check if an actual method type matches the expected protocol type.

        Exact match, Own[T] unwrapping, or protocol-safe coercion (e.g. StrView -> str).
        """
        if actual == expected:
            return True
        if isinstance(actual, OwnType) and actual.wrapped == expected:
            return True
        if is_protocol_safe_coercion(actual, expected):
            return True
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

        protocol_info = self.ctx.registry.get_protocol(protocol_name)
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

        protocol_info = self.ctx.registry.get_protocol(protocol_name)
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

        protocol_info = self.ctx.registry.get_protocol(protocol_name)
        if protocol_info is None:
            return False

        for parent_name in protocol_info.parent_protocols:
            if self.protocol_inherits_from(parent_name, ancestor_name, visited):
                return True
        return False

    def get_protocol_method_signature(
        self,
        protocol: NamedType,
        method_name: str,
        self_type: TpyType | None = None,
    ) -> tuple[list[tuple[str, TpyType]], TpyType] | None:
        """Get a method's signature from a protocol.

        Returns (params, return_type) with Self substituted, or None if not found.
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

        # Unified lookup - all protocols are in the registry
        protocol_info = self.ctx.registry.get_protocol(protocol.name)
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
                return (params, return_type)

        return None

    def lookup_protocol_method_return(
        self,
        protocol: NamedType,
        method_name: str,
        arg_types: list[TpyType],
    ) -> TpyType | None:
        """Look up a method's return type in a protocol, checking argument compatibility.

        For protocol-typed values, we use the protocol's method signatures.
        Self in the protocol is bound to the protocol itself (not a concrete type).

        Returns the method's return type if found and args match, None otherwise.
        """
        protocol_info = self.ctx.registry.get_protocol(protocol.name)
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
        # Check parent class
        if record_info.parent:
            parent_info = self.ctx.registry.get_record_for_type(record_info.parent)
            if parent_info:
                inherited = self.lookup_record_field(parent_info, field_name)
                if inherited:
                    # Substitute parent's type params with concrete type args
                    type_subst = self._get_parent_type_subst(record_info.parent, parent_info)
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

        # Check parent (user-defined or builtin) -- unified recursive path
        if record_info.parent:
            parent_info = self.ctx.registry.get_record_for_type(record_info.parent)
            if parent_info:
                inherited, parent_subst = self.lookup_record_method_overloads(parent_info, method_name)
                if inherited:
                    type_subst = self._get_parent_type_subst(record_info.parent, parent_info)
                    combined_subst = {**parent_subst, **type_subst}
                    return (inherited, combined_subst)

        return ([], {})

    def _get_parent_type_subst(
        self, parent_type: TpyType, parent_info: RecordInfo
    ) -> dict[str, TpyType | int]:
        """Build substitution map from parent's type parameters to concrete type args.

        For NamedType parents (user or module), extracts type args directly.
        For non-NamedType parents (ListType, ArrayType, etc.), uses extract_type_params.
        """
        if not parent_info.type_params:
            return {}

        if isinstance(parent_type, NamedType):
            if not parent_type.type_args:
                return {}
            return dict(zip(parent_info.type_params, parent_type.type_args))
        return builtin_modules.extract_type_params(parent_type)

    def get_missing_protocol_methods(self, record_type: NamedType, protocol: NamedType) -> list[str]:
        """Get list of protocol methods missing from record."""
        record_info = self.ctx.registry.get_record_for_type(record_type)
        if record_info is None:
            return []

        protocol_info = self.ctx.registry.get_protocol(protocol.name)
        if protocol_info is None:
            return []

        missing = []
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

            if not self.type_has_method_with_signature(
                record_type, method_sig.name, expected_params, expected_return,
                require_readonly=method_readonly,
            ):
                missing.append(method_sig.name)

        return missing
