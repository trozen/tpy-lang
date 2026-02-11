"""
TurboPython Call Analysis

Function and constructor call analysis.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, OwnType, ListType, PendingListType, IntLiteralType,
    StrType, CharType, ListLiteralInfo, FunctionInfo, RecordInfo,
    VOID, BIGINT, is_protocol_type,
)
from ..parse import TpyCall, TpyStrLiteral, TpyName
from ..namespace import BindingKind
from ..coercions import CoercionContext
from .diagnostics import SemanticError
from .overloads import type_matches_numeric, resolve_overload

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from .list_literals import ListLiteralTracker
    from .expressions import ExpressionAnalyzer

from tpyc import modules as builtin_modules


class CallAnalyzer:
    """Function and constructor call analysis."""

    def __init__(
        self,
        ctx: SemanticContext,
        type_ops: TypeOperations,
        protocols: ProtocolChecker,
        compat: TypeCompatibility,
        list_tracker: ListLiteralTracker,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.protocols = protocols
        self.compat = compat
        self.list_tracker = list_tracker
        # Set via set_cross_deps() to break circular dependency
        self.expr: ExpressionAnalyzer | None = None

    def set_cross_deps(self, expr: ExpressionAnalyzer) -> None:
        """Wire circular dependencies (must be called before analyze_call)."""
        self.expr = expr

    def analyze_call(self, expr: TpyCall) -> TpyType:
        """Analyze a function or constructor call."""
        # Handle super() call
        if expr.func == "super":
            from .methods import MethodAnalyzer
            return MethodAnalyzer._analyze_super_call_static(self.ctx, expr)

        # Generic type instantiation (e.g., Container[T, N](), StaticList[Int32, 8]())
        # Only if it's actually a type - for generic functions with uppercase names,
        # call_type may be set but we should use type_args instead
        if expr.call_type is not None:
            # Check if this is a user-defined generic function
            is_known_function = self.ctx.registry.get_function(expr.func) is not None
            if not is_known_function:
                # Check if it's a user-defined record - use _analyze_record_constructor for bound validation
                record = self.ctx.registry.get_record(expr.func)
                if record:
                    return self._analyze_record_constructor(expr, record)
                # It's a builtin type instantiation - use call_type directly
                for arg in expr.args:
                    self.expr.analyze_expr(arg)
                return expr.call_type
            # Otherwise fall through to function handling (type_args will be used)

        # Check registry for built-in functions (global builtins like chr)
        if overloads := self.ctx.registry.get_builtin_function_overloads(expr.func):
            if not overloads[0].special_handling:
                return self._analyze_builtin_function_overloads(expr, overloads)

        # Track if we found an imported generic type (allows fallthrough to generic handling)
        # Stores the original name (not alias) for lookup_generic_type
        imported_generic_name: str | None = None

        # Use namespace for unified lookup - handles shadowing automatically
        if self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(expr.func)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    raise self.ctx.error(f"'{expr.func}' is not callable", expr)
                elif binding.kind == BindingKind.FUNCTION:
                    return self._analyze_user_function_call(expr, binding.func_info)
                elif binding.kind == BindingKind.RECORD:
                    return self._analyze_record_constructor(expr, binding.record_info)
                elif binding.kind == BindingKind.IMPORTED_NAME:
                    module_name, func_name = binding.import_source
                    # Special handling for copy() from tpy - truly generic function
                    if module_name == "tpy" and func_name == "copy":
                        return self._analyze_tpy_copy(expr)
                    # Special handling for builtins with custom sema
                    if module_name == "builtins":
                        if func_name == "print":
                            for arg in expr.args:
                                self.expr.analyze_expr(arg)
                            return VOID
                        elif func_name == "range":
                            raise SemanticError(
                                "range() can only be used in 'for i in range(...)' loops",
                                expr.loc
                            )
                        elif func_name in ("enumerate", "zip"):
                            raise SemanticError(f"{func_name}() is not yet implemented", expr.loc)
                    # Check for user module function (registered via _register_user_module_import)
                    if func_info := self.ctx.registry.get_function(expr.func):
                        return self._analyze_user_function_call(expr, func_info)
                    # Check for user module record (registered via _register_user_module_import)
                    if record_info := self.ctx.registry.get_record(expr.func):
                        return self._analyze_record_constructor(expr, record_info)
                    # Check for module function (e.g., math.sqrt)
                    from .registration import TypeRegistrar
                    if overloads := self._get_module_function_overloads(module_name, func_name):
                        return self._analyze_builtin_function_overloads(expr, overloads)
                    # Check for type constructor (e.g., Int32 from tpy, int from builtins)
                    qname = f"{module_name}.{func_name}"
                    if record_info := self.ctx.registry.get_builtin_record(qname):
                        if record_info.constructors and not record_info.type_params:
                            return self._check_builtin_constructor(expr, record_info)
                    # Generic types (StaticList, Array, list) - mark as found and fall through
                    if builtin_modules.lookup_generic_type(func_name):
                        imported_generic_name = func_name
                    else:
                        raise SemanticError(f"Unknown function '{func_name}' in module '{module_name}'", expr.loc)
                elif binding.kind == BindingKind.MODULE:
                    raise SemanticError(f"Cannot call module '{expr.func}' directly; use module.function()", expr.loc)
                elif binding.kind == BindingKind.BUILTIN:
                    raise SemanticError(f"'{expr.func}' is not callable", expr.loc)

        # Check if it's a tpy type that requires explicit import
        # Only check if we didn't find it in namespace (i.e., not imported)
        # Python builtins (int, str, list) are in builtins_ns and would be found above
        if imported_generic_name is None:
            tpy_qname = f"tpy.{expr.func}"
            if record_info := self.ctx.registry.get_builtin_record(tpy_qname):
                if record_info.constructors and not record_info.type_params:
                    raise self.ctx.error(
                        f"'{expr.func}' is not defined. Did you mean: from tpy import {expr.func}",
                        expr
                    )
            # Also check generic tpy types (StaticList, Array, Span)
            if lookup := builtin_modules.lookup_generic_type(expr.func):
                if lookup.qualified_name.startswith("tpy."):
                    raise self.ctx.error(
                        f"'{expr.func}' is not defined. Did you mean: from tpy import {expr.func}",
                        expr
                    )

        # Fallback: Check if it's a record constructor
        record = self.ctx.registry.get_record(expr.func)
        if record:
            # Analyze arguments
            for arg in expr.args:
                self.expr.analyze_expr(arg)
            return NamedType(expr.func)

        # Fallback: Check if it's a function call
        func = self.ctx.registry.get_function(expr.func)
        if func:
            return self._analyze_legacy_function_call(expr, func)

        # Generic type constructor without context for type inference
        # Only proceed if we found an imported generic type in namespace
        if imported_generic_name and (lookup := builtin_modules.lookup_generic_type(imported_generic_name)):
            type_def = lookup.type_def
            params = ", ".join(type_def.type_params)

            # Check for constructors that can infer type from arguments
            if expr.args and type_def.constructors:
                arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
                for ctor in type_def.constructors:
                    if len(ctor.params) != len(arg_types):
                        continue
                    # Try to match and infer type parameters
                    inferred_params = self.type_ops.match_generic_constructor(ctor.params, arg_types)
                    if inferred_params is not None:
                        # Use type_factory to create the result type
                        elem_type = inferred_params.get("T")
                        if elem_type and type_def.type_factory:
                            # Resolve IntLiteralType to BigInt (Python semantics)
                            if isinstance(elem_type, IntLiteralType):
                                elem_type = BIGINT
                            result_type = type_def.type_factory(elem_type)
                            expr.call_type = result_type
                            return result_type

            if expr.args:
                raise self.ctx.error(
                    f"Cannot infer element type for {expr.func}() from these arguments; "
                    f"use {expr.func}[{params}]() or provide a type annotation",
                    expr
                )
            raise self.ctx.error(
                f"Cannot infer element type for {expr.func}(); "
                f"use {expr.func}[{params}](), provide a type annotation, or pass an iterable",
                expr
            )

        raise self.ctx.error(f"Unknown function or type: '{expr.func}'", expr)

    def _get_module_function_overloads(self, module_name: str, func_name: str) -> list[FunctionInfo] | None:
        """Look up function overloads in a module using the unified registry."""
        module_info = self.ctx.registry.get_module(module_name)
        if module_info and func_name in module_info.functions:
            return module_info.functions[func_name]
        return None

    def _analyze_tpy_copy(self, expr: TpyCall) -> TpyType:
        """Analyze a call to tpy.copy() - explicit copy for ownership transfer.

        copy() is truly generic (works with any type T, returns Own[T]).
        This is handled specially because the module system doesn't support
        truly generic functions yet.
        """
        if len(expr.args) != 1:
            raise self.ctx.error("copy() takes exactly 1 argument", expr)
        arg_type = self.expr.analyze_expr(expr.args[0])
        # Unwrap OwnType if already wrapped
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped
        return OwnType(arg_type)

    def _check_builtin_constructor(self, expr: TpyCall, record_info: RecordInfo) -> TpyType:
        """Check a builtin type constructor call using unified RecordInfo.constructors."""
        type_name = expr.func
        arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]

        # Find a matching constructor overload
        for ctor in record_info.constructors:
            if len(ctor.params) != len(arg_types):
                continue
            if all(type_matches_numeric(arg_type, ptype)
                   for (pname, ptype), arg_type in zip(ctor.params, arg_types)):
                expr.resolved_function_info = ctor
                return ctor.return_type

        # No matching overload found
        if not record_info.constructors:
            raise self.ctx.error(f"{type_name}() is not callable", expr)
        elif len(arg_types) == 0:
            raise self.ctx.error(f"{type_name}() requires an argument", expr)
        elif len(arg_types) == 1:
            raise self.ctx.error(f"{type_name}() cannot convert {arg_types[0]}", expr)
        else:
            raise self.ctx.error(f"{type_name}() takes at most 1 argument, got {len(arg_types)}", expr)

    def _analyze_builtin_function_overloads(self, expr: TpyCall, overloads: list[FunctionInfo]) -> TpyType:
        """Type-check a call to a builtin function using unified FunctionInfo overloads.

        Uses two-pass overload resolution: prefer exact type matches over coercion matches.
        """
        arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
        protocol_checker = self.protocols.type_conforms_to_protocol

        matched = resolve_overload(overloads, arg_types, protocol_checker)
        if matched is not None:
            expr.resolved_function_info = matched
            # Apply coercions to arguments where needed
            for i, (arg, arg_t, (pname, ptype)) in enumerate(zip(expr.args, arg_types, matched.params)):
                if arg_t != ptype:
                    expr.args[i] = self.compat.coerce_expr(arg, arg_t, ptype,
                                                            f"argument '{pname}'",
                                                            coercion_ctx=CoercionContext.ARG)
            return matched.return_type

        # No matching overload found - build error message
        arg_type_strs = ", ".join(str(t) for t in arg_types)
        raise self.ctx.error(f"No matching overload for {expr.func}({arg_type_strs})", expr)

    def _analyze_user_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a call to a user-defined function."""
        # Handle generic functions
        if func.is_generic():
            return self._analyze_generic_function_call(expr, func)

        expr.resolved_function_info = func
        if len(expr.args) != len(func.params):
            raise self.ctx.error(f"Function '{expr.func}' expects {len(func.params)} arguments, got {len(expr.args)}", expr)
        for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
            # Handle list() constructor - infer type from parameter
            arg_type = self.expr.analyze_expr_with_hint(arg, ptype)

            # Check for Own[T] passed directly to object type parameter
            if isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType) and not ptype.is_value_type():
                if isinstance(arg, TpyName):
                    hint = f"Declare the variable as '{arg_type.wrapped}' instead of 'Own[{arg_type.wrapped}]'"
                else:
                    hint = "Assign to a variable first: x = func(); other_func(x)"
                raise self.ctx.error(
                    f"Cannot pass Own[{arg_type.wrapped}] directly to parameter '{pname}' "
                    f"(object types are passed by reference). {hint}",
                    arg
                )

            # Check for T passed to Own[T] parameter - would be implicit copy
            if isinstance(ptype, OwnType) and not isinstance(arg_type, OwnType) and not arg_type.is_value_type():
                raise self.ctx.error(
                    f"Cannot pass '{arg_type}' to parameter '{pname}: Own[{ptype.wrapped}]' "
                    f"(would be implicit copy)",
                    arg
                )

            # Special case: single-char string literal can be passed as Char
            if not (isinstance(ptype, CharType) and isinstance(arg_type, StrType) and
                    isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                coerced_arg = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                       coercion_ctx=CoercionContext.ARG)
                expr.args[i] = coerced_arg

            # Track parameter context for list inference
            if isinstance(arg_type, PendingListType):
                self.list_tracker.mark_list_param_context(arg, ptype)

        return func.return_type

    def _analyze_generic_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a call to a generic function."""
        # Check for invalid type arguments (e.g., first[123](x) or first[var](x))
        if expr.type_args_parse_error:
            raise self.ctx.error(expr.type_args_parse_error, expr)

        # Check argument count first
        if len(expr.args) != len(func.params):
            raise self.ctx.error(
                f"Function '{expr.func}' expects {len(func.params)} arguments, got {len(expr.args)}",
                expr
            )

        # Get type substitution from explicit args or inference
        if expr.type_args:
            # Explicit: first[Int32](items)
            if len(expr.type_args) != len(func.type_params):
                raise self.ctx.error(
                    f"Function '{expr.func}' expects {len(func.type_params)} type arguments, "
                    f"got {len(expr.type_args)}",
                    expr
                )
            # Validate each explicit type argument
            for i, type_arg in enumerate(expr.type_args):
                # Protocol types cannot be used as type arguments
                if is_protocol_type(type_arg):
                    raise self.ctx.error(
                        f"Protocol type '{type_arg.name}' cannot be used as a type argument. "
                        f"Protocols are only valid for function parameters",
                        expr
                    )
                # Check for unknown record types (no forward references allowed at call sites)
                if isinstance(type_arg, NamedType) and type_arg.is_record and not type_arg.type_args:
                    if self.ctx.registry.get_record(type_arg.name) is None:
                        raise self.ctx.error(f"Unknown type: {type_arg.name}", expr)
                # Validate the type (checks for missing generic args, etc.)
                self.type_ops.validate_type(type_arg, loc=expr.loc)
            type_subst = dict(zip(func.type_params, expr.type_args))
            # Validate type parameter bounds
            for param_name, type_arg in type_subst.items():
                if param_name in func.type_param_bounds:
                    bound = func.type_param_bounds[param_name]
                    if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                        raise self.ctx.error(
                            f"Type argument '{type_arg}' does not satisfy bound '{bound}' "
                            f"for type parameter '{param_name}' of '{func.name}'",
                            expr
                        )
        else:
            # Infer from arguments
            arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
            type_subst = self.type_ops.infer_type_params_for_function(
                func, arg_types, self.protocols.type_conforms_to_protocol
            )
            if type_subst is None:
                raise self.ctx.error(
                    f"Cannot infer type arguments for '{func.name}'. "
                    f"Specify explicitly: {func.name}[{', '.join(func.type_params)}](...)",
                    expr
                )

        # Store inferred type args for codegen
        expr.inferred_type_args = tuple(type_subst[p] for p in func.type_params)

        # Resolve and check parameters
        resolved_func = self.type_ops.substitute_method_type_params(func, type_subst)
        expr.resolved_function_info = resolved_func
        for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
            resolved_ptype = self.type_ops.substitute_type_params(ptype, type_subst)
            arg_type = self.expr.analyze_expr_with_hint(arg, resolved_ptype)

            # Check for Own[T] passed directly to object type parameter
            if isinstance(arg_type, OwnType) and not isinstance(resolved_ptype, OwnType) and not resolved_ptype.is_value_type():
                if isinstance(arg, TpyName):
                    hint = f"Declare the variable as '{arg_type.wrapped}' instead of 'Own[{arg_type.wrapped}]'"
                else:
                    hint = "Assign to a variable first: x = func(); other_func(x)"
                raise self.ctx.error(
                    f"Cannot pass Own[{arg_type.wrapped}] directly to parameter '{pname}' "
                    f"(object types are passed by reference). {hint}",
                    arg
                )

            # Check for T passed to Own[T] parameter - would be implicit copy
            if isinstance(resolved_ptype, OwnType) and not isinstance(arg_type, OwnType) and not arg_type.is_value_type():
                raise self.ctx.error(
                    f"Cannot pass '{arg_type}' to parameter '{pname}: Own[{resolved_ptype.wrapped}]' "
                    f"(would be implicit copy)",
                    arg
                )

            # Special case: single-char string literal can be passed as Char
            if not (isinstance(resolved_ptype, CharType) and isinstance(arg_type, StrType) and
                    isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                coerced_arg = self.compat.coerce_expr(arg, arg_type, resolved_ptype, f"argument '{pname}'",
                                                       coercion_ctx=CoercionContext.ARG)
                expr.args[i] = coerced_arg

            # Track parameter context for list inference
            if isinstance(arg_type, PendingListType):
                self.list_tracker.mark_list_param_context(arg, resolved_ptype)

        # Resolve return type
        return self.type_ops.substitute_type_params(func.return_type, type_subst)

    def _analyze_record_constructor(self, expr: TpyCall, record: RecordInfo) -> TpyType:
        """Analyze a call to a record constructor."""
        # Check if this is a generic record instantiation (e.g., Stack[Int32]())
        if expr.call_type is not None and isinstance(expr.call_type, NamedType) and expr.call_type.is_record:
            # Validate type arguments
            if record.is_generic():
                if not expr.call_type.type_args:
                    raise self.ctx.error(
                        f"Generic record '{record.name}' requires type arguments: "
                        f"{record.name}[{', '.join(record.type_params)}]",
                        expr
                    )
                if len(expr.call_type.type_args) != len(record.type_params):
                    raise self.ctx.error(
                        f"Record '{record.name}' expects {len(record.type_params)} type arguments, "
                        f"got {len(expr.call_type.type_args)}",
                        expr
                    )
                # Validate type parameter bounds
                for param_name, type_arg in zip(record.type_params, expr.call_type.type_args):
                    if param_name in record.type_param_bounds:
                        bound = record.type_param_bounds[param_name]
                        if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                            raise self.ctx.error(
                                f"Type argument '{type_arg}' does not satisfy bound '{bound}' "
                                f"for type parameter '{param_name}' of '{record.name}'",
                                expr
                            )
            # Analyze and type-check constructor arguments with type substitution
            type_subst = self.type_ops.build_type_substitution(expr.call_type)
            if record.has_init:
                # Type-check __init__ parameters
                if len(expr.args) != len(record.init_params):
                    raise self.ctx.error(
                        f"{record.name}() takes {len(record.init_params)} argument(s), got {len(expr.args)}",
                        expr
                    )
                for i, (arg, (pname, ptype, _)) in enumerate(zip(expr.args, record.init_params)):
                    arg_type = self.expr.analyze_expr(arg)
                    resolved_ptype = self.type_ops.substitute_type_params(ptype, type_subst) if type_subst else ptype
                    expr.args[i] = self.compat.coerce_expr(arg, arg_type, resolved_ptype, f"argument '{pname}'",
                                                           coercion_ctx=CoercionContext.ARG)
            else:
                for arg in expr.args:
                    self.expr.analyze_expr(arg)
            return expr.call_type
        # Generic record without explicit type args - try type inference
        if record.is_generic():
            if record.has_init:
                arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
                inferred = self.type_ops.infer_type_params_for_record(record, arg_types)
                if inferred:
                    # Resolve pending types for codegen (Python semantics)
                    for k, v in list(inferred.items()):
                        if isinstance(v, IntLiteralType):
                            inferred[k] = BIGINT
                        elif isinstance(v, PendingListType):
                            # Resolve PendingListType to ListType
                            elem_type = v.element_type
                            if isinstance(elem_type, IntLiteralType):
                                elem_type = BIGINT
                            inferred[k] = ListType(elem_type)
                    # Validate type parameter bounds
                    for param_name, type_arg in inferred.items():
                        if param_name in record.type_param_bounds:
                            bound = record.type_param_bounds[param_name]
                            if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                                raise self.ctx.error(
                                    f"Inferred type '{type_arg}' does not satisfy bound '{bound}' "
                                    f"for type parameter '{param_name}' of '{record.name}'",
                                    expr
                                )
                    type_args = tuple(inferred[p] for p in record.type_params)
                    # Use expr.func (local name) not record.name (original) for alias support
                    inferred_type = NamedType(expr.func, type_args)
                    expr.call_type = inferred_type
                    # Coerce arguments with substitution
                    type_subst = inferred
                    for i, (arg, (pname, ptype, _)) in enumerate(zip(expr.args, record.init_params)):
                        resolved_ptype = self.type_ops.substitute_type_params(ptype, type_subst)
                        expr.args[i] = self.compat.coerce_expr(
                            arg, arg_types[i], resolved_ptype,
                            f"argument '{pname}'", coercion_ctx=CoercionContext.ARG
                        )
                    return inferred_type
            # Inference failed - require explicit type args
            raise self.ctx.error(
                f"Cannot infer type arguments for '{record.name}'. "
                f"Please specify explicitly: {record.name}[{', '.join(record.type_params)}](...)",
                expr
            )
        # Non-generic record
        if record.has_init:
            # Type-check __init__ parameters
            if len(expr.args) != len(record.init_params):
                raise self.ctx.error(
                    f"{record.name}() takes {len(record.init_params)} argument(s), got {len(expr.args)}",
                    expr
                )
            for i, (arg, (pname, ptype, _)) in enumerate(zip(expr.args, record.init_params)):
                arg_type = self.expr.analyze_expr(arg)
                expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                        coercion_ctx=CoercionContext.ARG)
        else:
            for arg in expr.args:
                self.expr.analyze_expr(arg)
        # Use expr.func (local name) not record.name (original) for alias support
        return NamedType(expr.func)

    def _analyze_legacy_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a legacy function call (fallback path)."""
        expr.resolved_function_info = func
        if len(expr.args) != len(func.params):
            raise self.ctx.error(f"Function '{expr.func}' expects {len(func.params)} arguments, got {len(expr.args)}", expr)
        for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
            # Handle list() constructor - infer type from parameter
            arg_type = self.expr.analyze_expr_with_hint(arg, ptype)

            # Check for Own[T] passed directly to object type parameter
            if isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType) and not ptype.is_value_type():
                if isinstance(arg, TpyName):
                    hint = f"Declare the variable as '{arg_type.wrapped}' instead of 'Own[{arg_type.wrapped}]'"
                else:
                    hint = "Assign to a variable first: x = func(); other_func(x)"
                raise self.ctx.error(
                    f"Cannot pass Own[{arg_type.wrapped}] directly to parameter '{pname}' "
                    f"(object types are passed by reference). {hint}",
                    arg
                )

            # Check for T passed to Own[T] parameter - would be implicit copy
            if isinstance(ptype, OwnType) and not isinstance(arg_type, OwnType) and not arg_type.is_value_type():
                raise self.ctx.error(
                    f"Cannot pass '{arg_type}' to parameter '{pname}: Own[{ptype.wrapped}]' "
                    f"(would be implicit copy)",
                    arg
                )

            # Special case: single-char string literal can be passed as Char
            if not (isinstance(ptype, CharType) and isinstance(arg_type, StrType) and
                    isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                coerced_arg = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                       coercion_ctx=CoercionContext.ARG)
                expr.args[i] = coerced_arg

            # Track parameter context for list inference
            if isinstance(arg_type, PendingListType):
                self.list_tracker.mark_list_param_context(arg, ptype)

        return func.return_type
