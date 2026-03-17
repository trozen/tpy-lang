"""
TurboPython Type Registration

Registers builtin types, records, protocols, and functions.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, TypeParamRef, SelfType, RecordInfo, FieldInfo, FunctionInfo, FunctionLinkage,
    TypeParamKind, OwnType, VoidType, ParamInfo, MethodSignature, is_protocol_type,
    IMPLICIT_READONLY_METHODS, CONST_PARAMS_METHODS, FinalType, EnumType, IntEnumType, BoolType, SpanIterType,
    FixedIntType, StrType, StrViewType, STRVIEW, INT32, BIGINT, BOOL, UINT64,
    register_value_type_record, register_send_record, register_sync_record,
    attach_type_param_bounds,
    has_auto_readonly,
)
from ..parse import (
    TpyRecord, TpyProtocol, TpyEnum, TpyFunction, TpyExpr, TpyStmt, TpyVarDecl, RecordLinkage,
    TpyAssign, TpyFieldAccess, TpyName, TpyBinOp, TpyReturn, TpyMethodCall, TpyCall, TpyExprStmt,
)
from ..namespace import NameBinding, BindingKind
from .diagnostics import SemanticError
from .operators import DUNDER_CPP_TEMPLATES

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker

from tpyc import modules as builtin_modules

_LINKAGE_MAP = {
    'DEFAULT': FunctionLinkage.DEFAULT,
    'NATIVE': FunctionLinkage.NATIVE,
    'NATIVE_C': FunctionLinkage.NATIVE_C,
    'EXTERN_C': FunctionLinkage.EXTERN_C,
}


def _contains_self_type(typ: TpyType) -> bool:
    """Check if a type contains SelfType anywhere in its structure."""
    if isinstance(typ, SelfType):
        return True
    return any(_contains_self_type(inner) for inner in typ.inner_types())


def build_record_self_type(record: TpyRecord) -> NamedType:
    """Build a NamedType representing Self for a record, preserving type param kinds."""
    if record.type_params:
        type_args = tuple(
            TypeParamRef(
                name=tp,
                kind=record.type_param_kinds[i] if i < len(record.type_param_kinds) else TypeParamKind.TYPE,
            )
            for i, tp in enumerate(record.type_params)
        )
        return NamedType(record.name, type_args)
    return NamedType(record.name)


class TypeRegistrar:
    """Registers builtin types, records, protocols, and functions."""

    def __init__(self, ctx: SemanticContext, type_ops: TypeOperations, protocols: ProtocolChecker):
        self.ctx = ctx
        self.type_ops = type_ops
        self.protocols = protocols

    def _resolve_type_param_bounds(
        self, raw_bounds: dict[str, TpyType], loc,
    ) -> dict[str, NamedType]:
        """Resolve parsed type parameter bounds, validating each is a protocol."""
        resolved: dict[str, NamedType] = {}
        for param_name, bound_type in raw_bounds.items():
            resolved_bound = self.type_ops.resolve_type(bound_type)
            if not is_protocol_type(resolved_bound):
                raise SemanticError(
                    f"Type parameter bound must be a protocol, got {resolved_bound}",
                    loc,
                )
            resolved[param_name] = resolved_bound
        return resolved

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

        # Register Python exception base classes as empty records.
        # These are no-op in C++ codegen but allow CPython-compatible
        # error types: class MyError(Exception): pass
        base_exc = RecordInfo(name="BaseException", fields=[], is_value_type=True)
        exc = RecordInfo(name="Exception", fields=[], is_value_type=True,
                         parent=NamedType("BaseException"))
        stop_iter = RecordInfo(name="StopIteration", fields=[], is_value_type=True,
                               parent=NamedType("Exception"))
        self.ctx.registry.register_record(base_exc)
        self.ctx.registry.register_record(exc)
        self.ctx.registry.register_record(stop_iter)

    def register_builtin_functions(self) -> None:
        """Register builtin functions for unified function lookup.

        This converts BuiltinFunctionDef entries from the default modules
        (builtins + tpy) into FunctionInfo entries in the registry.
        """
        for module in [builtin_modules.get_builtins(), builtin_modules.get_tpy()]:
            for name, fn_def in module.functions.items():
                overloads = builtin_modules.builtin_function_to_info(fn_def, module.name)
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

        # Register all tpy types (Int32, Array, Span, etc.)
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

    def register_enum(self, enum: TpyEnum) -> None:
        """Register an enum type."""
        # Validate no duplicate member names
        seen_names: set[str] = set()
        for name, _, loc in enum.members:
            if name in seen_names:
                raise SemanticError(
                    f"Duplicate enum member name: '{name}'",
                    loc=loc or enum.loc,
                )
            seen_names.add(name)

        # Validate no duplicate values
        seen_values: dict[int, str] = {}
        for name, value, loc in enum.members:
            if value in seen_values:
                raise SemanticError(
                    f"Duplicate enum value {value} "
                    f"(already used by '{seen_values[value]}')",
                    loc=loc or enum.loc,
                )
            seen_values[value] = name

        # Determine underlying type
        underlying = INT32  # default
        if enum.is_int_enum and enum.underlying_type_name:
            underlying = self._resolve_int_enum_underlying(enum.underlying_type_name)
            # Validate member values fit in underlying type range
            if isinstance(underlying, FixedIntType):
                for name, value, loc in enum.members:
                    if value < underlying.min_value or value > underlying.max_value:
                        raise SemanticError(
                            f"Enum member '{name}' value {value} is out of range "
                            f"for {underlying} ({underlying.min_value}..{underlying.max_value})",
                            loc=loc or enum.loc,
                        )

        module_name = self.ctx.module_name if self.ctx.module_name != "__main__" else None
        common_args = dict(
            name=enum.name,
            members=tuple(m for m, _, _ in enum.members),
            member_values=tuple((m, v) for m, v, _ in enum.members),
            underlying_type=underlying,
            module_name=module_name,
        )
        if enum.is_int_enum:
            enum_type = IntEnumType(**common_args)
        else:
            enum_type = EnumType(**common_args)
        self.ctx.registry.register_enum(enum_type)
        self.ctx.global_ns.bind_enum(enum_type)

    _INT_ENUM_UNDERLYING_MAP: dict[str, TpyType] = {
        "int": INT32,
        "Int8": FixedIntType(8, True),
        "Int16": FixedIntType(16, True),
        "Int32": INT32,
        "Int64": FixedIntType(64, True),
        "UInt8": FixedIntType(8, False),
        "UInt16": FixedIntType(16, False),
        "UInt32": FixedIntType(32, False),
        "UInt64": FixedIntType(64, False),
    }

    def _resolve_int_enum_underlying(self, type_name: str) -> TpyType:
        """Resolve IntEnum underlying type name to TpyType."""
        result = self._INT_ENUM_UNDERLYING_MAP.get(type_name)
        if result is None:
            raise SemanticError(f"Unknown IntEnum underlying type: '{type_name}'")
        return result

    def register_record(self, record: TpyRecord) -> None:
        """Register a record type."""
        is_native = record.linkage != RecordLinkage.DEFAULT
        is_native_c = record.linkage == RecordLinkage.NATIVE_C

        # For generic records, skip validation of TypeParamRef types
        is_generic = bool(record.type_params)

        # Check for duplicate method definitions (second definition silently wins in Python,
        # but it is always a bug and can interfere with @override checks).
        # @overload stubs are exempt -- multiple stubs + one implementation share the same name.
        # Parser-cloned @auto_readonly pairs are exempt -- the mutable clone is marked
        # is_auto_readonly_mutable_clone=True so only those pairs bypass the duplicate check.
        propagate_clone_names: set[str] = {m.name for m in record.methods if m.is_auto_readonly_mutable_clone}
        overload_names: set[str] = {m.name for m in record.methods if m.is_overload_stub}
        seen_method_names: set[str] = set()
        for method in record.methods:
            if method.name in overload_names or method.name in propagate_clone_names:
                continue
            if method.name in seen_method_names:
                raise SemanticError(
                    f"Method '{method.name}' defined twice in class '{record.name}'",
                    method.loc or record.loc,
                )
            seen_method_names.add(method.name)

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
            resolved_fld_type = self.type_ops.resolve_type(fld.type)
            if is_protocol_type(resolved_fld_type):
                raise SemanticError(
                    f"Protocol type '{fld.type.name}' cannot be used as a field type in '{record.name}'. "
                    f"Protocols are only valid as function and method parameters",
                    loc=fld.loc
                )
            # StrView fields are a lifetime hazard -- use str or String instead
            if isinstance(fld.type, StrViewType):
                raise SemanticError(
                    f"StrView cannot be used as a field type (dangling reference risk). "
                    f"Use 'str' or 'String' for owned string fields",
                    loc=fld.loc
                )
            # Self cannot be used as a field type (infinite size or broken codegen)
            if _contains_self_type(fld.type):
                raise SemanticError(
                    f"Self cannot be used as a field type in '{record.name}'",
                    loc=fld.loc
                )

        del_method = record.del_method
        if del_method:
            if del_method.params:
                raise SemanticError(
                    f"'__del__' must not have parameters",
                    del_method.loc or record.loc,
                )
            if not isinstance(del_method.return_type, VoidType):
                raise SemanticError(
                    f"'__del__' must return None, got '{del_method.return_type}'",
                    del_method.loc or record.loc,
                )
            if del_method.is_staticmethod:
                raise SemanticError(
                    f"'__del__' cannot be a static method",
                    del_method.loc or record.loc,
                )
            if del_method.type_params:
                raise SemanticError(
                    f"'__del__' cannot have type parameters",
                    del_method.loc or record.loc,
                )

        # Collect inherited fields from parent @dataclass (if any)
        parent_dc_fields = self._get_parent_dataclass_fields(record)
        # All fields for __eq__/__hash__/order: parent fields first, then own
        all_dc_fields = parent_dc_fields + record.fields

        # Validate frozen consistency in dataclass inheritance (CPython raises TypeError)
        if record.is_dataclass and parent_dc_fields:
            for base in record.bases:
                if not isinstance(base, NamedType):
                    continue
                parent_info = self.ctx.registry.get_record(base.name)
                if parent_info is not None and parent_info.is_dataclass:
                    if parent_info.is_frozen and not record.is_frozen:
                        raise SemanticError(
                            f"Cannot inherit non-frozen @dataclass '{record.name}' "
                            f"from frozen @dataclass '{base.name}'",
                            record.loc,
                        )
                    if not parent_info.is_frozen and record.is_frozen:
                        raise SemanticError(
                            f"Cannot inherit frozen @dataclass '{record.name}' "
                            f"from non-frozen @dataclass '{base.name}'",
                            record.loc,
                        )
                    break

        # Synthesize __init__ for @dataclass classes without explicit __init__
        if record.is_dataclass and record.init_method:
            self.ctx.warning_from_loc(
                f"@dataclass class '{record.name}' has an explicit __init__; "
                f"@dataclass will not generate __init__",
                record.init_method.loc or record.loc,
            )
        elif record.is_dataclass and not record.init_method:
            if not all_dc_fields:
                raise SemanticError(
                    f"@dataclass class '{record.name}' must have at least one field annotation",
                    record.loc,
                )
            # Validate field ordering across parent + child: no non-default after default
            seen_default = False
            for fld in all_dc_fields:
                if fld.default_expr is not None:
                    seen_default = True
                elif seen_default:
                    raise SemanticError(
                        f"Field '{fld.name}' without default follows field with default "
                        f"in @dataclass class '{record.name}'",
                        fld.loc or record.loc,
                    )
            # Build synthetic __init__ from field annotations
            params: list[tuple[str, TpyType]] = []
            defaults: list[TpyExpr | None] = []
            body: list[TpyStmt] = []
            # Parent fields come first as params; forwarded via super().__init__()
            for fld in parent_dc_fields:
                param_type = fld.type if fld.type.is_value_type() else OwnType(fld.type)
                params.append((fld.name, param_type))
                defaults.append(fld.default_expr)
            if parent_dc_fields:
                super_args = [TpyName(fld.name) for fld in parent_dc_fields]
                super_call = TpyMethodCall(
                    obj=TpyCall(func="super", args=[]),
                    method="__init__",
                    args=super_args,
                )
                body.append(TpyExprStmt(expr=super_call))
            # Own fields
            for fld in record.fields:
                param_type = fld.type if fld.type.is_value_type() else OwnType(fld.type)
                params.append((fld.name, param_type))
                defaults.append(fld.default_expr)
                body.append(TpyAssign(
                    target=TpyFieldAccess(obj=TpyName("self"), field=fld.name),
                    value=TpyName(fld.name),
                ))
            init_fn = TpyFunction(
                name="__init__",
                params=params,
                return_type=VoidType(),
                body=body,
                is_method=True,
                defaults=defaults,
            )
            record.methods.insert(0, init_fn)

        # Synthesize __eq__ for @dataclass classes without explicit __eq__
        if record.is_dataclass and all_dc_fields:
            eq_method = next((m for m in record.methods if m.name == "__eq__"), None)
            if eq_method is not None:
                self.ctx.warning_from_loc(
                    f"@dataclass class '{record.name}' has an explicit __eq__; "
                    f"@dataclass will not generate __eq__",
                    eq_method.loc or record.loc,
                )
            else:
                other_type = NamedType(record.name)
                comparisons = [
                    TpyBinOp(
                        left=TpyFieldAccess(obj=TpyName("self"), field=fld.name),
                        op="==",
                        right=TpyFieldAccess(obj=TpyName("other"), field=fld.name),
                    )
                    for fld in all_dc_fields
                ]
                eq_expr: TpyExpr = comparisons[0]
                for cmp in comparisons[1:]:
                    eq_expr = TpyBinOp(left=eq_expr, op="&&", right=cmp)
                eq_fn = TpyFunction(
                    name="__eq__",
                    params=[("other", other_type)],
                    return_type=BoolType(),
                    body=[TpyReturn(value=eq_expr)],
                    is_method=True,
                )
                record.methods.append(eq_fn)

        init_params = []
        if record.init_method:
            init_defaults = record.init_method.defaults
            for i, (pname, ptype) in enumerate(record.init_method.params):
                has_default = bool(init_defaults) and i < len(init_defaults) and init_defaults[i] is not None
                resolved_ptype = self.type_ops.resolve_type(ptype, protocols_only=True)
                init_params.append((pname, resolved_ptype, init_defaults[i] if has_default else None))
        elif is_native and record.fields:
            # Native records without __init__: synthesize init_params from fields
            for fld in record.fields:
                init_params.append((fld.name, fld.type, fld.default_value))

        # Build the Self type for this record (used to substitute SelfType in methods)
        record_self_type = build_record_self_type(record)

        # Pre-compute ids of mutable clones from @auto_readonly (flagged by the parser).
        # Used below to prevent implicit_readonly from clobbering is_readonly=False on these
        # methods, which would break mutable-vs-const overload tie-breaking.
        mutable_clone_ids: set[int] = {id(m) for m in record.methods if m.is_auto_readonly_mutable_clone}

        # Register all methods
        methods = {}
        for method in record.methods:
            method_has_type_params = bool(method.type_params)
            allow_tpref = is_generic or method_has_type_params

            # Validate Self usage: not allowed in @staticmethod
            if method.is_staticmethod:
                for pname, ptype in method.params:
                    if _contains_self_type(ptype):
                        raise SemanticError(
                            f"Self type cannot be used in @staticmethod '{record.name}.{method.name}' "
                            f"parameter '{pname}'",
                            method.loc or record.loc,
                        )
                if _contains_self_type(method.return_type):
                    raise SemanticError(
                        f"Self type cannot be used as return type of "
                        f"@staticmethod '{record.name}.{method.name}'",
                        method.loc or record.loc,
                    )
            # Substitute Self -> record type and resolve cross-module protocol flags
            method_params = [
                (n, self.type_ops.resolve_type(
                    self.type_ops.substitute_self(t, record_self_type), protocols_only=True))
                for n, t in method.params
            ]
            method_return = self.type_ops.resolve_type(
                self.type_ops.substitute_self(method.return_type, record_self_type), protocols_only=True)

            for pname, ptype in method_params:
                if has_auto_readonly(ptype):
                    raise SemanticError(
                        f"'auto_readonly[T]' is not allowed in parameter types "
                        f"(parameter '{pname}' of '{record.name}.{method.name}'). "
                        f"Only return types of @auto_readonly methods may use it.",
                        method.loc or record.loc,
                    )
                if not self.type_ops.is_type_param_ref(ptype):
                    self.type_ops.validate_type(ptype, allow_type_param_ref=allow_tpref, loc=record.loc)
            if not self.type_ops.is_type_param_ref(method_return):
                self.type_ops.validate_type(method_return, allow_type_param_ref=allow_tpref, loc=record.loc)
            # Protocol types cannot be used as method return types.
            # Exception: @dynamic protocols can be returned as Base& (same as free functions).
            if is_protocol_type(method_return):
                pi = self.ctx.registry.get_protocol(method_return.name)
                if not (pi and pi.is_dynamic):
                    raise SemanticError(
                        f"Protocol type '{method_return.name}' cannot be used as a return type in '{record.name}.{method.name}'. "
                        f"Only @dynamic protocols can be used as return types",
                        method.loc or record.loc,
                    )
            # For the mutable clone of a auto_readonly pair, skip implicit_readonly so
            # that the mutable clone keeps is_readonly=False. This allows tie-breaking in
            # method resolution to correctly distinguish the two clones based on receiver
            # const-ness. Without this, implicitly-readonly methods like __span__ would have
            # both clones marked is_readonly=True, making tie-breaking pick the wrong clone.
            is_mutable_propagate_clone = id(method) in mutable_clone_ids
            is_implicit_readonly = (
                method.name in IMPLICIT_READONLY_METHODS
                and not method.readonly_opt_out
                and not is_mutable_propagate_clone
            )
            resolved_readonly = method.is_readonly or method.is_pure or is_implicit_readonly or (
                record.is_frozen and method.name != "__init__"
            )
            method.is_readonly = resolved_readonly
            method_type_param_bounds = self._resolve_type_param_bounds(
                method.type_param_bounds, method.loc or record.loc)
            if method_type_param_bounds:
                method.type_param_bounds.update(method_type_param_bounds)
            method_defaults = method.defaults if method.defaults else []
            # Propagate resolved types back to AST so analyzer/codegen see concrete types.
            # Attach class-level type param bounds to TypeParamRef instances so that
            # codegen can check bounds (e.g. T: ValueType) without context lookup.
            if record.type_param_bounds:
                method_params = [
                    (n, attach_type_param_bounds(t, record.type_param_bounds))
                    for n, t in method_params
                ]
                method_return = attach_type_param_bounds(method_return, record.type_param_bounds)
            # Validate that auto_readonly[T] in return type is only on @auto_readonly methods.
            # With parser cloning, clones always have auto_readonly=False and their return types
            # already have AutoReadonlyType resolved. If a user writes auto_readonly[T]
            # without the decorator, it's an error.
            if has_auto_readonly(method_return):
                raise SemanticError(
                    f"'auto_readonly[T]' in return type is only allowed on "
                    f"@auto_readonly methods ('{record.name}.{method.name}')",
                    method.loc or record.loc,
                )
            method.params = method_params
            method.return_type = method_return
            # Inplace dunders must return self (the record type), not None or Own[T]
            if method.name in CONST_PARAMS_METHODS:
                if isinstance(method_return, OwnType):
                    raise SemanticError(
                        f"'{method.name}' must return self ('{record.name}'), "
                        f"not Own[{method_return.wrapped}] -- inplace methods return self, not a new value",
                        method.loc or record.loc,
                    )
                if isinstance(method_return, VoidType):
                    raise SemanticError(
                        f"'{method.name}' must return self ('{record.name}'), not None",
                        method.loc or record.loc,
                    )
                if not (isinstance(method_return, NamedType) and method_return.name == record.name):
                    raise SemanticError(
                        f"'{method.name}' must return self ('{record.name}'), "
                        f"got '{method_return}'",
                        method.loc or record.loc,
                    )
            func_info = FunctionInfo(
                name=method.name,
                params=[
                    ParamInfo(n, t, default_expr=method_defaults[i] if i < len(method_defaults) else None)
                    for i, (n, t) in enumerate(method_params)
                ],
                return_type=method_return,
                is_readonly=resolved_readonly,
                is_pure=method.is_pure,
                is_consuming=method.is_consuming,
                is_method=True,
                is_staticmethod=method.is_staticmethod,
                linkage=method.linkage,
                native_name=method.native_name,
                cpp_template=DUNDER_CPP_TEMPLATES.get(method.name),
                type_params=list(method.type_params),
                type_param_bounds=method_type_param_bounds,
                error_return_type=method.error_return,
            )
            if method.is_overload_stub:
                # Accumulate overload stubs for this method name
                methods.setdefault(method.name, []).append(func_info)
            elif (method.name in methods
                  and method.name not in overload_names
                  and any(m.is_readonly != func_info.is_readonly for m in methods[method.name])):
                # auto_readonly clone: add the complementary readonly/mutable overload
                methods[method.name].append(func_info)
            elif method.name in methods:
                # Implementation following stubs: stubs are the callable
                # overloads. Don't add the implementation to the method list --
                # callers resolve against stubs only.
                pass
            else:
                methods[method.name] = [func_info]

        # Auto-synthesize __iter__() -> Self on iterator types (has __next__ but no __iter__)
        if "__next__" in methods and "__iter__" not in methods:
            methods["__iter__"] = [FunctionInfo(
                name="__iter__",
                params=[],
                return_type=record_self_type,
                is_method=True,
                is_readonly=False,
            )]

        # Convert parsed bounds to NamedType (validate they are protocols).
        # Same-file protocols already have is_protocol=True from the parser;
        # resolve_type fixes cross-module protocols (e.g. imported Sized).
        type_param_bounds: dict[str, NamedType] = {}
        for param_name, bound_type in record.type_param_bounds.items():
            resolved_bound = self.type_ops.resolve_type(bound_type) if not is_protocol_type(bound_type) else bound_type
            if not is_protocol_type(resolved_bound):
                raise SemanticError(
                    f"Type parameter bound must be a protocol, got {resolved_bound}",
                    record.loc,
                )
            type_param_bounds[param_name] = resolved_bound

        # Update the record's parsed bounds with resolved versions so they
        # propagate to record_ctx.type_param_bounds during method analysis.
        if type_param_bounds:
            record.type_param_bounds.update(type_param_bounds)

        # Attach bounds to TypeParamRef instances in method signatures so that
        # downstream code (codegen, type_ops) can check bounds without context lookup.
        if type_param_bounds:
            for method_list in methods.values():
                for i, func_info in enumerate(method_list):
                    new_params = [
                        ParamInfo(p.name, attach_type_param_bounds(p.type, type_param_bounds),
                                  p.requires_lvalue, p.requires_mutable, default_expr=p.default_expr)
                        for p in func_info.params
                    ]
                    new_return = attach_type_param_bounds(func_info.return_type, type_param_bounds)
                    if (any(np.type is not op.type for np, op in zip(new_params, func_info.params))
                            or new_return is not func_info.return_type):
                        method_list[i] = FunctionInfo(
                            name=func_info.name, params=new_params, return_type=new_return,
                            is_readonly=func_info.is_readonly, is_consuming=func_info.is_consuming,
                            is_method=func_info.is_method,
                            is_staticmethod=func_info.is_staticmethod, linkage=func_info.linkage,
                            native_name=func_info.native_name, cpp_template=func_info.cpp_template,
                            type_params=func_info.type_params, type_param_bounds=func_info.type_param_bounds,
                        )

        # Validate __copy__ signature
        has_copy = "__copy__" in methods
        if has_copy:
            copy_loc = next((m.loc for m in record.methods if m.name == "__copy__"), record.loc)
            if record.is_nocopy:
                raise SemanticError(
                    f"@nocopy class '{record.name}' cannot define __copy__",
                    record.loc,
                )
            copy_info = methods["__copy__"][0]
            if len(copy_info.params) > 0:
                raise SemanticError(
                    f"__copy__ must take no parameters (besides self)",
                    copy_loc,
                )
            ret = copy_info.return_type
            inner = ret.wrapped if isinstance(ret, OwnType) else ret
            if not (isinstance(inner, NamedType) and inner.name == record.name):
                raise SemanticError(
                    f"__copy__ must return {record.name}, got {ret}",
                    copy_loc,
                )

        # Synthesize __repr__ for @dataclass without explicit __repr__
        if record.is_dataclass and all_dc_fields and "__repr__" not in methods:
            methods["__repr__"] = [FunctionInfo(
                name="__repr__",
                params=[],
                return_type=StrType(),
                is_method=True,
                is_readonly=True,
            )]

        # Synthesize __hash__ for frozen dataclasses without explicit __hash__
        if record.is_frozen and all_dc_fields and "__hash__" not in methods:
            methods["__hash__"] = [FunctionInfo(
                name="__hash__",
                params=[],
                return_type=UINT64,
                is_method=True,
                is_readonly=True,
            )]

        # Synthesize ordering methods for @dataclass(order=True)
        _ORDER_DUNDERS = ("__lt__", "__le__", "__gt__", "__ge__")
        if record.is_ordered and all_dc_fields:
            for dunder in _ORDER_DUNDERS:
                if dunder in methods:
                    raise SemanticError(
                        f"@dataclass(order=True) cannot overwrite '{dunder}' "
                        f"defined in class '{record.name}'",
                        record.loc,
                    )
            other_type = NamedType(record.name)
            for dunder in _ORDER_DUNDERS:
                methods[dunder] = [FunctionInfo(
                    name=dunder,
                    params=[("other", other_type)],
                    return_type=BoolType(),
                    is_method=True,
                    is_readonly=True,
                )]

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
            is_dataclass=record.is_dataclass,
            dataclass_fields=all_dc_fields,
            is_frozen=record.is_frozen,
            is_ordered=record.is_ordered,
            has_del=record.del_method is not None,
            has_copy=has_copy,
        )
        self.ctx.registry.register_record(info)
        self.ctx.global_ns.bind_record(info)
        # Local class definition shadows any `from X import name` import
        self.ctx.user_imported_records.pop(info.name, None)
        if self.ctx.user_imported_functions.pop(info.name, None):
            self.ctx.registry.functions.pop(info.name, None)

        # Warn when a field or method name shadows an auto-synthesized C++ method
        SYNTHESIZED_FROM_DUNDER = {
            "size": "__len__",
            "begin": "__span__",
            "end": "__span__",
        }
        field_names = {f.name for f in record.fields}
        method_names = {m.name for m in record.methods}
        user_names = field_names | method_names
        for synth_name, dunder in SYNTHESIZED_FROM_DUNDER.items():
            if synth_name in user_names and dunder in methods:
                self.ctx.warning_from_loc(
                    f"'{synth_name}' shadows auto-generated C++ {synth_name}() "
                    f"from {dunder}; consider renaming",
                    record.loc,
                )

    def validate_record_inheritance(self, record: TpyRecord) -> None:
        """Validate inheritance relationships for a record.

        Called after all records AND protocols are registered to allow forward references.
        This is where we classify bases into parent class vs protocol implementations.

        Supports inheritance from:
        - User-defined classes (NamedType with is_record)
        - Builtin types (ListType, ArrayType, etc.)
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
                # Set _module_qname from the protocol's registry entry so that
                # qualified_name() returns the correct module (not just the
                # _protocol_modules fallback which is first-write-wins)
                if isinstance(protocol_type, NamedType) and not protocol_type._module_qname:
                    proto_info = self.ctx.registry.get_protocol(base_name)
                    if proto_info and proto_info.module:
                        protocol_type = NamedType(
                            protocol_type.name, protocol_type.type_args, True,
                            f"{proto_info.module}.{protocol_type.name}",
                            protocol_type.is_dynamic_protocol)
                implemented_protocols.append(protocol_type)
            elif isinstance(base_type, NamedType) and base_type.is_user_record:
                # It's a user-defined class - check for multiple inheritance
                if parent is not None:
                    raise SemanticError(
                        f"Multiple class inheritance not allowed in '{record.name}'. "
                        f"Use protocols for multiple interfaces.",
                        record.loc
                    )
                # Check if parent is generic and requires type args
                parent_info = self.ctx.registry.get_record_for_type(base_type)
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
        if record_info.parent and isinstance(record_info.parent, NamedType) and record_info.parent.is_user_record:
            # Check for circular inheritance
            if self._has_circular_inheritance(record.name, record_info.parent.name):
                raise SemanticError(
                    f"Circular inheritance detected: '{record.name}' inherits from '{record_info.parent.name}'",
                    record.loc
                )

        # Coordinate method hiding and @override checks.
        # @override methods are handled by _check_override_annotations (which emits a more
        # targeted non-polymorphic warning) and are skipped by _check_method_hiding.
        override_method_names = {m.name for m in record.methods if m.is_override}

        # Check for method hiding (child defines method with same name as parent)
        if record_info.parent:
            self._check_method_hiding(record, record_info, override_method_names)

        # Validate @override annotations (error if no match; warn if non-polymorphic)
        if override_method_names:
            self._check_override_annotations(record, record_info)

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

        # ValueType marker: set flag (field validation deferred to a second pass
        # so that all ValueType records in the module are registered first)
        for protocol in record_info.implemented_protocols:
            if protocol.qualified_name() == "tpy.ValueType":
                if record_info.is_nocopy:
                    raise SemanticError(
                        f"@nocopy class '{record.name}' cannot implement ValueType "
                        f"(value types require copy semantics)",
                        record.loc
                    )
                record_info.is_value_type = True
                register_value_type_record(record.name)
                break

        # Auto-derive NativeIterable[T] for types with __iter__() -> SpanIter[T].
        # SpanIter is a NativeIterable, so the container inherits the protocol.
        if "__iter__" in record_info.methods:
            iter_overloads = record_info.methods["__iter__"]
            if iter_overloads:
                ret = iter_overloads[0].return_type
                if isinstance(ret, SpanIterType):
                    elem_type = ret.element_type
                    ni_proto = NamedType("NativeIterable", (elem_type,), is_protocol=True,
                                        _module_qname="tpy.NativeIterable")
                    if not any(p.name == "NativeIterable" for p in record_info.implemented_protocols):
                        record_info.implemented_protocols.append(ni_proto)

        # Auto-derive Send/Sync based on field types.
        # A record is Send if all its fields are Send (safe to move across threads).
        # A record is Sync if all its fields are Sync (safe to share across threads).
        # TypeParamRef fields are assumed OK -- enforced at C++ instantiation via concepts.
        # NOTE: Modules are compiled in dependency order, so parent records from
        # imported modules are already registered in the global sets.
        is_send = all(
            f.type.is_send() or isinstance(f.type, TypeParamRef)
            for f in record_info.fields
        )
        is_sync = all(
            f.type.is_sync() or isinstance(f.type, TypeParamRef)
            for f in record_info.fields
        )
        if record_info.parent is not None:
            is_send = is_send and record_info.parent.is_send()
            is_sync = is_sync and record_info.parent.is_sync()
        if is_send:
            register_send_record(record.name)
        if is_sync:
            register_sync_record(record.name)

    def validate_value_type_fields(self, record: TpyRecord) -> None:
        """Validate that all fields of a ValueType record are themselves value types,
        and that any parent class is also a value type.

        Called in a second pass after all records have been processed, so that
        ValueType records defined later in the same module are already registered.
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None or not record_info.is_value_type:
            return
        if record_info.parent is not None and not record_info.parent.is_value_type():
            raise SemanticError(
                f"ValueType class '{record.name}': parent '{record_info.parent}' "
                f"is not a value type",
                record.loc
            )
        for fld in record_info.fields:
            if not self._is_field_value_type(fld.type):
                raise SemanticError(
                    f"ValueType class '{record.name}': field '{fld.name}' "
                    f"has non-value type '{fld.type}'",
                    fld.loc
                )

    @staticmethod
    def _is_field_value_type(typ: TpyType) -> bool:
        """Check if a field type satisfies the ValueType constraint.

        Type parameters are accepted unconditionally -- for generic ValueType
        records, the C++ is_value_type trait enforces this at instantiation time.
        """
        if typ.is_value_type():
            return True
        if isinstance(typ, TypeParamRef):
            return True
        return False

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

    def _check_method_hiding(self, record: TpyRecord, record_info: RecordInfo,
                              skip_names: set[str] | None = None) -> None:
        """Warn when child class defines method with same name as parent.

        In Python, methods use dynamic dispatch (virtual by default).
        In C++, methods use static dispatch (non-virtual by default).
        This causes different behavior when a parent method calls self.method().

        skip_names: method names to skip (e.g. @override methods handled separately).
        """
        if not record_info.parent:
            return

        parent_info = self.ctx.registry.get_record_for_type(record_info.parent)
        if not parent_info:
            return

        # Synthesized dataclass methods intentionally hide parent versions
        skip_dc: set[str] = set()
        if record_info.is_dataclass:
            skip_dc = {"__eq__", "__hash__", "__repr__"}
        if record_info.is_ordered:
            skip_dc.update({"__lt__", "__le__", "__gt__", "__ge__"})

        # Check each method defined in this class
        for method_name in record_info.methods:
            if method_name in ("__init__", "__del__"):
                continue  # constructor/destructor hiding is expected
            if method_name in skip_dc:
                continue
            if skip_names and method_name in skip_names:
                continue  # @override methods are checked (with better messages) separately

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

    def _check_override_annotations(self, record: TpyRecord, record_info: RecordInfo) -> None:
        """Validate @override-annotated methods.

        Errors if the method does not exist in any parent class or implemented protocol.
        Warns if the override is non-polymorphic (parent class, not @dynamic protocol).
        """
        override_methods = {m.name: m for m in record.methods if m.is_override}

        parent_info = self.ctx.registry.get_record_for_type(record_info.parent) if record_info.parent else None

        for method_name, method in override_methods.items():
            found_in_parent = self._find_ancestor_with_method(parent_info, method_name) if parent_info else None

            # Check each explicitly implemented protocol for the method
            found_in_protocol: str | None = None
            found_in_dynamic_protocol = False
            for proto_type in record_info.implemented_protocols:
                all_proto_methods = self._collect_all_protocol_methods(proto_type.name)
                if any(m.name == method_name for m in all_proto_methods):
                    found_in_protocol = proto_type.name
                    found_in_dynamic_protocol = getattr(proto_type, 'is_dynamic_protocol', False)
                    break

            if not found_in_parent and not found_in_protocol:
                raise SemanticError(
                    f"Method '{record.name}.{method_name}' is marked @override "
                    f"but does not override any parent class or protocol method",
                    method.loc or record.loc,
                )

            # Non-polymorphic warning: only for parent class overrides (not protocol implementations).
            # Constructors/destructors are skipped -- they are never polymorphically dispatched.
            if found_in_parent and method_name not in ("__init__", "__del__"):
                already_dynamic = found_in_dynamic_protocol
                suffix = "" if already_dynamic else " Use a @dynamic protocol for runtime dispatch."
                self.ctx.warning_from_loc(
                    f"Method '{record.name}.{method_name}' overrides '{found_in_parent}.{method_name}' "
                    f"but the override is non-polymorphic. '{found_in_parent}'-typed references "
                    f"will call '{found_in_parent}.{method_name}', not '{record.name}.{method_name}'."
                    f"{suffix}",
                    method.loc or record.loc,
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
            parent_info = self.ctx.registry.get_record_for_type(record_info.parent)
            if parent_info:
                return self._find_ancestor_with_method(parent_info, method_name)

        return None

    def _get_parent_dataclass_fields(self, record: TpyRecord) -> list[FieldInfo]:
        """Get inherited fields from parent @dataclass chain.

        Returns parent's full dataclass_fields (including grandparent fields),
        or empty list if no parent is a @dataclass.
        """
        if not record.is_dataclass:
            return []
        for base in record.bases:
            if not isinstance(base, NamedType):
                continue
            parent_info = self.ctx.registry.get_record(base.name)
            if parent_info is not None and parent_info.is_dataclass:
                return list(parent_info.dataclass_fields)
        return []

    def _is_inheritable_builtin(self, typ: TpyType) -> bool:
        """Check if a type is a builtin type that can be inherited from.

        Returns True for module-defined types (like Array) that have
        a registered RecordInfo in the builtin_records registry.
        """
        qname = typ.qualified_name()
        if qname is None:
            return False
        return self.ctx.registry.get_builtin_record(qname) is not None

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
            parent_protocols=protocol.parent_protocols,
            is_dynamic=protocol.is_dynamic,
            module=self.ctx.module_name,
        )
        self.ctx.registry.register_protocol(info)

        if protocol.is_dynamic:
            # Generic check can run immediately (doesn't need parent info)
            if protocol.type_params:
                raise SemanticError(
                    f"@dynamic protocol '{protocol.name}' cannot be generic. "
                    f"Generic @dynamic protocols are not yet supported",
                    protocol.loc
                )

    def validate_protocol_parents(self, protocol: TpyProtocol) -> None:
        """Validate that all parent protocols are actual protocols.

        Called after all protocols are registered to allow forward references.
        Also validates object safety for @dynamic protocols over the full inherited
        surface (methods + fields from all ancestor protocols).
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

        if protocol.is_dynamic:
            self._validate_dynamic_object_safety(protocol)

    def _validate_dynamic_object_safety(self, protocol: TpyProtocol) -> None:
        """Validate that a @dynamic protocol is object-safe for runtime dispatch.

        Checks the full inherited surface (methods + fields from all ancestors),
        since codegen emits all inherited members into the C++ base class.
        """
        all_methods = self._collect_all_protocol_methods(protocol.name)
        all_fields = self._collect_all_protocol_fields(protocol.name)

        if not all_methods and not all_fields:
            raise SemanticError(
                f"@dynamic protocol '{protocol.name}' must have at least one method or field",
                protocol.loc
            )

        for msig in all_methods:
            if _contains_self_type(msig.return_type):
                raise SemanticError(
                    f"@dynamic protocol '{protocol.name}' cannot use Self type "
                    f"in method '{msig.name}' return type",
                    protocol.loc
                )
            for pname, ptype in msig.params:
                if _contains_self_type(ptype):
                    raise SemanticError(
                        f"@dynamic protocol '{protocol.name}' cannot use Self type "
                        f"in method '{msig.name}' parameter '{pname}'",
                        protocol.loc
                    )

        for field_name, field_type in all_fields:
            if _contains_self_type(field_type):
                raise SemanticError(
                    f"@dynamic protocol '{protocol.name}' cannot use Self type "
                    f"in field '{field_name}'",
                    protocol.loc
                )

    def _collect_all_protocol_methods(self, protocol_name: str,
                                       visited: set[str] | None = None) -> list[MethodSignature]:
        """Collect methods from a protocol and all ancestors."""
        if visited is None:
            visited = set()
        if protocol_name in visited:
            return []
        visited.add(protocol_name)
        protocol_info = self.ctx.registry.get_protocol(protocol_name)
        if protocol_info is None:
            return []
        methods_by_name: dict[str, MethodSignature] = {}
        for method in protocol_info.methods:
            methods_by_name[method.name] = method
        for parent_name in protocol_info.parent_protocols:
            for method in self._collect_all_protocol_methods(parent_name, visited):
                if method.name not in methods_by_name:
                    methods_by_name[method.name] = method
        return list(methods_by_name.values())

    def _collect_all_protocol_fields(self, protocol_name: str,
                                      visited: set[str] | None = None) -> list[tuple[str, TpyType]]:
        """Collect fields from a protocol and all ancestors."""
        if visited is None:
            visited = set()
        if protocol_name in visited:
            return []
        visited.add(protocol_name)
        protocol_info = self.ctx.registry.get_protocol(protocol_name)
        if protocol_info is None:
            return []
        fields_by_name: dict[str, tuple[str, TpyType]] = {}
        for field_name, field_type in protocol_info.fields:
            fields_by_name[field_name] = (field_name, field_type)
        for parent_name in protocol_info.parent_protocols:
            for field_name, field_type in self._collect_all_protocol_fields(parent_name, visited):
                if field_name not in fields_by_name:
                    fields_by_name[field_name] = (field_name, field_type)
        return list(fields_by_name.values())

    def register_function(self, func: TpyFunction) -> None:
        """Register a function."""
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
            if _contains_self_type(resolved_ptype):
                raise SemanticError(
                    f"Self type cannot be used in function parameter '{pname}'. "
                    f"Self is only valid in class or protocol method signatures",
                    func.loc
                )
            resolved_params.append((pname, resolved_ptype))

        resolved_return = self.type_ops.resolve_type(func.return_type)
        try:
            self.type_ops.validate_type(resolved_return, allow_type_param_ref=is_generic)
        except SemanticError as e:
            raise self.ctx.error(str(e), func)

        if _contains_self_type(resolved_return):
            raise SemanticError(
                f"Self type cannot be used as a return type. "
                f"Self is only valid in class or protocol method signatures",
                func.loc
            )

        # Protocol types cannot be used as return types (but TypeParamRef is OK).
        # Exception: @dynamic protocols can be returned (lifetime-checked in sema).
        if is_protocol_type(resolved_return):
            pi = self.ctx.registry.get_protocol(resolved_return.name)
            if not (pi and pi.is_dynamic):
                raise SemanticError(
                    f"Protocol type '{resolved_return.name}' cannot be used as a return type. "
                    f"Only @dynamic protocols can be used as return types",
                    func.loc
                )

        type_param_bounds = self._resolve_type_param_bounds(
            func.type_param_bounds, func.loc)
        # Propagate resolved bounds back to AST so get_type_param_bound sees
        # correct is_protocol flags during body analysis (safe: fresh AST per compile)
        if type_param_bounds:
            func.type_param_bounds.update(type_param_bounds)

        fi_linkage = _LINKAGE_MAP[func.linkage.name]

        func_defaults = func.defaults if func.defaults else []
        info = FunctionInfo(
            name=func.name,
            params=[
                ParamInfo(n, t, default_expr=func_defaults[i] if i < len(func_defaults) else None)
                for i, (n, t) in enumerate(resolved_params)
            ],
            return_type=resolved_return,
            is_noalloc=func.is_noalloc,
            is_readonly=func.is_readonly or func.is_pure,
            is_pure=func.is_pure,
            linkage=fi_linkage,
            native_name=func.native_name,
            cpp_template=func.cpp_template,
            type_params=func.type_params,
            type_param_bounds=type_param_bounds,
            error_return_type=func.error_return,
            qualified_name=f"{self.ctx.module_name}.{func.name}",
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
        # Local definition shadows any `from X import name` import
        self.ctx.user_imported_functions.pop(info.name, None)
        self.ctx.user_imported_records.pop(info.name, None)

    def register_overload_group(self, stubs: list[TpyFunction]) -> None:
        """Register a group of @overload stubs as a single overloaded function binding.

        Each stub is resolved and validated. All stubs are bound together so
        call-site resolution can pick the best match.
        """
        infos: list[FunctionInfo] = []
        for func in stubs:
            is_generic = bool(func.type_params)
            resolved_params = []
            for pname, ptype in func.params:
                resolved_ptype = self.type_ops.resolve_type(ptype)
                try:
                    self.type_ops.validate_type(resolved_ptype, allow_type_param_ref=is_generic)
                except SemanticError as e:
                    raise self.ctx.error(str(e), func)
                resolved_params.append((pname, resolved_ptype))

            resolved_return = self.type_ops.resolve_type(func.return_type)
            try:
                self.type_ops.validate_type(resolved_return, allow_type_param_ref=is_generic)
            except SemanticError as e:
                raise self.ctx.error(str(e), func)

            type_param_bounds = self._resolve_type_param_bounds(
                func.type_param_bounds, func.loc)
            # Propagate resolved bounds back to AST (safe: fresh AST per compile)
            if type_param_bounds:
                func.type_param_bounds.update(type_param_bounds)

            func_defaults = func.defaults if func.defaults else []
            info = FunctionInfo(
                name=func.name,
                params=[
                    ParamInfo(n, t, default_expr=func_defaults[i] if i < len(func_defaults) else None)
                    for i, (n, t) in enumerate(resolved_params)
                ],
                return_type=resolved_return,
                is_noalloc=func.is_noalloc,
                is_readonly=func.is_readonly or func.is_pure,
                is_pure=func.is_pure,
                linkage=_LINKAGE_MAP[func.linkage.name],
                native_name=func.native_name,
                cpp_template=func.cpp_template,
                type_params=func.type_params,
                type_param_bounds=type_param_bounds,
                qualified_name=f"{self.ctx.module_name}.{func.name}",
            )
            # Propagate resolved types back to AST (matches register_record behavior)
            func.params = list(resolved_params)
            func.return_type = resolved_return
            infos.append(info)

        if infos:
            self.ctx.registry.register_function_group(infos[0].name, infos)
            self.ctx.global_ns.bind(NameBinding(
                kind=BindingKind.FUNCTION,
                name=infos[0].name,
                func_infos=infos,
            ))

    def register_globals(self, stmts: list[TpyStmt]) -> None:
        """Register top-level variable declarations in global scope.

        Only registers explicitly typed globals here. Untyped globals are
        fully analyzed in analyze_top_level, which provides proper context
        for list literal type inference.
        """
        for stmt in stmts:
            if isinstance(stmt, TpyVarDecl) and stmt.type:
                actual_type = stmt.type
                # Detect Final[T]: unwrap, record finality, register inner type
                if isinstance(actual_type, FinalType):
                    actual_type = actual_type.wrapped
                    # Final[str] generates constexpr string_view, so the effective
                    # type is StrView (enables coercion when used as std::string)
                    if isinstance(actual_type, StrType):
                        actual_type = STRVIEW
                    stmt.is_final = True
                    self.ctx.final_globals.add(stmt.name)
                self.ctx.global_scope.define(stmt.name, actual_type)
                self.ctx.global_ns.bind_variable(stmt.name, actual_type)
                # Track with line number for order-aware codegen (earliest line wins)
                decl_line = stmt.loc.line if stmt.loc else 0
                if stmt.name not in self.ctx.top_level_decls:
                    self.ctx.top_level_decls[stmt.name] = decl_line
                else:
                    self.ctx.top_level_decls[stmt.name] = min(self.ctx.top_level_decls[stmt.name], decl_line)
