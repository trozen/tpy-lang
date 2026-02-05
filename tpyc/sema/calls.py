"""
TurboPython Call Analysis

Function and constructor call analysis.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, RecordType, ProtocolType, OwnType, ListType, PendingListType, IntLiteralType,
    StrType, CharType, ListLiteralInfo, FunctionInfo, RecordInfo,
    VOID, BIGINT
)
from ..parse import TpyCall, TpyStrLiteral, TpyName
from ..namespace import BindingKind
from ..coercions import CoercionContext
from .diagnostics import SemanticError

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
        # Set later to break circular dependency
        self.expr: ExpressionAnalyzer | None = None

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

        # Use namespace for unified lookup - handles shadowing automatically
        if self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(expr.func)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    raise SemanticError(f"'{expr.func}' is not callable")
                elif binding.kind == BindingKind.FUNCTION:
                    return self._analyze_user_function_call(expr, binding.func_info)
                elif binding.kind == BindingKind.RECORD:
                    return self._analyze_record_constructor(expr, binding.record_info)
                elif binding.kind == BindingKind.IMPORTED_NAME:
                    module_name, func_name = binding.import_source
                    # Special handling for copy() from tpy - truly generic function
                    if module_name == "tpy" and func_name == "copy":
                        return self._analyze_tpy_copy(expr)
                    # Check for module function (e.g., math.sqrt)
                    from .registration import TypeRegistrar
                    if overloads := self._get_module_function_overloads(module_name, func_name):
                        return self._analyze_builtin_function_overloads(expr, overloads)
                    # Check for type constructor (e.g., Int32 from tpy)
                    if record_info := self.ctx.registry.get_builtin_record_by_name(func_name):
                        return self._check_builtin_constructor(expr, record_info)
                    raise SemanticError(f"Unknown function '{func_name}' in module '{module_name}'")
                elif binding.kind == BindingKind.MODULE:
                    raise SemanticError(f"Cannot call module '{expr.func}' directly; use module.function()")
                elif binding.kind == BindingKind.BUILTIN:
                    raise SemanticError(f"'{expr.func}' is not callable")

        # Fallback to old lookup for compatibility (when current_ns not set)
        # Check if it's an imported function (from X import Y)
        # Only if not shadowed by a variable, user-defined function, or record
        if expr.func in self.ctx.imported_names:
            is_shadowed = (self.ctx.current_scope.lookup(expr.func) is not None or
                           self.ctx.registry.get_function(expr.func) is not None or
                           self.ctx.registry.get_record(expr.func) is not None)
            if not is_shadowed:
                module_name, func_name = self.ctx.imported_names[expr.func]
                # Special handling for copy() from tpy - truly generic function
                if module_name == "tpy" and func_name == "copy":
                    return self._analyze_tpy_copy(expr)
                # Check for module function
                if overloads := self._get_module_function_overloads(module_name, func_name):
                    return self._analyze_builtin_function_overloads(expr, overloads)
                # Check for type constructor (e.g., Int32 from tpy)
                if record_info := self.ctx.registry.get_builtin_record_by_name(func_name):
                    return self._check_builtin_constructor(expr, record_info)

        # Built-in print()
        if expr.func == "print":
            for arg in expr.args:
                self.expr.analyze_expr(arg)
            return VOID

        # Check if it's a builtin type constructor (e.g., Int32, int)
        if record_info := self.ctx.registry.get_builtin_record_by_name(expr.func):
            return self._check_builtin_constructor(expr, record_info)

        # Fallback: Check if it's a record constructor
        record = self.ctx.registry.get_record(expr.func)
        if record:
            # Analyze arguments
            for arg in expr.args:
                self.expr.analyze_expr(arg)
            return RecordType(expr.func)

        # Fallback: Check if it's a function call
        func = self.ctx.registry.get_function(expr.func)
        if func:
            return self._analyze_legacy_function_call(expr, func)

        # Generic type constructor without context for type inference
        if lookup := builtin_modules.lookup_generic_type(expr.func):
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
            raise SemanticError("copy() takes exactly 1 argument")
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
            # Check if all arguments match
            match = True
            for (pname, ptype), arg_type in zip(ctor.params, arg_types):
                if not builtin_modules._type_matches_param(arg_type, ptype):
                    match = False
                    break
            if match:
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

        # First pass: look for exact match (no coercions needed)
        for overload in overloads:
            if len(overload.params) != len(arg_types):
                continue
            if all(self._builtin_type_matches_exact(arg_t, ptype)
                   for arg_t, (_, ptype) in zip(arg_types, overload.params)):
                return overload.return_type

        # Second pass: allow coercions
        for overload in overloads:
            if len(overload.params) != len(arg_types):
                continue
            if all(self._builtin_type_matches(arg_t, ptype)
                   for arg_t, (_, ptype) in zip(arg_types, overload.params)):
                # Apply coercions to arguments where needed
                for i, (arg, arg_t, (pname, ptype)) in enumerate(zip(expr.args, arg_types, overload.params)):
                    if arg_t != ptype:
                        expr.args[i] = self.compat.coerce_expr(arg, arg_t, ptype,
                                                                f"argument '{pname}'",
                                                                coercion_ctx=CoercionContext.ARG)
                return overload.return_type

        # No matching overload found - build error message
        arg_type_strs = ", ".join(str(t) for t in arg_types)
        raise self.ctx.error(f"No matching overload for {expr.func}({arg_type_strs})", expr)

    def _builtin_type_matches_exact(self, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if an argument type exactly matches a builtin parameter type (no coercions)."""
        if arg_type == param_type:
            return True
        # Protocol parameter: check if arg_type conforms to the protocol
        if isinstance(param_type, ProtocolType):
            return self.protocols.type_conforms_to_protocol(arg_type, param_type)
        return False

    def _builtin_type_matches(self, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if an argument type is compatible with a builtin parameter type (allows coercions)."""
        from ..coercions import resolve_coercion
        if arg_type == param_type:
            return True
        # IntLiteralType matches any IntLiteralType (regardless of value field)
        if isinstance(arg_type, IntLiteralType) and isinstance(param_type, IntLiteralType):
            return True
        # Protocol parameter: check if arg_type conforms to the protocol
        if isinstance(param_type, ProtocolType):
            return self.protocols.type_conforms_to_protocol(arg_type, param_type)
        # Check if there's a coercion from arg_type to param_type
        if resolve_coercion(arg_type, param_type, CoercionContext.ARG) is not None:
            return True
        return False

    def _analyze_user_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a call to a user-defined function."""
        # Handle generic functions
        if func.is_generic():
            return self._analyze_generic_function_call(expr, func)

        if len(expr.args) != len(func.params):
            raise SemanticError(f"Function '{expr.func}' expects {len(func.params)} arguments, got {len(expr.args)}")
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
                if isinstance(type_arg, ProtocolType):
                    raise self.ctx.error(
                        f"Protocol type '{type_arg.name}' cannot be used as a type argument. "
                        f"Protocols are only valid for function parameters",
                        expr
                    )
                # Check for unknown record types (no forward references allowed at call sites)
                if isinstance(type_arg, RecordType) and not type_arg.type_args:
                    if self.ctx.registry.get_record(type_arg.name) is None:
                        raise self.ctx.error(f"Unknown type: {type_arg.name}", expr)
                # Validate the type (checks for missing generic args, etc.)
                self.type_ops.validate_type(type_arg)
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
        if expr.call_type is not None and isinstance(expr.call_type, RecordType):
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
                    inferred_type = RecordType(record.name, type_args)
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
        return RecordType(record.name)

    def _analyze_legacy_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a legacy function call (fallback path)."""
        if len(expr.args) != len(func.params):
            raise SemanticError(f"Function '{expr.func}' expects {len(func.params)} arguments, got {len(expr.args)}")
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
