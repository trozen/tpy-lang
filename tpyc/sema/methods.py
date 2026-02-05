"""
TurboPython Method Analysis

Method call and super() analysis.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, RecordType, ProtocolType, OwnType, ListType, PendingListType,
    SuperType, TypeParamRef, FunctionInfo, VOID
)
from ..parse import (
    TpyCall, TpyMethodCall, TpyName, TpyFunction, TpyExprStmt, TpyStrLiteral, TpyStmt
)
from ..namespace import BindingKind
from ..coercions import CoercionContext
from .diagnostics import SemanticError

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from .expressions import ExpressionAnalyzer

from tpyc import modules as builtin_modules


class MethodAnalyzer:
    """Method call and super() analysis."""

    def __init__(
        self,
        ctx: SemanticContext,
        type_ops: TypeOperations,
        protocols: ProtocolChecker,
        compat: TypeCompatibility,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.protocols = protocols
        self.compat = compat
        # Set later to break circular dependency
        self.expr: ExpressionAnalyzer | None = None

    @staticmethod
    def _analyze_super_call_static(ctx: SemanticContext, expr: TpyCall) -> TpyType:
        """Analyze a super() call (static method for use from CallAnalyzer).

        super() can only be called:
        - Inside a method (not at module level)
        - In a class that has a parent class
        - Without arguments (Python 3 style)

        Returns a SuperType that wraps the parent class type.
        """
        # Validate context: must be in a method
        if ctx.current_function is None or not isinstance(ctx.current_function, TpyFunction):
            raise ctx.error("super() can only be used inside a method", expr)

        if not ctx.current_function.is_method:
            raise ctx.error("super() can only be used inside a method", expr)

        if ctx.current_function.is_staticmethod:
            raise ctx.error("super() cannot be used in a static method", expr)

        # Validate context: must have a current record
        if ctx.current_record is None:
            raise ctx.error("super() can only be used inside a class method", expr)

        # Validate: class must have a parent
        record_info = ctx.registry.get_record(ctx.current_record.name)
        if record_info is None or record_info.parent is None:
            raise ctx.error(
                f"super() requires a parent class, but '{ctx.current_record.name}' has no parent",
                expr
            )

        # Validate: no arguments (Python 3 style only)
        if expr.args:
            raise ctx.error("super() takes no arguments (Python 3 style)", expr)

        return SuperType(record_info.parent, ctx.current_record.name)

    def analyze_method_call(self, expr: TpyMethodCall) -> TpyType:
        """Analyze a method call."""
        # Handle super().method() calls
        if isinstance(expr.obj, TpyCall) and expr.obj.func == "super":
            return self._analyze_super_method_call(expr)

        # Check for ClassName.staticmethod() pattern
        # Use namespace to verify the name refers to a record and isn't shadowed by a variable
        if isinstance(expr.obj, TpyName):
            is_record_name = False
            if self.ctx.current_ns:
                binding = self.ctx.current_ns.lookup(expr.obj.name)
                if binding and binding.kind == BindingKind.RECORD:
                    is_record_name = True
            else:
                # Fallback: check if it's a record and not shadowed by a variable
                if (self.ctx.registry.get_record(expr.obj.name) is not None and
                    self.ctx.current_scope.lookup(expr.obj.name) is None):
                    is_record_name = True

            if is_record_name:
                record_info = self.ctx.registry.get_record(expr.obj.name)
                method_info = self.protocols.lookup_record_method(record_info, expr.method)
                if method_info and method_info.is_staticmethod:
                    # It's a static method call via class name
                    if len(expr.args) != len(method_info.params):
                        raise SemanticError(
                            f"Static method '{expr.method}' expects {len(method_info.params)} arguments, "
                            f"got {len(expr.args)}"
                        )
                    # Type-check arguments
                    for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, method_info.params)):
                        arg_type = self.expr.analyze_expr(arg)
                        expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                                coercion_ctx=CoercionContext.ARG)
                    # Mark as static call for codegen
                    expr.is_static_call = True
                    return method_info.return_type
                elif method_info and not method_info.is_staticmethod:
                    raise SemanticError(f"Method '{expr.method}' requires an instance (not a static method)")

        # Check for module.function() pattern (import X -> X.func())
        # Use namespace to check if module binding exists and isn't shadowed
        if isinstance(expr.obj, TpyName):
            if self.ctx.current_ns:
                binding = self.ctx.current_ns.lookup(expr.obj.name)
                if binding and binding.kind == BindingKind.MODULE:
                    # It's a module call: module.function()
                    module_name = expr.obj.name

                    # Check unified registry for both user and builtin modules
                    module_info = self.ctx.registry.get_module(module_name)
                    if module_info and module_info.functions and expr.method in module_info.functions:
                        overloads = module_info.functions[expr.method]
                        if module_info.is_builtin:
                            temp_call = TpyCall(func=expr.method, args=expr.args, loc=expr.loc)
                            return self._analyze_builtin_function_overloads(temp_call, overloads)
                        else:
                            # User module: single overload
                            func_info = overloads[0]
                            return self._analyze_user_module_function_call(expr, func_info, module_name)
                    raise SemanticError(f"Module '{module_name}' has no function '{expr.method}'")
            # Fallback for when namespace isn't set
            elif expr.obj.name in self.ctx.imports:
                module_name = expr.obj.name
                # Check if shadowed by variable, user-defined function, or record
                if (self.ctx.current_scope.lookup(module_name) is None and
                    self.ctx.registry.get_function(module_name) is None and
                    self.ctx.registry.get_record(module_name) is None):
                    # Module was imported with 'import X' (not 'from X import ...')
                    if self.ctx.imports[module_name] is None:
                        # Check unified registry for both user and builtin modules
                        module_info = self.ctx.registry.get_module(module_name)
                        if module_info and module_info.functions and expr.method in module_info.functions:
                            overloads = module_info.functions[expr.method]
                            if module_info.is_builtin:
                                temp_call = TpyCall(func=expr.method, args=expr.args, loc=expr.loc)
                                return self._analyze_builtin_function_overloads(temp_call, overloads)
                            else:
                                # User module: single overload
                                func_info = overloads[0]
                                return self._analyze_user_module_function_call(expr, func_info, module_name)
                        raise SemanticError(f"Module '{module_name}' has no function '{expr.method}'")

        obj_type = self.expr.analyze_expr(expr.obj)

        # Unwrap OwnType for method lookup - Own[T] behaves as T for method calls
        if isinstance(obj_type, OwnType):
            obj_type = obj_type.wrapped

        # List mutation methods - mark literal as mutated before module lookup
        if isinstance(obj_type, (PendingListType, ListType)):
            mutation_methods = {"append", "pop", "insert", "remove", "clear", "extend", "reverse", "__setitem__"}
            if expr.method in mutation_methods:
                from .list_literals import ListLiteralTracker
                tracker = ListLiteralTracker(self.ctx)
                tracker.mark_list_mutated(expr.obj)

        # Built-in type methods - use registry lookup
        # (Skip for RecordType - those are handled in the user record path below)
        if not isinstance(obj_type, RecordType):
            record_info = self.ctx.registry.get_record_for_type(obj_type)
            if record_info:
                overloads = record_info.get_method_overloads(expr.method)
                if overloads:
                    type_subst = builtin_modules.extract_type_params(obj_type)

                    if len(overloads) == 1:
                        # Single overload - go directly to arg checking for better error messages
                        resolved = self.type_ops.substitute_method_type_params(overloads[0], type_subst) if type_subst else overloads[0]
                        if len(expr.args) != len(resolved.params):
                            raise SemanticError(
                                f"Method '{expr.method}' expects {len(resolved.params)} arguments, "
                                f"got {len(expr.args)}"
                            )
                        expr.resolved_function_info = resolved
                        # Type-check and coerce arguments (gives detailed error messages)
                        for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, resolved.params)):
                            arg_type = self.expr.analyze_expr(arg)
                            expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"{pname} argument",
                                                                    coercion_ctx=CoercionContext.ARG)
                        return resolved.return_type
                    else:
                        # Multiple overloads - find matching one
                        arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
                        resolved = self._resolve_method_overload(overloads, arg_types, type_subst)
                        if resolved is None:
                            arg_type_strs = ", ".join(str(t) for t in arg_types)
                            raise SemanticError(f"No matching overload for {expr.method}({arg_type_strs})")

                        expr.resolved_function_info = resolved
                        # Type-check and coerce arguments
                        for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, resolved.params)):
                            expr.args[i] = self.compat.coerce_expr(arg, arg_types[i], ptype, f"{pname} argument",
                                                                    coercion_ctx=CoercionContext.ARG)
                        return resolved.return_type

        # User-defined record methods (including inherited methods from builtins)
        if isinstance(obj_type, RecordType):
            record_info = self.ctx.registry.get_record(obj_type.name)
            if record_info:
                overloads, inherited_subst = self.protocols.lookup_record_method_overloads(record_info, expr.method)
                if overloads:
                    # Build type substitution for generic records
                    # Resolve any TypeParamRefs in inherited_subst using instance's type args
                    instance_subst = self.type_ops.build_type_substitution(obj_type)
                    if inherited_subst and instance_subst:
                        # Resolve TypeParamRefs in inherited values
                        type_subst = {
                            k: self.type_ops.substitute_type_params(v, instance_subst)
                            for k, v in inherited_subst.items()
                        }
                    elif inherited_subst:
                        type_subst = inherited_subst
                    else:
                        type_subst = instance_subst

                    if len(overloads) == 1:
                        # Single overload - check args directly for better error messages
                        method_info = overloads[0]
                        resolved = self.type_ops.substitute_method_type_params(method_info, type_subst) if type_subst else method_info
                        if len(expr.args) != len(resolved.params):
                            raise SemanticError(
                                f"Method '{expr.method}' expects {len(resolved.params)} arguments, "
                                f"got {len(expr.args)}"
                            )
                        expr.resolved_function_info = resolved
                        # Type-check and coerce arguments
                        for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, resolved.params)):
                            arg_type = self.expr.analyze_expr(arg)
                            expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                                    coercion_ctx=CoercionContext.ARG)
                        return resolved.return_type
                    else:
                        # Multiple overloads - do overload resolution
                        arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
                        for method_info in overloads:
                            resolved = self.type_ops.substitute_method_type_params(method_info, type_subst) if type_subst else method_info
                            if len(resolved.params) != len(arg_types):
                                continue
                            # Check if all args match params
                            match = True
                            for arg_type, (pname, ptype) in zip(arg_types, resolved.params):
                                if not builtin_modules._type_matches_param(arg_type, ptype):
                                    match = False
                                    break
                            if match:
                                expr.resolved_function_info = resolved
                                # Coerce arguments
                                for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, resolved.params)):
                                    expr.args[i] = self.compat.coerce_expr(arg, arg_types[i], ptype, f"{pname} argument",
                                                                            coercion_ctx=CoercionContext.ARG)
                                return resolved.return_type
                        # No matching overload found
                        param_types_str = ", ".join(str(t) for t in arg_types)
                        raise SemanticError(
                            f"No matching overload for '{expr.method}' with argument types ({param_types_str})"
                        )

        # Protocol-typed values - use protocol method signatures
        if isinstance(obj_type, ProtocolType):
            method_sig = self.protocols.get_protocol_method_signature(obj_type, expr.method)
            if method_sig is None:
                raise SemanticError(f"Protocol '{obj_type.name}' has no method '{expr.method}'")

            params, return_type = method_sig
            # Check argument count
            if len(expr.args) != len(params):
                raise SemanticError(
                    f"Method '{expr.method}' expects {len(params)} arguments, "
                    f"got {len(expr.args)}"
                )
            # Type-check and coerce arguments
            for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, params)):
                arg_type = self.expr.analyze_expr(arg)
                expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                        coercion_ctx=CoercionContext.ARG)
            return return_type

        # Bounded type parameter - treat method calls as if on the bound protocol
        if isinstance(obj_type, TypeParamRef):
            bound = self.type_ops.get_type_param_bound(obj_type.name)
            if bound is not None and isinstance(bound, ProtocolType):
                # Pass obj_type as self_type so Self in signatures resolves to T, not the protocol
                method_sig = self.protocols.get_protocol_method_signature(bound, expr.method, self_type=obj_type)
                if method_sig is None:
                    raise self.ctx.error(f"Protocol '{bound.name}' has no method '{expr.method}'", expr)

                params, return_type = method_sig
                # Check argument count
                if len(expr.args) != len(params):
                    raise self.ctx.error(
                        f"Method '{expr.method}' expects {len(params)} arguments, "
                        f"got {len(expr.args)}",
                        expr
                    )
                # Type-check and coerce arguments
                for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, params)):
                    arg_type = self.expr.analyze_expr(arg)
                    expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                            coercion_ctx=CoercionContext.ARG)
                return return_type

        raise self.ctx.error(f"Cannot call method '{expr.method}' on type {obj_type}", expr)

    def _analyze_super_method_call(self, expr: TpyMethodCall) -> TpyType:
        """Analyze a super().method() call.

        The method is looked up in the parent class and type arguments are
        substituted for generic parent classes.
        """
        # Analyze super() to get the SuperType
        assert isinstance(expr.obj, TpyCall) and expr.obj.func == "super"
        super_type = self._analyze_super_call_static(self.ctx, expr.obj)
        assert isinstance(super_type, SuperType)

        parent_type = super_type.parent_type
        parent_info = self.protocols._get_parent_record_info(parent_type)
        if parent_info is None:
            raise self.ctx.error(f"Parent class '{parent_type}' not found", expr)

        # Special handling for super().__init__()
        if expr.method == "__init__":
            # super().__init__() can only be called inside __init__
            if self.ctx.current_function is None or self.ctx.current_function.name != "__init__":
                raise self.ctx.error(
                    "super().__init__() can only be called inside __init__",
                    expr
                )
            # Check for duplicate super().__init__() calls
            if self.ctx.super_init_call is not None:
                raise self.ctx.error(
                    "super().__init__() can only be called once",
                    expr
                )
            # Track this call for later validation (must be first statement)
            self.ctx.super_init_call = expr

            # Check for __init__ method or constructors (builtin types use constructors)
            init_overloads = parent_info.get_method_overloads("__init__")
            if not init_overloads and parent_info.constructors:
                # Builtin type with constructors - use those as overloads
                init_overloads = parent_info.constructors

            if not init_overloads:
                # Parent has no explicit __init__ or constructors, allow with no arguments
                if expr.args:
                    raise self.ctx.error(
                        f"Parent class '{parent_type}' has no __init__, "
                        "super().__init__() must be called with no arguments",
                        expr
                    )
                # Store parent type for codegen (will generate default base init)
                expr.super_parent_type = parent_type
                return VOID

        # Look up the method in the parent class
        # For __init__, we already have init_overloads; for other methods, look up
        if expr.method == "__init__":
            overloads = init_overloads
        else:
            overloads = parent_info.get_method_overloads(expr.method)
        if not overloads:
            raise self.ctx.error(
                f"Parent class '{parent_type}' has no method '{expr.method}'",
                expr
            )

        # Build type substitution for generic parent (e.g., Container[Int32] -> {"T": Int32})
        type_subst = self.protocols._get_parent_type_subst(parent_type, parent_info)

        # For single overload, resolve directly
        if len(overloads) == 1:
            method_info = overloads[0]
            resolved = self.type_ops.substitute_method_type_params(method_info, type_subst) if type_subst else method_info

            if len(expr.args) != len(resolved.params):
                raise self.ctx.error(
                    f"Method '{expr.method}' expects {len(resolved.params)} arguments, "
                    f"got {len(expr.args)}",
                    expr
                )

            # Type-check and coerce arguments
            for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, resolved.params)):
                arg_type = self.expr.analyze_expr(arg)
                expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                        coercion_ctx=CoercionContext.ARG)

            # Store parent type for codegen
            expr.super_parent_type = parent_type
            return resolved.return_type

        # Multiple overloads - find matching one
        arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
        for method_info in overloads:
            resolved = self.type_ops.substitute_method_type_params(method_info, type_subst) if type_subst else method_info
            if len(resolved.params) != len(arg_types):
                continue
            # Check if all args match params
            match = True
            for arg_type, (pname, ptype) in zip(arg_types, resolved.params):
                if not builtin_modules._type_matches_param(arg_type, ptype):
                    match = False
                    break
            if match:
                # Coerce arguments
                for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, resolved.params)):
                    expr.args[i] = self.compat.coerce_expr(arg, arg_types[i], ptype, f"argument '{pname}'",
                                                            coercion_ctx=CoercionContext.ARG)
                # Store parent type for codegen
                expr.super_parent_type = parent_type
                return resolved.return_type

        # No matching overload found
        param_types_str = ", ".join(str(t) for t in arg_types)
        raise self.ctx.error(
            f"No matching overload for '{expr.method}' with argument types ({param_types_str})",
            expr
        )

    def _resolve_method_overload(
        self,
        overloads: list[FunctionInfo],
        arg_types: list[TpyType],
        type_subst: dict[str, TpyType]
    ) -> FunctionInfo | None:
        """Find matching overload from list of FunctionInfo.

        Args:
            overloads: List of method overloads to check
            arg_types: Already-analyzed argument types
            type_subst: Type parameter substitution (e.g., {"T": Int32})

        Returns:
            The matching FunctionInfo with type params substituted, or None
        """
        from ..coercions import resolve_coercion
        for method in overloads:
            # Substitute type params in method signature
            resolved = self.type_ops.substitute_method_type_params(method, type_subst) if type_subst else method

            # Check param count
            if len(resolved.params) != len(arg_types):
                continue

            # Check param types
            params_match = True
            for arg_t, (_, param_t) in zip(arg_types, resolved.params):
                if arg_t == param_t:
                    continue
                # IntLiteralType matches any IntLiteralType
                from ..typesys import IntLiteralType
                if isinstance(arg_t, IntLiteralType) and isinstance(param_t, IntLiteralType):
                    continue
                # Protocol parameter: check conformance
                if isinstance(param_t, ProtocolType):
                    if self.protocols.type_conforms_to_protocol(arg_t, param_t):
                        continue
                # Check if there's a coercion
                if resolve_coercion(arg_t, param_t, CoercionContext.ARG) is not None:
                    continue
                params_match = False
                break

            if params_match:
                return resolved

        return None

    def _analyze_builtin_function_overloads(self, expr: TpyCall, overloads: list[FunctionInfo]) -> TpyType:
        """Type-check a call to a builtin function using unified FunctionInfo overloads."""
        from ..coercions import resolve_coercion
        from ..typesys import IntLiteralType

        arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]

        # First pass: look for exact match
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

        # No matching overload found
        arg_type_strs = ", ".join(str(t) for t in arg_types)
        raise self.ctx.error(f"No matching overload for {expr.func}({arg_type_strs})", expr)

    def _builtin_type_matches_exact(self, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if argument type exactly matches parameter type."""
        if arg_type == param_type:
            return True
        if isinstance(param_type, ProtocolType):
            return self.protocols.type_conforms_to_protocol(arg_type, param_type)
        return False

    def _builtin_type_matches(self, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if argument type is compatible with parameter type (allows coercions)."""
        from ..coercions import resolve_coercion
        from ..typesys import IntLiteralType
        if arg_type == param_type:
            return True
        if isinstance(arg_type, IntLiteralType) and isinstance(param_type, IntLiteralType):
            return True
        if isinstance(param_type, ProtocolType):
            return self.protocols.type_conforms_to_protocol(arg_type, param_type)
        if resolve_coercion(arg_type, param_type, CoercionContext.ARG) is not None:
            return True
        return False

    def _analyze_user_module_function_call(
        self, expr: TpyMethodCall, func_info: FunctionInfo, module_name: str
    ) -> TpyType:
        """Analyze a call to a user module function (module.func() pattern).

        Args:
            expr: The method call expression (module.func(args))
            func_info: The FunctionInfo for the function
            module_name: Name of the user module

        Returns:
            The return type of the function
        """
        # Check argument count
        if len(expr.args) != len(func_info.params):
            raise SemanticError(
                f"Function '{func_info.name}' expects {len(func_info.params)} arguments, "
                f"got {len(expr.args)}"
            )

        # Type-check and coerce arguments
        for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, func_info.params)):
            arg_type = self.expr.analyze_expr(arg)
            expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                    coercion_ctx=CoercionContext.ARG)

        # Mark as user module call for codegen
        expr.user_module_call = module_name

        return func_info.return_type

    @staticmethod
    def stmt_contains_super_init(stmt: TpyStmt, super_init: TpyMethodCall) -> bool:
        """Check if a statement contains the given super().__init__() call.

        Used to validate that super().__init__() is the first statement.
        """
        # Direct expression statement containing the super().__init__() call
        if isinstance(stmt, TpyExprStmt):
            return stmt.expr is super_init
        return False

    @staticmethod
    def find_first_non_docstring_stmt(stmts: list[TpyStmt]) -> TpyStmt | None:
        """Find the first non-docstring statement in a list.

        Docstrings are expression statements containing a string literal.
        Returns None if all statements are docstrings or list is empty.
        """
        for stmt in stmts:
            # Skip docstrings (expression statements with string literals)
            if isinstance(stmt, TpyExprStmt) and isinstance(stmt.expr, TpyStrLiteral):
                continue
            return stmt
        return None
