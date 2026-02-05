"""
TurboPython Type Registration

Registers builtin types, records, protocols, and functions.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, RecordType, ProtocolType, TypeParamRef, RecordInfo, FunctionInfo,
    TypeParamKind
)
from ..parse import TpyRecord, TpyProtocol, TpyFunction, TpyStmt, TpyVarDecl
from .diagnostics import SemanticError
from .operators import DUNDER_CPP_TEMPLATES

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker

from tpyc import modules as builtin_modules


class TypeRegistrar:
    """Registers builtin types, records, protocols, and functions."""

    def __init__(self, ctx: SemanticContext, type_ops: TypeOperations, protocols: ProtocolChecker):
        self.ctx = ctx
        self.type_ops = type_ops
        self.protocols = protocols

    def register_builtin_types(self) -> None:
        """Register builtin types as RecordInfo for unified method lookup.

        This converts BuiltinTypeDef entries from the module system into
        RecordInfo entries, enabling unified method lookup for both
        user-defined and builtin types.
        """
        for module in builtin_modules.get_all_modules():
            for qname, type_def in module.types.items():
                info = builtin_modules.builtin_type_to_record_info(qname, type_def)
                self.ctx.registry.register_builtin_record(qname, info)

    def register_builtin_functions(self) -> None:
        """Register builtin functions for unified function lookup.

        This converts BuiltinFunctionDef entries from the default modules
        (builtins + tpy) into FunctionInfo entries in the registry.
        """
        for module in [builtin_modules.get_builtins(), builtin_modules.get_tpy()]:
            for name, fn_def in module.functions.items():
                overloads = builtin_modules.builtin_function_to_info(fn_def)
                self.ctx.registry.register_builtin_function_overloads(name, overloads)

    def register_builtin_modules(self) -> None:
        """Register builtin modules for unified module lookup.

        This converts BuiltinModule entries into ModuleInfo for the registry,
        enabling unified lookup of module functions and variables.
        """
        for module in builtin_modules.get_importable_modules():
            info = builtin_modules.builtin_module_to_info(module)
            self.ctx.registry.register_module(info)

    def register_tpy_star_import(self) -> None:
        """Register all tpy exports for 'from tpy import *'.

        Registers all exported types and functions from the tpy module
        into the global namespace and imported_names tracking.
        """
        tpy_module = builtin_modules.get_tpy()

        # Register all tpy types (Int32, Array, StaticList, Span, etc.)
        for qname, type_def in tpy_module.types.items():
            # Extract simple name from qualified name (tpy.Int32 -> Int32)
            simple_name = qname.split(".")[-1]
            self.ctx.imported_names[simple_name] = ("tpy", simple_name)
            self.ctx.global_ns.bind_imported_name(simple_name, "tpy", simple_name)

        # Register all tpy functions (copy, etc.)
        for name, fn_def in tpy_module.functions.items():
            self.ctx.imported_names[name] = ("tpy", name)
            self.ctx.global_ns.bind_imported_name(name, "tpy", name)

        # Register protocols (Comparable, NativeIterable, etc.)
        for name in tpy_module.protocols:
            self.ctx.imported_names[name] = ("tpy", name)
            self.ctx.global_ns.bind_imported_name(name, "tpy", name)

    def get_module_function_overloads(self, module_name: str, func_name: str) -> list[FunctionInfo] | None:
        """Look up function overloads in a module using the unified registry."""
        module_info = self.ctx.registry.get_module(module_name)
        if module_info and func_name in module_info.functions:
            return module_info.functions[func_name]
        return None

    def register_record(self, record: TpyRecord) -> None:
        """Register a record type."""
        # For generic records, skip validation of TypeParamRef types
        is_generic = bool(record.type_params)

        # Validate field types
        for fld in record.fields:
            # Check INT type params before general validation (to provide field location)
            if isinstance(fld.type, TypeParamRef) and fld.type.kind == TypeParamKind.INT:
                raise SemanticError(
                    f"Integer type parameter '{fld.type.name}' cannot be used as a type annotation",
                    loc=fld.loc
                )
            self.type_ops.validate_type(fld.type, allow_type_param_ref=is_generic)
            # Protocol types cannot be used as field types
            if isinstance(fld.type, ProtocolType):
                raise SemanticError(
                    f"Protocol type '{fld.type.name}' cannot be used as a field type in '{record.name}'. "
                    f"Protocols are only valid for function parameters",
                    loc=fld.loc
                )

        init_params = []
        if record.init_method:
            for pname, ptype in record.init_method.params:
                # Protocol types cannot be used in __init__ parameters
                if isinstance(ptype, ProtocolType):
                    raise SemanticError(
                        f"Protocol type '{ptype.name}' cannot be used as a parameter type in '{record.name}.__init__'. "
                        f"Protocols are only valid for free function parameters"
                    )
                init_params.append((pname, ptype, None))

        # Register all methods
        methods = {}
        for method in record.methods:
            for pname, ptype in method.params:
                if not self.type_ops.is_type_param_ref(ptype):
                    self.type_ops.validate_type(ptype, allow_type_param_ref=is_generic)
                # Protocol types cannot be used in method parameters
                if isinstance(ptype, ProtocolType):
                    raise SemanticError(
                        f"Protocol type '{ptype.name}' cannot be used as a parameter type in '{record.name}.{method.name}'. "
                        f"Protocols are only valid for free function parameters"
                    )
            if not self.type_ops.is_type_param_ref(method.return_type):
                self.type_ops.validate_type(method.return_type, allow_type_param_ref=is_generic)
            # Protocol types cannot be used as method return types
            if isinstance(method.return_type, ProtocolType):
                raise SemanticError(
                    f"Protocol type '{method.return_type.name}' cannot be used as a return type in '{record.name}.{method.name}'. "
                    f"Protocols are only valid for free function parameters"
                )
            methods[method.name] = [FunctionInfo(
                name=method.name,
                params=method.params,
                return_type=method.return_type,
                is_method=True,
                is_staticmethod=method.is_staticmethod,
                cpp_template=DUNDER_CPP_TEMPLATES.get(method.name)
            )]

        # Convert parsed bounds to ProtocolType (validate they are protocols)
        type_param_bounds: dict[str, ProtocolType] = {}
        for param_name, bound_type in record.type_param_bounds.items():
            if not isinstance(bound_type, ProtocolType):
                raise SemanticError(
                    f"Type parameter bound must be a protocol, got {bound_type}",
                    None
                )
            type_param_bounds[param_name] = bound_type

        # Don't classify bases here - defer to validate_record_inheritance
        # (so forward-referenced protocols are properly recognized)
        info = RecordInfo(
            name=record.name,
            fields=record.fields,
            has_init=record.init_method is not None,
            init_params=init_params,
            methods=methods,
            type_params=record.type_params,
            type_param_kinds=record.type_param_kinds,
            type_param_bounds=type_param_bounds,
            parent=None,
            implemented_protocols=[]
        )
        self.ctx.registry.register_record(info)
        self.ctx.global_ns.bind_record(info)

    def validate_record_inheritance(self, record: TpyRecord) -> None:
        """Validate inheritance relationships for a record.

        Called after all records AND protocols are registered to allow forward references.
        This is where we classify bases into parent class vs protocol implementations.

        Supports inheritance from:
        - User-defined classes (RecordType)
        - Builtin types (ModuleType, ListType, ArrayType, etc.)
        - Protocols (ProtocolType)
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None:
            return

        # Classify bases into parent class vs protocol implementations
        # We do this here (not in register_record) so forward-referenced protocols are recognized
        parent: TpyType | None = None  # Can be RecordType or builtin type
        implemented_protocols: list[ProtocolType] = []

        for base_type in record.bases:
            # Get the base name to check if it's actually a protocol
            base_name = None
            if isinstance(base_type, ProtocolType):
                base_name = base_type.name
            elif isinstance(base_type, RecordType):
                base_name = base_type.name

            # Check if this base is actually a protocol (handles forward references)
            is_protocol = False
            if base_name and self.ctx.registry.get_protocol(base_name) is not None:
                is_protocol = True

            if is_protocol:
                # It's a protocol implementation
                if isinstance(base_type, RecordType):
                    # Convert RecordType to ProtocolType (was misclassified due to forward ref)
                    protocol_type = ProtocolType(base_type.name, base_type.type_args)
                else:
                    protocol_type = base_type
                implemented_protocols.append(protocol_type)
            elif isinstance(base_type, RecordType):
                # It's a user-defined class - check for multiple inheritance
                if parent is not None:
                    raise SemanticError(
                        f"Multiple class inheritance not allowed in '{record.name}'. "
                        f"Use protocols for multiple interfaces.",
                        record.loc
                    )
                # Check if parent is generic and requires type args
                parent_info = self.ctx.registry.get_record(base_type.name)
                if parent_info is None:
                    raise SemanticError(
                        f"Parent class '{base_type.name}' not defined for '{record.name}'",
                        record.loc
                    )
                if parent_info.is_generic() and not base_type.type_args:
                    raise SemanticError(
                        f"Generic class '{base_type.name}' requires type arguments in '{record.name}'. "
                        f"Use '{base_type.name}[T]' with appropriate type arguments.",
                        record.loc
                    )
                parent = base_type
            elif isinstance(base_type, ProtocolType):
                # Already handled above when is_protocol is True
                pass
            elif self._is_inheritable_builtin(base_type):
                # It's a builtin type - check for multiple inheritance
                if parent is not None:
                    raise SemanticError(
                        f"Multiple class inheritance not allowed in '{record.name}'. "
                        f"Use protocols for multiple interfaces.",
                        record.loc
                    )
                parent = base_type
            else:
                raise SemanticError(
                    f"Invalid base type '{base_type}' in '{record.name}'. "
                    f"Only classes, builtin types, and protocols can be inherited.",
                    record.loc
                )

        # Update RecordInfo with classified bases
        record_info.parent = parent
        record_info.implemented_protocols = implemented_protocols

        # Validate parent class (only check circular inheritance for user-defined types)
        if record_info.parent and isinstance(record_info.parent, RecordType):
            # Check for circular inheritance
            if self._has_circular_inheritance(record.name, record_info.parent.name):
                raise SemanticError(
                    f"Circular inheritance detected: '{record.name}' inherits from '{record_info.parent.name}'",
                    record.loc
                )

        # Check for method hiding (child defines method with same name as parent)
        if record_info.parent:
            self._check_method_hiding(record, record_info)

        # Validate protocol implementations
        for protocol in record_info.implemented_protocols:
            protocol_info = self.ctx.registry.get_protocol(protocol.name)
            if protocol_info is None:
                raise SemanticError(
                    f"Protocol '{protocol.name}' not defined for implementation in '{record.name}'",
                    record.loc
                )

            # Check if record implements all protocol methods
            record_type = RecordType(record.name)
            if not self.protocols.type_conforms_to_protocol(record_type, protocol):
                # Generate helpful error message listing missing methods
                missing = self.protocols.get_missing_protocol_methods(record_type, protocol)
                if missing:
                    methods_str = ", ".join(missing)
                    raise SemanticError(
                        f"Class '{record.name}' declares implementation of protocol '{protocol}' "
                        f"but is missing required methods: {methods_str}",
                        record.loc
                    )
                # If no missing methods, it might be a field or signature issue
                raise SemanticError(
                    f"Class '{record.name}' declares implementation of protocol '{protocol}' "
                    f"but does not satisfy the protocol requirements",
                    record.loc
                )

    def _has_circular_inheritance(self, record_name: str, parent_name: str) -> bool:
        """Check if record_name would be in the inheritance chain of parent_name."""
        visited = set()
        current = parent_name
        while current:
            if current == record_name:
                return True
            if current in visited:
                return False  # Already detected a cycle elsewhere
            visited.add(current)
            parent_info = self.ctx.registry.get_record(current)
            if parent_info is None:
                return False
            current = parent_info.parent.name if parent_info.parent else None
        return False

    def _check_method_hiding(self, record: TpyRecord, record_info: RecordInfo) -> None:
        """Warn when child class defines method with same name as parent.

        In Python, methods use dynamic dispatch (virtual by default).
        In C++, methods use static dispatch (non-virtual by default).
        This causes different behavior when a parent method calls self.method().
        """
        if not record_info.parent:
            return

        parent_info = self._get_parent_record_info(record_info.parent)
        if not parent_info:
            return

        # Check each method defined in this class
        for method_name in record_info.methods:
            if method_name == "__init__":
                continue  # __init__ hiding is expected (constructors)

            # Check if any ancestor has this method
            ancestor_with_method = self._find_ancestor_with_method(parent_info, method_name)
            if ancestor_with_method:
                self.ctx.warning(
                    f"Method '{record.name}.{method_name}' hides '{ancestor_with_method}.{method_name}'. "
                    f"In Python, parent methods calling 'self.{method_name}()' use dynamic dispatch "
                    f"(child method called). In C++, static dispatch is used (parent method called). "
                    f"This may cause different behavior between TurboPython and CPython.",
                    record
                )

    def _find_ancestor_with_method(self, record_info: RecordInfo, method_name: str) -> str | None:
        """Find the nearest ancestor that defines a method with the given name.

        Returns the ancestor's name if found, None otherwise.
        """
        # Check this record's own methods
        if record_info.get_method(method_name) is not None:
            return record_info.name

        # Check parent recursively
        if record_info.parent:
            parent_info = self._get_parent_record_info(record_info.parent)
            if parent_info:
                return self._find_ancestor_with_method(parent_info, method_name)

        return None

    def _is_inheritable_builtin(self, typ: TpyType) -> bool:
        """Check if a type is a builtin type that can be inherited from.

        Returns True for module-defined types (like StaticList, Array) that have
        a registered RecordInfo in the builtin_records registry.
        """
        qname = typ.qualified_name()
        if qname is None:
            return False
        return self.ctx.registry.get_builtin_record(qname) is not None

    def _get_parent_record_info(self, parent_type: TpyType) -> RecordInfo | None:
        """Get RecordInfo for a parent type (user-defined or builtin).

        Args:
            parent_type: The parent type (RecordType or builtin TpyType).

        Returns:
            RecordInfo for the parent, or None if not found.
        """
        if isinstance(parent_type, RecordType):
            return self.ctx.registry.get_record(parent_type.name)
        else:
            qname = parent_type.qualified_name()
            return self.ctx.registry.get_builtin_record(qname) if qname else None

    def register_protocol(self, protocol: TpyProtocol) -> None:
        """Register a protocol type (without validating parents yet)."""
        from ..typesys import ProtocolInfo
        info = ProtocolInfo(
            name=protocol.name,
            methods=protocol.methods,
            fields=protocol.fields,
            type_params=protocol.type_params,
            parent_protocols=protocol.parent_protocols
        )
        self.ctx.registry.register_protocol(info)

    def validate_protocol_parents(self, protocol: TpyProtocol) -> None:
        """Validate that all parent protocols are actual protocols.

        Called after all protocols are registered to allow forward references.
        """
        for parent_name in protocol.parent_protocols:
            parent_info = self.ctx.registry.get_protocol(parent_name)
            if parent_info is None:
                raise SemanticError(
                    f"Protocol '{protocol.name}' inherits from '{parent_name}', "
                    f"which is not a defined protocol",
                    protocol.loc
                )
            # Generic protocols can't be inherited without type args
            if parent_info.type_params:
                raise SemanticError(
                    f"Protocol '{protocol.name}' inherits from generic protocol '{parent_name}' "
                    f"without type arguments. Generic protocol inheritance is not yet supported.",
                    protocol.loc
                )

    def register_function(self, func: TpyFunction) -> None:
        """Register a function."""
        from ..typesys import SelfType
        # Allow TypeParamRef in params/return for generic functions
        is_generic = bool(func.type_params)

        # Resolve types (converts RecordType to ProtocolType for imported protocols)
        resolved_params = []
        for pname, ptype in func.params:
            resolved_ptype = self.type_ops.resolve_type(ptype)
            self.type_ops.validate_type(resolved_ptype, allow_type_param_ref=is_generic)
            # Self type can only be used in protocol method signatures
            if isinstance(resolved_ptype, SelfType):
                raise SemanticError(
                    f"Self type cannot be used in function parameter '{pname}'. "
                    f"Self is only valid in protocol method signatures",
                    func.loc
                )
            resolved_params.append((pname, resolved_ptype))

        resolved_return = self.type_ops.resolve_type(func.return_type)
        self.type_ops.validate_type(resolved_return, allow_type_param_ref=is_generic)

        # Self type can only be used in protocol method signatures
        if isinstance(resolved_return, SelfType):
            raise SemanticError(
                f"Self type cannot be used as a return type. "
                f"Self is only valid in protocol method signatures",
                func.loc
            )

        # Protocol types cannot be used as return types (but TypeParamRef is OK)
        if isinstance(resolved_return, ProtocolType):
            raise SemanticError(
                f"Protocol type '{resolved_return.name}' cannot be used as a return type. "
                f"Protocols are only valid for function parameters",
                func.loc
            )

        # Convert parsed bounds to ProtocolType (validate they are protocols)
        type_param_bounds: dict[str, ProtocolType] = {}
        for param_name, bound_type in func.type_param_bounds.items():
            resolved_bound = self.type_ops.resolve_type(bound_type)
            if not isinstance(resolved_bound, ProtocolType):
                raise SemanticError(
                    f"Type parameter bound must be a protocol, got {resolved_bound}",
                    func.loc
                )
            type_param_bounds[param_name] = resolved_bound

        info = FunctionInfo(
            name=func.name,
            params=resolved_params,
            return_type=resolved_return,
            is_noalloc=func.is_noalloc,
            type_params=func.type_params,
            type_param_bounds=type_param_bounds
        )
        self.ctx.registry.register_function(info)
        self.ctx.global_ns.bind_function(info)

    def register_globals(self, stmts: list[TpyStmt]) -> None:
        """Register top-level variable declarations in global scope.

        Only registers explicitly typed globals here. Untyped globals are
        fully analyzed in analyze_top_level, which provides proper context
        for list literal type inference.
        """
        for stmt in stmts:
            if isinstance(stmt, TpyVarDecl) and stmt.type:
                self.ctx.global_scope.define(stmt.name, stmt.type)
                self.ctx.global_ns.bind_variable(stmt.name, stmt.type)
                # Track with line number for order-aware codegen (earliest line wins)
                decl_line = stmt.loc.line if stmt.loc else 0
                if stmt.name not in self.ctx.top_level_decls:
                    self.ctx.top_level_decls[stmt.name] = decl_line
                else:
                    self.ctx.top_level_decls[stmt.name] = min(self.ctx.top_level_decls[stmt.name], decl_line)
