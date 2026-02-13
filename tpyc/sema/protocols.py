"""
TurboPython Protocol Checking

Protocol conformance checking and method/field lookups.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, TypeParamRef, SelfType, OwnType,
    MethodSignature, FunctionInfo, FieldInfo, RecordInfo, is_protocol_type,
)
from ..coercions import resolve_coercion, CoercionContext

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations

from tpyc import modules as builtin_modules


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

        # Check explicit extends declaration (for builtin types with marker protocols)
        if builtin_modules.type_extends_protocol(actual, protocol.name, protocol.type_args):
            return True

        # Marker protocols require explicit extends
        if protocol_info.is_marker:
            return False

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

            if not self.type_has_method_with_signature(
                actual, method_sig.name, expected_params, expected_return
            ):
                return False

        # Collect all required fields (including inherited)
        all_fields = self.collect_protocol_fields(protocol.name)
        for field_name, field_type in all_fields:
            expected_type = self.type_ops.substitute_types(field_type, type_subst)
            if not self.type_has_field_with_type(actual, field_name, expected_type):
                return False

        return True

    def type_has_method_with_signature(
        self,
        actual: TpyType,
        method_name: str,
        expected_params: list[TpyType],
        expected_return: TpyType,
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
        elif isinstance(actual, NamedType) and actual.is_record:
            # User record - check methods in RecordInfo (including inherited methods)
            record = self.ctx.registry.get_record(actual.name)
            if record is None:
                return False
            overloads, type_subst = self.lookup_record_method_overloads(record, method_name)
            if not overloads:
                return False
            # Apply type args from the instantiated type (e.g., Counter[Int32] → T=Int32)
            if record.type_params and actual.type_args:
                record_subst = dict(zip(record.type_params, actual.type_args))
                type_subst = {**record_subst, **type_subst}
                # Transitively resolve TypeParamRef chains (e.g., Base.T→Mid.U→Child.V→Int32)
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
                # Check return type
                if resolved.return_type != expected_return:
                    continue
                # Check parameter count and types
                if len(resolved.params) != len(expected_params):
                    continue
                match = True
                for (_, actual_ptype), expected_ptype in zip(resolved.params, expected_params):
                    if actual_ptype != expected_ptype:
                        match = False
                        break
                if match:
                    return True
            return False
        else:
            # Builtin type - check via registry (unified with user records)
            record_info = self.ctx.registry.get_record_for_type(actual)
            if record_info is None:
                return False

            overloads = record_info.get_method_overloads(method_name)
            if not overloads:
                return False

            # Extract type parameters for substitution
            type_params = builtin_modules.extract_type_params(actual)

            # Check if any overload matches the expected signature
            for method_info in overloads:
                if type_params:
                    resolved = self.type_ops.substitute_method_type_params(method_info, type_params)
                else:
                    resolved = method_info

                # Check return type (allow coercions like IntLiteral -> Int32)
                if not self.types_compatible_for_protocol(resolved.return_type, expected_return):
                    continue
                # Check parameter count and types
                if len(resolved.params) != len(expected_params):
                    continue
                params_match = True
                for (_, actual_ptype), expected_ptype in zip(resolved.params, expected_params):
                    if not self.types_compatible_for_protocol(actual_ptype, expected_ptype):
                        params_match = False
                        break
                if params_match:
                    return True
            return False

    def type_has_field_with_type(self, actual: TpyType, field_name: str, expected_type: TpyType) -> bool:
        """Check if a type has a field with the expected type."""
        if isinstance(actual, NamedType) and actual.is_record:
            record = self.ctx.registry.get_record(actual.name)
            if record:
                type_subst = self.type_ops.build_type_substitution(actual)
                for fld in record.fields:
                    if fld.name == field_name:
                        field_type = fld.type
                        if type_subst:
                            field_type = self.type_ops.substitute_type_params(field_type, type_subst)
                        return field_type == expected_type
        return False

    def types_compatible_for_protocol(self, actual_return: TpyType, expected_return: TpyType) -> bool:
        """Check if actual return type is compatible with expected return type for protocol conformance.

        Allows coercions like IntLiteralType -> Int32, which enables
        PendingList[IntLiteral] to match NativeIterable[Int32].
        """
        if actual_return == expected_return:
            return True
        # Own[T] in implementation is compatible with T in protocol
        # (implementation takes ownership, protocol only requires the value)
        if isinstance(actual_return, OwnType) and actual_return.wrapped == expected_return:
            return True
        # Allow IntLiteralType to match any integer type it can coerce to
        if resolve_coercion(actual_return, expected_return, CoercionContext.RETURN) is not None:
            return True
        # Deref coercion: Deref[T] -> T
        deref_target = self.type_ops.get_deref_coercion_target(actual_return)
        if deref_target is not None and deref_target == expected_return:
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
            parent_info = self.ctx.registry.get_record(record_info.parent.name)
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

        # Check parent (user-defined or builtin)
        if record_info.parent:
            parent_info = self._get_parent_record_info(record_info.parent)
            if parent_info:
                # For user-defined parents, recurse; for builtins, just check directly
                if isinstance(record_info.parent, NamedType) and record_info.parent.is_record:
                    inherited, parent_subst = self.lookup_record_method_overloads(parent_info, method_name)
                    if inherited:
                        # Combine parent's substitution with this class's substitution
                        type_subst = self._get_parent_type_subst(record_info.parent, parent_info)
                        # Merge substitutions (parent_subst should already be resolved)
                        combined_subst = {**parent_subst, **type_subst}
                        return (inherited, combined_subst)
                else:
                    inherited = parent_info.get_method_overloads(method_name)
                    if inherited:
                        # Build type substitution from builtin's type args
                        type_subst = self._get_parent_type_subst(record_info.parent, parent_info)
                        return (inherited, type_subst)

        return ([], {})

    def _get_parent_record_info(self, parent_type: TpyType) -> RecordInfo | None:
        """Get RecordInfo for a parent type (user-defined or builtin).

        Args:
            parent_type: The parent type (NamedType or builtin TpyType).

        Returns:
            RecordInfo for the parent, or None if not found.
        """
        if isinstance(parent_type, NamedType) and parent_type.is_record:
            return self.ctx.registry.get_record(parent_type.name)
        else:
            qname = parent_type.qualified_name()
            return self.ctx.registry.get_builtin_record(qname) if qname else None

    def _get_parent_type_subst(
        self, parent_type: TpyType, parent_info: RecordInfo
    ) -> dict[str, TpyType | int]:
        """Build substitution map from parent's type parameters to concrete type args.

        Handles both user-defined classes (NamedType) and builtin types.

        Args:
            parent_type: The parent type as declared in the child (e.g., Container[Int32]).
            parent_info: The RecordInfo for the parent class.

        Returns:
            Mapping from parent's type parameter names to concrete types.
        """
        if not parent_info.type_params:
            return {}

        if isinstance(parent_type, NamedType) and parent_type.is_record:
            # User-defined class: extract type args directly
            if not parent_type.type_args:
                return {}
            return dict(zip(parent_info.type_params, parent_type.type_args))
        else:
            # Builtin type: use extract_type_params to get type params
            return builtin_modules.extract_type_params(parent_type)

    def get_missing_protocol_methods(self, record_type: NamedType, protocol: NamedType) -> list[str]:
        """Get list of protocol methods missing from record."""
        record_info = self.ctx.registry.get_record(record_type.name)
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

            if not self.type_has_method_with_signature(
                record_type, method_sig.name, expected_params, expected_return
            ):
                missing.append(method_sig.name)

        return missing
