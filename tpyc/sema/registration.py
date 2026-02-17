"""
TurboPython Type Registration

Registers builtin types, records, protocols, and functions.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, TypeParamRef, RecordInfo, FunctionInfo, FunctionLinkage,
    TypeParamKind, OptionalType, VoidType, ParamInfo, is_protocol_type,
    IMPLICIT_READONLY_METHODS,
)
from ..parse import TpyRecord, TpyProtocol, TpyFunction, TpyStmt, TpyVarDecl, RecordLinkage
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
        is_native = record.linkage != RecordLinkage.DEFAULT
        is_native_c = record.linkage == RecordLinkage.NATIVE_C

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
            self.type_ops.validate_type(fld.type, allow_type_param_ref=is_generic, loc=fld.loc)
            # Protocol types cannot be used as field types
            if is_protocol_type(fld.type):
                raise SemanticError(
                    f"Protocol type '{fld.type.name}' cannot be used as a field type in '{record.name}'. "
                    f"Protocols are only valid for function parameters",
                    loc=fld.loc
                )

        init_params = []
        if record.init_method:
            for pname, ptype in record.init_method.params:
                # Protocol types cannot be used in __init__ parameters
                if is_protocol_type(ptype):
                    raise SemanticError(
                        f"Protocol type '{ptype.name}' cannot be used as a parameter type in '{record.name}.__init__'. "
                        f"Protocols are only valid for free function parameters",
                        record.loc,
                    )
                init_params.append((pname, ptype, None))
        elif is_native and record.fields:
            # Native records without __init__: synthesize init_params from fields
            for fld in record.fields:
                init_params.append((fld.name, fld.type, fld.default_value))

        # Register all methods
        methods = {}
        for method in record.methods:
            for pname, ptype in method.params:
                if not self.type_ops.is_type_param_ref(ptype):
                    self.type_ops.validate_type(ptype, allow_type_param_ref=is_generic, loc=record.loc)
                # Protocol types cannot be used in method parameters
                if is_protocol_type(ptype):
                    raise SemanticError(
                        f"Protocol type '{ptype.name}' cannot be used as a parameter type in '{record.name}.{method.name}'. "
                        f"Protocols are only valid for free function parameters",
                        record.loc,
                    )
            if not self.type_ops.is_type_param_ref(method.return_type):
                self.type_ops.validate_type(method.return_type, allow_type_param_ref=is_generic, loc=record.loc)
            # Protocol types cannot be used as method return types
            if is_protocol_type(method.return_type):
                raise SemanticError(
                    f"Protocol type '{method.return_type.name}' cannot be used as a return type in '{record.name}.{method.name}'. "
                    f"Protocols are only valid for free function parameters",
                    record.loc,
                )
            resolved_readonly = method.is_readonly or (
                method.name in IMPLICIT_READONLY_METHODS and not method.readonly_opt_out
            )
            method.is_readonly = resolved_readonly
            methods[method.name] = [FunctionInfo(
                name=method.name,
                params=[ParamInfo(n, t) for n, t in method.params],
                return_type=method.return_type,
                is_readonly=resolved_readonly,
                is_method=True,
                is_staticmethod=method.is_staticmethod,
                linkage=method.linkage,
                native_name=method.native_name,
                cpp_template=DUNDER_CPP_TEMPLATES.get(method.name)
            )]

        # __next__() -> T implies __next_opt__() -> Optional[T] for protocol conformance
        if "__next__" in methods and "__next_opt__" not in methods:
            next_info = methods["__next__"][0]
            if not isinstance(next_info.return_type, (OptionalType, VoidType)):
                methods["__next_opt__"] = [FunctionInfo(
                    name="__next_opt__",
                    params=[ParamInfo(p.name, p.type) for p in next_info.params],
                    return_type=OptionalType(next_info.return_type),
                    is_method=True,
                )]

        # Convert parsed bounds to NamedType (validate they are protocols)
        type_param_bounds: dict[str, NamedType] = {}
        for param_name, bound_type in record.type_param_bounds.items():
            if not is_protocol_type(bound_type):
                raise SemanticError(
                    f"Type parameter bound must be a protocol, got {bound_type}",
                    record.loc,
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
            implemented_protocols=[],
            native_name=record.native_name,
            is_native=is_native,
            is_native_c=is_native_c,
            is_nocopy=record.is_nocopy,
        )
        self.ctx.registry.register_record(info)
        self.ctx.global_ns.bind_record(info)

    def validate_record_inheritance(self, record: TpyRecord) -> None:
        """Validate inheritance relationships for a record.

        Called after all records AND protocols are registered to allow forward references.
        This is where we classify bases into parent class vs protocol implementations.

        Supports inheritance from:
        - User-defined classes (NamedType with is_record)
        - Builtin types (ModuleType, ListType, ArrayType, etc.)
        - Protocols (NamedType with is_protocol)
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None:
            return

        # Native records cannot have bases
        if record_info.is_native and record.bases:
            raise SemanticError(
                f"@{record.linkage.value} class '{record.name}' cannot have base classes",
                record.loc
            )

        # Classify bases into parent class vs protocol implementations
        # We do this here (not in register_record) so forward-referenced protocols are recognized
        parent: TpyType | None = None  # Can be NamedType or builtin type
        implemented_protocols: list[NamedType] = []

        for base_type in record.bases:
            # Get the base name to check if it's actually a protocol
            base_name = None
            if isinstance(base_type, NamedType):
                base_name = base_type.name

            # Check if this base is actually a protocol (handles forward references)
            is_protocol_base = False
            if base_name and self.ctx.registry.get_protocol(base_name) is not None:
                is_protocol_base = True

            if is_protocol_base:
                # It's a protocol implementation - set the is_protocol flag correctly
                protocol_type = base_type.with_protocol_flag(True) if isinstance(base_type, NamedType) else base_type
                implemented_protocols.append(protocol_type)
            elif isinstance(base_type, NamedType) and base_type.is_record:
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
        if record_info.parent and isinstance(record_info.parent, NamedType) and record_info.parent.is_record:
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
            record_type = NamedType(record.name)
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
            parent_type: The parent type (NamedType or builtin TpyType).

        Returns:
            RecordInfo for the parent, or None if not found.
        """
        if isinstance(parent_type, NamedType) and parent_type.is_record:
            return self.ctx.registry.get_record(parent_type.name)
        else:
            qname = parent_type.qualified_name()
            return self.ctx.registry.get_builtin_record(qname) if qname else None

    def register_protocol(self, protocol: TpyProtocol) -> None:
        """Register a protocol type (without validating parents yet)."""
        from ..typesys import MethodSignature, ProtocolInfo
        # Resolve implicit readonly on protocol methods (same logic as records)
        resolved_methods = []
        for msig in protocol.methods:
            resolved_readonly = msig.is_readonly or (
                msig.name in IMPLICIT_READONLY_METHODS and not msig.readonly_opt_out
            )
            resolved_methods.append(MethodSignature(
                name=msig.name,
                params=msig.params,
                return_type=msig.return_type,
                is_readonly=resolved_readonly,
            ))
        info = ProtocolInfo(
            name=protocol.name,
            methods=resolved_methods,
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

        # Resolve types (sets is_protocol flag correctly for imported protocols)
        resolved_params = []
        for pname, ptype in func.params:
            resolved_ptype = self.type_ops.resolve_type(ptype)
            try:
                self.type_ops.validate_type(resolved_ptype, allow_type_param_ref=is_generic)
            except SemanticError as e:
                raise self.ctx.error(str(e), func)
            # Self type can only be used in protocol method signatures
            if isinstance(resolved_ptype, SelfType):
                raise SemanticError(
                    f"Self type cannot be used in function parameter '{pname}'. "
                    f"Self is only valid in protocol method signatures",
                    func.loc
                )
            resolved_params.append((pname, resolved_ptype))

        resolved_return = self.type_ops.resolve_type(func.return_type)
        try:
            self.type_ops.validate_type(resolved_return, allow_type_param_ref=is_generic)
        except SemanticError as e:
            raise self.ctx.error(str(e), func)

        # Self type can only be used in protocol method signatures
        if isinstance(resolved_return, SelfType):
            raise SemanticError(
                f"Self type cannot be used as a return type. "
                f"Self is only valid in protocol method signatures",
                func.loc
            )

        # Protocol types cannot be used as return types (but TypeParamRef is OK)
        if is_protocol_type(resolved_return):
            raise SemanticError(
                f"Protocol type '{resolved_return.name}' cannot be used as a return type. "
                f"Protocols are only valid for function parameters",
                func.loc
            )

        # Convert parsed bounds to NamedType (validate they are protocols)
        type_param_bounds: dict[str, NamedType] = {}
        for param_name, bound_type in func.type_param_bounds.items():
            resolved_bound = self.type_ops.resolve_type(bound_type)
            if not is_protocol_type(resolved_bound):
                raise SemanticError(
                    f"Type parameter bound must be a protocol, got {resolved_bound}",
                    func.loc
                )
            type_param_bounds[param_name] = resolved_bound

        # Map TpyFunction linkage to FunctionInfo linkage
        linkage_map = {
            'DEFAULT': FunctionLinkage.DEFAULT,
            'NATIVE': FunctionLinkage.NATIVE,
            'NATIVE_C': FunctionLinkage.NATIVE_C,
            'EXTERN_C': FunctionLinkage.EXTERN_C,
        }
        fi_linkage = linkage_map[func.linkage.name]

        info = FunctionInfo(
            name=func.name,
            params=[ParamInfo(n, t) for n, t in resolved_params],
            return_type=resolved_return,
            is_noalloc=func.is_noalloc,
            is_readonly=func.is_readonly,
            linkage=fi_linkage,
            native_name=func.native_name,
            type_params=func.type_params,
            type_param_bounds=type_param_bounds
        )

        # Check for duplicate extern symbol names
        if fi_linkage != FunctionLinkage.DEFAULT:
            symbol = func.native_name or func.name
            if symbol in self.ctx.extern_symbols:
                prev = self.ctx.extern_symbols[symbol]
                raise self.ctx.error(
                    f"Duplicate extern symbol '{symbol}' "
                    f"(already declared by '{prev}')",
                    func
                )
            self.ctx.extern_symbols[symbol] = func.name

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
