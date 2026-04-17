"""
TurboPython Type Registration

Registers builtin types, records, protocols, and functions.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, NominalType, TypeParamRef, SelfType, RecordInfo, FieldInfo, FunctionInfo, FunctionLinkage, PropertyInfo, is_fn_type, contains_fn_type,
    TypeParamKind, OwnType, VoidType, ParamInfo, MethodSignature, is_protocol_type,
    IMPLICIT_READONLY_METHODS, CONST_PARAMS_METHODS, FinalType, EnumType, IntEnumType, BoolType, make_span,
    FixedIntType, StrType, StrViewType, STRVIEW, INT32, BIGINT, BOOL, UINT64, TupleType, final_type_str_to_strview,
    register_value_type_record, register_send_record, register_sync_record,
    register_return_exception, is_return_exception,
    attach_type_param_bounds,
    has_auto_readonly, has_auto_own,
    qualify_exception_name, ensure_qualified,
    OptionalType, FStrType,
    public_module_name,
)
from ..parse import (
    TpyRecord, TpyProtocol, TpyEnum, TpyFunction, TpyExpr, TpyStmt, TpyVarDecl, RecordLinkage,
    TpyAssign, TpyFieldAccess, TpyName, TpyBinOp, TpyReturn, TpyMethodCall, TpyCall, TpyExprStmt,
    TpyNoneLiteral, TpyStrLiteral,
)
from ..namespace import NameBinding, BindingKind
from .diagnostics import SemanticError
from .operators import DUNDER_CPP_TEMPLATES
from ..macro_api import ClassInfo, expr_to_cpp_default
from ..macro_loader import validate_and_call_macro, call_macro_field_function

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker

from tpyc import modules as builtin_modules
from .. import qnames

def _vararg_span_type(elem_type: 'TpyType') -> NominalType:
    """Build the sema-level Span type for a *args parameter.

    Always Span[readonly[T]] regardless of value/non-value. The distinction
    (std::span<const T> vs tpy::varargs<T>) is handled in codegen only.
    """
    return make_span(elem_type, is_readonly=True)


_LINKAGE_MAP = {
    'DEFAULT': FunctionLinkage.DEFAULT,
    'NATIVE': FunctionLinkage.NATIVE,
    'NATIVE_C': FunctionLinkage.NATIVE_C,
    'EXPORT_C': FunctionLinkage.EXPORT_C,
}


def _contains_self_type(typ: TpyType) -> bool:
    """Check if a type contains SelfType anywhere in its structure."""
    if isinstance(typ, SelfType):
        return True
    return any(_contains_self_type(inner) for inner in typ.inner_types())


def _validate_const_field_default(expr: TpyExpr, loc: object) -> None:
    """Validate that a field default expression is a compile-time constant."""
    if expr_to_cpp_default(expr) is not None:
        return
    # Resolved call from a macro module (e.g. field()) used outside its macro
    if isinstance(expr, (TpyCall, TpyMethodCall)) and getattr(expr, 'resolved_import', None) is not None:
        mod, name = expr.resolved_import
        raise SemanticError(
            f"{mod}.{name}() can only be used in classes decorated with "
            f"a macro from '{mod}'", loc)
    raise SemanticError(
        "Default field value must be a constant expression "
        "(literal, None, or fixed-int constructor like Int32(5))", loc)


def build_record_self_type(record: TpyRecord) -> NominalType:
    """Build a NominalType representing Self for a record, preserving type param kinds."""
    if record.type_params:
        type_args = tuple(
            TypeParamRef(
                name=tp,
                kind=record.type_param_kinds[i] if i < len(record.type_param_kinds) else TypeParamKind.TYPE,
            )
            for i, tp in enumerate(record.type_params)
        )
        return NominalType(record.name, type_args)
    return NominalType(record.name)


class TypeRegistrar:
    """Registers builtin types, records, protocols, and functions."""

    def __init__(self, ctx: SemanticContext, type_ops: TypeOperations, protocols: ProtocolChecker):
        self.ctx = ctx
        self.type_ops = type_ops
        self.protocols = protocols

    def _resolve_type_param_bounds(
        self, raw_bounds: dict[str, TpyType], loc,
    ) -> dict[str, NominalType]:
        """Resolve parsed type parameter bounds, validating each is a protocol."""
        resolved: dict[str, NominalType] = {}
        for param_name, bound_type in raw_bounds.items():
            resolved_bound = self.type_ops.resolve_type(bound_type)
            if not is_protocol_type(resolved_bound):
                raise SemanticError(
                    f"Type parameter bound must be a protocol, got {resolved_bound}",
                    loc,
                )
            resolved[param_name] = resolved_bound
        return resolved

    def register_tpy_star_import(self) -> None:
        """Register all tpy exports for 'from tpy import *'.

        Registers all exported types and functions from the tpy module
        into the global namespace and imported_names tracking.
        """
        # Register tpy types from type factories (Int32, Array, Span, etc.)
        # Compile-time-only types (factory returns non-NominalType, e.g. FStr -> FStrType)
        # are also registered as type aliases so the parser resolves them directly.
        for qname in builtin_modules.get_type_factory_names("tpy"):
            simple_name = qname.split(".")[-1]
            if simple_name not in self.ctx.imported_names:
                self.ctx.imported_names[simple_name] = ("tpy", simple_name)
                self.ctx.global_ns.bind_imported_name(simple_name, "tpy", simple_name)
            self._register_compile_time_type_alias(simple_name, "tpy", simple_name)

        # Register compiled tpy module exports (functions, protocols, type aliases)
        tpy_info = self.ctx.registry.get_module("tpy")
        if tpy_info:
            for name in tpy_info.functions:
                if name not in self.ctx.imported_names:
                    self.ctx.imported_names[name] = ("tpy", name)
                    self.ctx.global_ns.bind_imported_name(name, "tpy", name)
            for name in tpy_info.protocols:
                if name not in self.ctx.imported_names:
                    self.ctx.imported_names[name] = ("tpy", name)
                    self.ctx.global_ns.bind_imported_name(name, "tpy", name)
            if tpy_info.type_aliases:
                for name in tpy_info.type_aliases:
                    if name not in self.ctx.imported_names:
                        self.ctx.imported_names[name] = ("tpy", name)
                        self.ctx.global_ns.bind_imported_name(name, "tpy", name)

    def _register_compile_time_type_alias(self, local_name: str, module: str, original_name: str) -> None:
        """Register a type alias for compile-time-only builtin types.

        Compile-time-only types (like FStr) have a type factory but no C++
        representation. They must be resolved to their singleton at sema time
        so isinstance checks (e.g. isinstance(ptype, FStrType)) work.
        Regular builtin types (Int32, basic_slice, etc.) stay as NominalType
        and use @native for C++ mapping.
        """
        type_obj = builtin_modules.get_builtin_type_obj(f"{module}.{original_name}")
        if type_obj is not None and type_obj.is_compile_time_only():
            self.ctx.registry.register_type_alias(local_name, type_obj)

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
        propagate_clone_names: set[str] = {m.name for m in record.methods if m.is_auto_readonly_mutable_clone or m.is_auto_own_borrowing_clone}
        overload_names: set[str] = {m.name for m in record.methods if m.is_overload_stub}
        # Property getter+setter share a name -- exempt from duplicate check
        property_method_names: set[str] = {m.name for m in record.methods if m.is_property_getter or m.is_property_setter}
        seen_method_names: set[str] = set()
        for method in record.methods:
            if method.name in overload_names or method.name in propagate_clone_names:
                continue
            if method.name in property_method_names:
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
            # Self cannot be used as a field type (infinite size or broken codegen)
            if _contains_self_type(fld.type):
                raise SemanticError(
                    f"Self cannot be used as a field type in '{record.name}'",
                    loc=fld.loc
                )
            if contains_fn_type(fld.type):
                raise SemanticError(
                    "Fn type is only valid in parameter position. "
                    "Use Callable for fields, returns, and locals",
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

        # Apply class macros (e.g. @dataclass)
        self._apply_class_macros(record)

        # Validate field defaults are const (after macros have transformed them)
        for fld in record.fields:
            if fld.default_expr is not None and not fld.is_factory_default:
                _validate_const_field_default(fld.default_expr, fld.loc)

        init_params = []
        if record.init_method:
            init_defaults = record.init_method.defaults
            for i, (pname, ptype) in enumerate(record.init_method.params):
                has_default = bool(init_defaults) and i < len(init_defaults) and init_defaults[i] is not None
                resolved_ptype = self.type_ops.resolve_type(ptype, protocols_only=True)
                init_params.append((pname, resolved_ptype, init_defaults[i] if has_default else None))
        elif is_native and record.fields:
            # Native records without __init__: synthesize init_params from fields.
            # Uses default_value (raw C++ literal string like "0", "nullptr") -- these
            # go directly into struct field declarations, not through kwargs resolution.
            for fld in record.fields:
                init_params.append((fld.name, fld.type, fld.default_value))
        elif record.is_typed_dict and record.fields:
            # total=False: wrap all field types in Optional, set None as default
            if record.is_total_false:
                for fld in record.fields:
                    if not isinstance(fld.type, OptionalType):
                        fld.type = OptionalType(fld.type)
                    if fld.default_expr is None:
                        fld.default_expr = TpyNoneLiteral(loc=fld.loc)
                        fld.default_value = "std::nullopt"
            # Synthesize init_params from fields (all keyword-constructible).
            # total=True: all required (no defaults). total=False: Optional with None default.
            for fld in record.fields:
                default = fld.default_expr if record.is_total_false else None
                init_params.append((fld.name, fld.type, default))

        # Build the Self type for this record (used to substitute SelfType in methods)
        record_self_type = build_record_self_type(record)

        # Pre-compute ids of mutable clones from @auto_readonly (flagged by the parser).
        # Used below to prevent implicit_readonly from clobbering is_readonly=False on these
        # methods, which would break mutable-vs-const overload tie-breaking.
        mutable_clone_ids: set[int] = {id(m) for m in record.methods
                                       if m.is_auto_readonly_mutable_clone or m.is_auto_own_borrowing_clone}

        # Resolve record-level type param bounds early so that
        # attach_type_param_bounds in the method loop below uses resolved versions
        # (with is_protocol=True, _module_qname set from sema registry).
        if record.type_param_bounds:
            resolved_record_bounds: dict[str, NominalType] = {}
            for param_name, bound_type in record.type_param_bounds.items():
                resolved_bound = self.type_ops.resolve_type(bound_type) if not is_protocol_type(bound_type) else bound_type
                if not is_protocol_type(resolved_bound):
                    raise SemanticError(
                        f"Type parameter bound must be a protocol, got {resolved_bound}",
                        record.loc,
                    )
                resolved_record_bounds[param_name] = resolved_bound
            record.type_param_bounds.update(resolved_record_bounds)

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
                        f"Internal error: unresolved 'auto_readonly[T]' in parameter "
                        f"'{pname}' of '{record.name}.{method.name}'",
                        method.loc or record.loc,
                    )
                if not self.type_ops.is_type_param_ref(ptype):
                    self.type_ops.validate_type(ptype, allow_type_param_ref=allow_tpref, loc=record.loc)
            if not self.type_ops.is_type_param_ref(method_return):
                self.type_ops.validate_type(method_return, allow_type_param_ref=allow_tpref, loc=record.loc)
            # Protocol types cannot be used as method return types.
            # Exceptions: @dynamic protocols, and @native/@cpp_template stub methods
            # (C++ handles the actual return type).
            if is_protocol_type(method_return):
                pi = self.ctx.registry.get_protocol(method_return.name)
                is_native_stub = method.is_stub and (method.native_name or method.cpp_template)
                # __iter__ returns Iterator[T] which is a protocol -- allow it since
                # C++ codegen uses auto return type (deduced from body).
                is_iter_method = method.name == "__iter__" and isinstance(method_return, NominalType) and method_return.qualified_name() in (qnames.ITERATOR, qnames.ITERABLE)
                if not (pi and pi.is_dynamic) and not is_native_stub and not is_iter_method and not method.is_generator:
                    raise SemanticError(
                        f"Protocol type '{method_return.name}' cannot be used as a return type in '{record.name}.{method.name}'. "
                        f"Only @dynamic protocols can be used as return types",
                        method.loc or record.loc,
                    )
            # Generator method: extract yield type from Iterator[T] return type
            if method.is_generator:
                if not (is_protocol_type(method_return) and isinstance(method_return, NominalType)
                        and method_return.qualified_name() == "typing.Iterator"):
                    raise SemanticError(
                        f"Generator method must have return type 'Iterator[T]', "
                        f"got '{method_return}'",
                        method.loc or record.loc,
                    )
                if not method_return.type_args:
                    raise SemanticError(
                        f"Iterator must have a type argument, e.g. Iterator[Int32]",
                        method.loc or record.loc,
                    )
                method.generator_yield_type = method_return.type_args[0]
                if record.type_params:
                    raise SemanticError(
                        f"Generator methods on generic classes are not yet supported "
                        f"('{record.name}.{method.name}')",
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
            if has_auto_own(method_return):
                raise SemanticError(
                    f"'auto_own[T]' in return type is only allowed on "
                    f"auto_own[Self] methods ('{record.name}.{method.name}')",
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
                if not (isinstance(method_return, NominalType) and method_return.name == record.name):
                    raise SemanticError(
                        f"'{method.name}' must return self ('{record.name}'), "
                        f"got '{method_return}'",
                        method.loc or record.loc,
                    )
            method_param_infos = [
                ParamInfo(n, t,
                          default_expr=method_defaults[i] if i < len(method_defaults) else None,
                          keyword_only=(method.keyword_only_start is not None
                                        and i >= method.keyword_only_start))
                for i, (n, t) in enumerate(method_params)
            ]
            if method.vararg_name is not None and method.vararg_type is not None:
                va_type = self.type_ops.resolve_type(method.vararg_type)
                method_param_infos.append(
                    ParamInfo(method.vararg_name, _vararg_span_type(va_type),
                              is_variadic=True))
            if method.kwarg_name is not None and method.kwarg_type is not None:
                resolved_kwarg_type = self.type_ops.resolve_type(method.kwarg_type)
                method_param_infos.append(ParamInfo(method.kwarg_name, resolved_kwarg_type))
            func_info = FunctionInfo(
                name=method.name,
                params=method_param_infos,
                return_type=method_return,
                is_readonly=resolved_readonly,
                is_pure=method.is_pure,
                is_inline=method.is_inline,
                is_consuming=method.is_consuming,
                is_method=True,
                is_staticmethod=method.is_staticmethod,
                is_property_getter=method.is_property_getter,
                is_property_setter=method.is_property_setter,
                property_name=method.property_name,
                linkage=method.linkage,
                native_name=method.native_name,
                native_function=method.native_function,
                native_preserves_refs=method.native_preserves_refs,
                cpp_template=method.cpp_template or (DUNDER_CPP_TEMPLATES.get(method.name)
                             if not method.native_function else None),
                type_params=list(method.type_params),
                type_param_bounds=method_type_param_bounds,
                error_return_type=(qualify_exception_name(method.error_return, self.ctx.registry)
                                   if method.error_return else None),
                kwarg_name=method.kwarg_name,
            )
            # @inline: store the body expression for call-site inlining.
            # Body must be a single call statement. Cloned and substituted at call sites.
            if method.is_inline and not method.is_stub:
                non_doc = [s for s in method.body
                           if not (isinstance(s, TpyExprStmt)
                                   and isinstance(s.expr, TpyStrLiteral))]
                if (len(non_doc) == 1
                        and isinstance(non_doc[0], TpyExprStmt)
                        and isinstance(non_doc[0].expr, (TpyCall, TpyMethodCall))):
                    func_info.inline_body = non_doc[0].expr
                else:
                    raise SemanticError(
                        f"@inline method '{record.name}.{method.name}' must have a single "
                        f"call expression as its body.",
                        method.loc or record.loc,
                    )
            # Validate: FStr params require @inline
            elif func_info.has_fstr_param and not method.is_stub:
                raise SemanticError(
                    f"Method '{record.name}.{method.name}' has FStr parameter but is "
                    f"not marked @inline. FStr parameters require @inline.",
                    method.loc or record.loc,
                )
            # Propagate qualified name back to AST so codegen can use it directly.
            # ReturnException validation is deferred to validate_method_error_returns()
            # because the ReturnException marker on exception records is set during
            # validate_record_inheritance, which runs after register_record.
            if method.error_return:
                method.error_return = func_info.error_return_type
            if method.is_property_getter or method.is_property_setter:
                # Property getter/setter share a name -- store both in the list
                methods.setdefault(method.name, []).append(func_info)
            elif method.is_overload_stub:
                # Accumulate overload stubs for this method name
                methods.setdefault(method.name, []).append(func_info)
            elif (method.name in methods
                  and method.name not in overload_names
                  and any(m.is_readonly != func_info.is_readonly
                          or m.is_consuming != func_info.is_consuming
                          for m in methods[method.name])):
                # auto_readonly or auto_own clone: add the complementary overload
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

        # Attach resolved bounds to TypeParamRef instances in RecordInfo method
        # signatures so downstream code (codegen, type_ops) can check bounds.
        if record.type_param_bounds:
            for method_list in methods.values():
                for i, func_info in enumerate(method_list):
                    new_params = [
                        dc_replace(p, type=attach_type_param_bounds(p.type, record.type_param_bounds))
                        for p in func_info.params
                    ]
                    new_return = attach_type_param_bounds(func_info.return_type, record.type_param_bounds)
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
            if not (isinstance(inner, NominalType) and inner.name == record.name):
                raise SemanticError(
                    f"__copy__ must return {record.name}, got {ret}",
                    copy_loc,
                )

        # Build property registry from @property getter/setter methods
        properties: dict[str, PropertyInfo] = {}
        field_names = {f.name for f in record.fields}
        for method_name, overloads in list(methods.items()):
            for fi in overloads:
                if fi.is_property_getter:
                    if method_name in field_names:
                        raise SemanticError(
                            f"Property '{method_name}' conflicts with field of the same name",
                            record.loc,
                        )
                    # Use the first overload for PropertyInfo (for type inference).
                    # Both const and mutable overloads exist from parser cloning.
                    if method_name not in properties:
                        properties[method_name] = PropertyInfo(name=method_name, getter=fi)
                elif fi.is_property_setter:
                    setter_target = fi.property_name
                    if setter_target and setter_target in properties:
                        if properties[setter_target].setter is not None:
                            raise SemanticError(
                                f"Property '{setter_target}' already has a setter defined",
                                record.loc,
                            )
                        properties[setter_target].setter = fi
                    else:
                        raise SemanticError(
                            f"@property setter '{method_name}' has no matching @property getter",
                            record.loc,
                        )
        # Validate setter name doesn't conflict with existing methods
        for prop_name, prop_info in properties.items():
            if prop_info.setter is not None:
                setter_cpp_name = f"set_{prop_name}"
                if setter_cpp_name in methods:
                    raise SemanticError(
                        f"Property setter 'set_{prop_name}' conflicts with method '{setter_cpp_name}'",
                        record.loc,
                    )
        # Remove property methods from methods dict (not callable as obj.method())
        # Prune the mutable getter clone when a single const overload suffices.
        # Only mutable non-value types (records, containers) need dual overloads
        # for T& vs const T& semantics.
        for prop_name, prop_info in properties.items():
            ret = prop_info.getter.return_type
            if ret.is_value_type():
                record.methods = [
                    m for m in record.methods
                    if not (m.is_property_getter and m.name == prop_name
                            and m.is_auto_readonly_mutable_clone)
                ]
            methods.pop(prop_name, None)

        # Set provisional parent from bases (for get_all_fields during macro execution).
        # Full validation (protocols, circular check) is deferred to validate_record_inheritance.
        provisional_parent = None
        for base in record.bases:
            if isinstance(base, NominalType) and self.ctx.registry.get_record(base.name) is not None:
                provisional_parent = base
                break

        info = RecordInfo(
            name=record.name,
            fields=record.fields,
            has_init=record.init_method is not None or record.is_typed_dict,
            init_params=init_params,
            methods=methods,
            properties=properties,
            type_params=record.type_params,
            type_param_kinds=record.type_param_kinds,
            type_param_bounds=record.type_param_bounds,
            parent=provisional_parent,
            implemented_protocols=[],
            native_name=ensure_qualified(record.native_name) if record.native_name else None,
            is_native=is_native,
            is_native_c=is_native_c,
            is_nocopy=record.is_nocopy,
            match_args=(
                record._macro_cls_info.get_match_args()
                if hasattr(record, '_macro_cls_info') and record._macro_cls_info is not None
                   and record._macro_cls_info.get_match_args() is not None
                else None
            ),
            is_frozen=record.is_frozen,
            is_typed_dict=record.is_typed_dict,
            is_total_false=record.is_total_false,
            has_del=record.del_method is not None,
            has_copy=has_copy,
            builtin_type_key=record.builtin_type_key,
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
        - User-defined classes (NominalType with is_record)
        - Builtin types (ArrayType, etc.)
        - Protocols (NominalType with is_protocol)
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None:
            return

        # Native records can only inherit from other native records or protocols.
        # (the C++ inheritance already exists, we just track it for type checking)
        if record_info.is_native and record.bases:
            for base in record.bases:
                if isinstance(base, NominalType):
                    # Protocols are fine (express interface conformance, not C++ inheritance)
                    if self.ctx.registry.get_protocol(base.name):
                        continue
                    base_record = self.ctx.registry.get_record(base.name)
                    if not base_record or not base_record.is_native:
                        raise SemanticError(
                            f"@{record.linkage.value} class '{record.name}' can only inherit from other @native classes",
                            record.loc
                        )

        # Classify bases into parent class vs protocol implementations
        # We do this here (not in register_record) so forward-referenced protocols are recognized
        parent: TpyType | None = None  # Can be NominalType or builtin type
        implemented_protocols: list[NominalType] = []

        for base_type in record.bases:
            # Get the base name to check if it's actually a protocol
            base_name = None
            if isinstance(base_type, NominalType):
                base_name = base_type.name

            # Check if this base is actually a protocol (handles forward references)
            is_protocol_base = False
            if base_name and self.ctx.registry.get_protocol(base_name) is not None:
                is_protocol_base = True

            if is_protocol_base:
                # It's a protocol implementation - set the is_protocol flag correctly
                protocol_type = base_type.with_protocol_flag(True) if isinstance(base_type, NominalType) else base_type
                # Set _module_qname from the protocol's registry entry so that
                # qualified_name() returns the correct module (not just the
                # _protocol_modules fallback which is first-write-wins)
                if isinstance(protocol_type, NominalType) and not protocol_type._module_qname:
                    proto_info = self.ctx.registry.get_protocol(base_name)
                    if proto_info and proto_info.module:
                        # Use public module name for qualified_name() comparisons
                        pub_module = public_module_name(proto_info.module)
                        protocol_type = NominalType(
                            protocol_type.name, protocol_type.type_args, True,
                            f"{pub_module}.{protocol_type.name}",
                            protocol_type.is_dynamic_protocol)
                implemented_protocols.append(protocol_type)
            elif isinstance(base_type, NominalType) and base_type.is_record:
                # It's a class (user-defined or builtin) - check for multiple inheritance
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
        if record_info.parent and isinstance(record_info.parent, NominalType) and record_info.parent.is_user_record:
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

            # Check if record implements all protocol methods.
            # For @builtin_type classes, use the concrete type (e.g. Float32Type)
            # so Self-substitution in protocol signatures matches method param types.
            record_type: TpyType = NominalType(record.name)
            if record_info.builtin_type_key:
                record_type = builtin_modules.get_builtin_type_obj(record_info.builtin_type_key) or record_type
            if not self.protocols.type_conforms_to_protocol(record_type, protocol):
                # Generate helpful error message describing each conformance issue
                issues = self.protocols.get_protocol_conformance_issues(record_type, protocol)
                if issues:
                    detail = "; ".join(issues)
                    raise SemanticError(
                        f"Class '{record.name}' does not conform to protocol "
                        f"'{protocol}': {detail}",
                        record.loc
                    )
                # Fallback when the detailed scan didn't surface a reason
                # (e.g. marker-protocol / extends gaps checked elsewhere).
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

        # ReturnException marker: register exception type as return-only
        for protocol in record_info.implemented_protocols:
            if protocol.qualified_name() == qnames.RETURN_EXCEPTION:
                register_return_exception(record.name)
                break

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

    def validate_method_error_returns(self, record: TpyRecord) -> None:
        """Validate @error_return(E) on methods references a ReturnException type.

        Deferred from register_record because ReturnException markers are set
        during validate_record_inheritance, which runs after register_record.
        """
        for method in record.methods:
            if method.error_return and not is_return_exception(method.error_return):
                raise SemanticError(
                    f"'{method.error_return}' is not a ReturnException type; "
                    f"@error_return requires a ReturnException exception",
                    method.loc
                )

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

        # Methods explicitly marked as hiding parent versions
        hides = {m.name for m in record.methods if m.hides_parent}

        # Check each method defined in this class
        for method_name in record_info.methods:
            if method_name in ("__init__", "__del__"):
                continue  # constructor/destructor hiding is expected
            if skip_names and method_name in skip_names:
                continue  # @override methods are checked (with better messages) separately
            if method_name in hides:
                continue

            # Check if any ancestor has this method
            ancestor_with_method = self._find_ancestor_with_method(parent_info, method_name)
            if ancestor_with_method:
                self.ctx.warning(
                    f"Method '{record.name}.{method_name}' hides "
                    f"'{ancestor_with_method}.{method_name}' -- any "
                    f"'{ancestor_with_method}' reference will call "
                    f"'{ancestor_with_method}.{method_name}', not "
                    f"'{record.name}.{method_name}' (differs from Python's dynamic dispatch); "
                    f"make '{ancestor_with_method}' a @dynamic protocol for runtime dispatch",
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

    def _apply_class_macros(self, record: TpyRecord) -> None:
        """Apply class macros (from pending_macros) to a record before registration."""
        if not record.pending_macros:
            return
        registry = self.ctx.macro_registry
        for qname, kwargs in record.pending_macros:
            parts = qname.rsplit(".", 1)
            if len(parts) != 2:
                raise SemanticError(f"Invalid macro name '{qname}'", record.loc)
            mod_name, func_name = parts
            macro_fn = registry.get_macro(mod_name, func_name) if registry else None
            if macro_fn is None:
                raise SemanticError(f"Unknown macro '{qname}'", record.loc)
            cls_info = ClassInfo(record, self.ctx)
            # Call macro-module functions in field defaults (e.g. field() -> Field)
            for fld in cls_info.fields:
                if fld.default_expr is not None and isinstance(fld.default_expr, (TpyCall, TpyMethodCall)):
                    result = call_macro_field_function(
                        registry, fld.default_expr, fld.loc)
                    if result is not None:
                        fld.default_obj = result
            validate_and_call_macro(macro_fn, cls_info, kwargs, qname, record.loc)
            cls_info.apply_to_record()
            record._macro_cls_info = cls_info  # type: ignore[attr-defined]

    def _is_inheritable_builtin(self, typ: TpyType) -> bool:
        """Check if a type is a builtin type that can be inherited from."""
        qname = typ.qualified_name()
        if qname is None:
            return False
        return self.ctx.registry.get_builtin_record(qname) is not None

    def register_protocol(self, protocol: TpyProtocol) -> None:
        """Register a protocol type (without validating parents yet)."""
        from ..typesys import MethodSignature, ProtocolInfo
        # Resolve implicit readonly and cross-module protocol flags on method
        # signatures. resolve_type backfills is_protocol / _module_qname on
        # NominalType references to other protocols (e.g. Iterator[T] in
        # Iterable[T].__iter__) so that later signature matches can detect
        # protocol returns.
        resolved_methods = []
        for msig in protocol.methods:
            resolved_readonly = msig.is_readonly or (
                msig.name in IMPLICIT_READONLY_METHODS and not msig.readonly_opt_out
            )
            resolved_params = [
                (n, self.type_ops.resolve_type(t, protocols_only=True))
                for n, t in msig.params
            ]
            resolved_return = self.type_ops.resolve_type(msig.return_type, protocols_only=True)
            resolved_methods.append(MethodSignature(
                name=msig.name,
                params=resolved_params,
                return_type=resolved_return,
                is_readonly=resolved_readonly,
            ))
        info = ProtocolInfo(
            name=protocol.name,
            methods=resolved_methods,
            fields=protocol.fields,
            type_params=protocol.type_params,
            parent_protocols=protocol.parent_protocols,
            cpp_concept=protocol.cpp_concept,
            is_marker=protocol.cpp_concept is not None and len(resolved_methods) == 0,
            is_dynamic=protocol.is_dynamic,
            module=public_module_name(self.ctx.module_name, self.ctx.module_cpp_namespace),
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
            try:
                resolved_ptype = self.type_ops.resolve_type(ptype)
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

        # Resolve *args parameter: append as Span[readonly[T]]
        resolved_vararg_type = None
        if func.vararg_name is not None:
            resolved_vararg_type = self.type_ops.resolve_type(func.vararg_type)
            self.type_ops.validate_type(resolved_vararg_type, allow_type_param_ref=is_generic)

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
        # Exception: generator functions return Iterator[T] (codegen emits concrete struct).
        # Exception: @native/@cpp_template stubs (C++ handles the actual return type).
        if is_protocol_type(resolved_return):
            if func.is_generator:
                # Validate that it's Iterator[T]
                if resolved_return.qualified_name() != "typing.Iterator":
                    raise SemanticError(
                        f"Generator function must have return type 'Iterator[T]', "
                        f"got '{resolved_return}'",
                        func.loc
                    )
                if not resolved_return.type_args:
                    raise SemanticError(
                        f"Iterator must have a type argument, e.g. Iterator[Int32]",
                        func.loc
                    )
                func.generator_yield_type = resolved_return.type_args[0]
            else:
                pi = self.ctx.registry.get_protocol(resolved_return.name)
                is_native_stub = func.is_stub and (func.native_name or func.cpp_template)
                if not (pi and pi.is_dynamic) and not is_native_stub:
                    raise SemanticError(
                        f"Protocol type '{resolved_return.name}' cannot be used as a return type. "
                        f"Only @dynamic protocols can be used as return types",
                        func.loc
                    )

        # Generator without Iterator[T] return type
        if func.is_generator and not is_protocol_type(resolved_return):
            raise SemanticError(
                f"Generator function must have return type 'Iterator[T]', "
                f"got '{resolved_return}'",
                func.loc
            )

        if contains_fn_type(resolved_return):
            raise SemanticError(
                "Fn type is only valid in parameter position. "
                "Use Callable for fields, returns, and locals",
                func.loc
            )

        # Fn is valid as a bare param type but not nested inside Optional/Union
        for pname, ptype in func.params:
            if not is_fn_type(ptype) and contains_fn_type(ptype):
                raise SemanticError(
                    f"Fn type cannot be nested inside another type (Optional, Union, list, etc.). "
                    f"Use Callable for parameter '{pname}' instead",
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
        param_infos = []
        kw_start = func.keyword_only_start
        for i, (n, t) in enumerate(resolved_params):
            is_kwonly = kw_start is not None and i >= kw_start
            # Insert *args param before keyword-only params
            if is_kwonly and func.vararg_name is not None and resolved_vararg_type is not None and i == kw_start:
                span_type = _vararg_span_type(resolved_vararg_type)
                param_infos.append(ParamInfo(func.vararg_name, span_type, is_variadic=True))
            param_infos.append(ParamInfo(n, t,
                      default_expr=func_defaults[i] if i < len(func_defaults) else None,
                      keyword_only=is_kwonly))
        # *args with no keyword-only params: append at end
        if func.vararg_name is not None and resolved_vararg_type is not None and (kw_start is None or kw_start >= len(resolved_params)):
            span_type = make_span(resolved_vararg_type, is_readonly=True)
            param_infos.append(ParamInfo(func.vararg_name, span_type, is_variadic=True))

        # **kwargs: Unpack[TypedDict] -- append as TypedDict param at end
        resolved_kwarg_type = None
        if func.kwarg_name is not None and func.kwarg_type is not None:
            resolved_kwarg_type = self.type_ops.resolve_type(func.kwarg_type)
            # Validate no field name conflicts with regular params
            if isinstance(resolved_kwarg_type, NominalType):
                td_record = self.ctx.registry.get_record(resolved_kwarg_type.name)
                if td_record is not None:
                    param_names = {n for n, _ in resolved_params}
                    for fld in td_record.fields:
                        if fld.name in param_names:
                            raise SemanticError(
                                f"TypedDict field '{fld.name}' conflicts with "
                                f"parameter '{fld.name}' on '{func.name}'",
                                func.loc,
                            )
            param_infos.append(ParamInfo(func.kwarg_name, resolved_kwarg_type))

        info = FunctionInfo(
            name=func.name,
            params=param_infos,
            return_type=resolved_return,
            is_noalloc=func.is_noalloc,
            is_readonly=func.is_readonly or func.is_pure,
            is_pure=func.is_pure,
            is_inline=func.is_inline,
            linkage=fi_linkage,
            native_name=func.native_name,
            cpp_template=func.cpp_template,
            value_ptr_coercion=func.value_ptr_coercion,
            type_params=func.type_params,
            type_param_bounds=type_param_bounds,
            type_param_defaults=func.type_param_defaults,
            is_builtin_function=bool(func.builtin_function_key),
            special_handling=bool(func.builtin_function_key),
            error_return_type=(qualify_exception_name(func.error_return, self.ctx.registry)
                               if func.error_return else None),
            qualified_name=(func.builtin_function_key
                            if func.builtin_function_key
                            else f"{self.ctx.module_name}.{func.name}"),
            kwarg_name=func.kwarg_name,
        )
        # @inline: store body for call-site inlining
        if func.is_inline and not func.is_stub:
            non_doc = [s for s in func.body
                       if not (isinstance(s, TpyExprStmt)
                               and isinstance(s.expr, TpyStrLiteral))]
            if (len(non_doc) == 1
                    and isinstance(non_doc[0], TpyExprStmt)
                    and isinstance(non_doc[0].expr, (TpyCall, TpyMethodCall))):
                info.inline_body = non_doc[0].expr
            else:
                raise SemanticError(
                    f"@inline function '{func.name}' must have a single "
                    f"call expression as its body.",
                    func.loc,
                )
        elif any(isinstance(p.type, FStrType) for p in info.params) and not func.is_stub:
            raise SemanticError(
                f"Function '{func.name}' has FStr parameter but is not marked @inline. "
                f"FStr parameters require @inline.",
                func.loc,
            )

        # Propagate qualified name back to AST so codegen can use it directly
        if func.error_return:
            orig_name = func.error_return
            func.error_return = info.error_return_type
            # @error_return(E) requires E to be a ReturnException type
            if not is_return_exception(info.error_return_type):
                raise SemanticError(
                    f"'{orig_name}' is not a ReturnException type; "
                    f"@error_return requires a ReturnException exception",
                    func.loc
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
            stub_param_infos = [
                ParamInfo(n, t,
                          default_expr=func_defaults[i] if i < len(func_defaults) else None,
                          keyword_only=(func.keyword_only_start is not None
                                        and i >= func.keyword_only_start))
                for i, (n, t) in enumerate(resolved_params)
            ]
            if func.vararg_name is not None and func.vararg_type is not None:
                va_type = self.type_ops.resolve_type(func.vararg_type)
                stub_param_infos.append(
                    ParamInfo(func.vararg_name, _vararg_span_type(va_type),
                              is_variadic=True))
            info = FunctionInfo(
                name=func.name,
                params=stub_param_infos,
                return_type=resolved_return,
                is_noalloc=func.is_noalloc,
                is_readonly=func.is_readonly or func.is_pure,
                is_pure=func.is_pure,
                linkage=_LINKAGE_MAP[func.linkage.name],
                native_name=func.native_name,
                cpp_template=func.cpp_template,
                type_params=func.type_params,
                type_param_bounds=type_param_bounds,
                type_param_defaults=func.type_param_defaults,
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
                    actual_type = final_type_str_to_strview(actual_type.wrapped)
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
