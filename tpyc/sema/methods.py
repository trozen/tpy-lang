"""
TurboPython Method Analysis

Method call and super() analysis.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, OwnType, ListType, PendingListType,
    SuperType, TypeParamRef, FunctionInfo, VOID, is_protocol_type,
)
from ..parse import (
    TpyCall, TpyMethodCall, TpyName, TpyFunction, TpyExprStmt, TpyStrLiteral, TpyStmt
)
from ..namespace import BindingKind
from ..coercions import CoercionContext
from .diagnostics import SemanticError
from .overloads import resolve_overload

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from .expressions import ExpressionAnalyzer
    from .calls import CallAnalyzer

from tpyc import modules as builtin_modules
from tpyc.modules.builtins import LIST_MUTATION_METHODS


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
        # Set via set_cross_deps() to break circular dependency
        self.expr: ExpressionAnalyzer | None = None
        self.calls: CallAnalyzer | None = None

    def set_cross_deps(self, expr: ExpressionAnalyzer, calls: CallAnalyzer) -> None:
        """Wire circular dependencies (must be called before analyze_method_call)."""
        self.expr = expr
        self.calls = calls

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
        if ctx.record_ctx.record is None:
            raise ctx.error("super() can only be used inside a class method", expr)

        # Validate: class must have a parent
        record_info = ctx.registry.get_record(ctx.record_ctx.record.name)
        if record_info is None or record_info.parent is None:
            raise ctx.error(
                f"super() requires a parent class, but '{ctx.record_ctx.record.name}' has no parent",
                expr
            )

        # Validate: no arguments (Python 3 style only)
        if expr.args:
            raise ctx.error("super() takes no arguments (Python 3 style)", expr)

        return SuperType(record_info.parent, ctx.record_ctx.record.name)

    def analyze_method_call(self, expr: TpyMethodCall) -> TpyType:
        """Analyze a method call."""
        # super().method() calls
        if isinstance(expr.obj, TpyCall) and expr.obj.func == "super":
            return self._analyze_super_method_call(expr)

        if isinstance(expr.obj, TpyName):
            # ClassName.staticmethod() pattern
            result = self._analyze_static_method_call(expr)
            if result is not None:
                return result

            # module.function() pattern (import X -> X.func())
            result = self._analyze_module_method_call(expr)
            if result is not None:
                return result

        obj_type = self.expr.analyze_expr(expr.obj)

        if isinstance(obj_type, OwnType):
            obj_type = obj_type.wrapped

        # List mutation tracking
        if isinstance(obj_type, (PendingListType, ListType)):
            if expr.method in LIST_MUTATION_METHODS:
                from .list_literals import ListLiteralTracker
                tracker = ListLiteralTracker(self.ctx)
                tracker.mark_list_mutated(expr.obj)

        # Built-in type methods
        result = self._analyze_builtin_type_method(expr, obj_type)
        if result is not None:
            return result

        # User-defined record methods
        result = self._analyze_user_record_method(expr, obj_type)
        if result is not None:
            return result

        # Protocol-typed values and bounded type parameters
        result = self._analyze_protocol_or_bound_method(expr, obj_type)
        if result is not None:
            return result

        raise self.ctx.error(f"Cannot call method '{expr.method}' on type {obj_type}", expr)

    def _analyze_static_method_call(self, expr: TpyMethodCall) -> TpyType | None:
        """Check for ClassName.staticmethod() pattern. Returns type or None if not a static call."""
        assert isinstance(expr.obj, TpyName)
        is_record_name = False
        binding = self.ctx.current_ns.lookup(expr.obj.name)
        if binding and binding.kind == BindingKind.RECORD:
            is_record_name = True

        if not is_record_name:
            return None

        record_info = self.ctx.registry.get_record(expr.obj.name)
        method_info = self.protocols.lookup_record_method(record_info, expr.method)
        if method_info and method_info.is_staticmethod:
            if len(expr.args) != len(method_info.params):
                raise SemanticError(
                    f"Static method '{expr.method}' expects {len(method_info.params)} arguments, "
                    f"got {len(expr.args)}"
                )
            for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, method_info.params)):
                arg_type = self.expr.analyze_expr(arg)
                expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                        coercion_ctx=CoercionContext.ARG)
            expr.is_static_call = True
            return method_info.return_type
        elif method_info and not method_info.is_staticmethod:
            raise SemanticError(f"Method '{expr.method}' requires an instance (not a static method)")
        return None

    def _analyze_module_method_call(self, expr: TpyMethodCall) -> TpyType | None:
        """Check for module.function() pattern. Returns type or None if not a module call."""
        assert isinstance(expr.obj, TpyName)

        module_name = self._resolve_module_name(expr.obj.name)
        if module_name is None:
            return None

        module_info = self.ctx.registry.get_module(module_name)
        if module_info and module_info.functions and expr.method in module_info.functions:
            overloads = module_info.functions[expr.method]
            if module_info.is_builtin:
                expr.builtin_module_call = module_name
                temp_call = TpyCall(func=expr.method, args=expr.args, loc=expr.loc)
                return self._analyze_builtin_function_overloads(temp_call, overloads)
            else:
                func_info = overloads[0]
                return self._analyze_user_module_function_call(expr, func_info, module_name)

        qname = f"{module_name}.{expr.method}"
        if record_info := self.ctx.registry.get_builtin_record(qname):
            if record_info.constructors and not record_info.type_params:
                expr.builtin_module_call = module_name
                temp_call = TpyCall(func=expr.method, args=expr.args, loc=expr.loc)
                return self.calls._check_builtin_constructor(temp_call, record_info)

        raise SemanticError(f"Module '{module_name}' has no function '{expr.method}'")

    def _resolve_module_name(self, name: str) -> str | None:
        """Resolve a name to a module name if it refers to a module. Returns None otherwise."""
        binding = self.ctx.current_ns.lookup(name)
        if binding and binding.kind == BindingKind.MODULE:
            return binding.import_source[0] if binding.import_source else name
        return None

    def _analyze_builtin_type_method(self, expr: TpyMethodCall, obj_type: TpyType) -> TpyType | None:
        """Analyze a builtin type method call. Returns type or None if no matching builtin."""
        if isinstance(obj_type, NamedType) and obj_type.is_record:
            return None

        record_info = self.ctx.registry.get_record_for_type(obj_type)
        if not record_info:
            return None

        overloads = record_info.get_method_overloads(expr.method)
        if not overloads:
            return None

        type_subst = builtin_modules.extract_type_params(obj_type)

        if len(overloads) == 1:
            resolved = self.type_ops.substitute_method_type_params(overloads[0], type_subst) if type_subst else overloads[0]
            if len(expr.args) != len(resolved.params):
                raise SemanticError(
                    f"Method '{expr.method}' expects {len(resolved.params)} arguments, "
                    f"got {len(expr.args)}"
                )
            expr.resolved_function_info = resolved
            for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, resolved.params)):
                arg_type = self.expr.analyze_expr(arg)
                expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"{pname} argument",
                                                        coercion_ctx=CoercionContext.ARG)
            return resolved.return_type
        else:
            arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
            resolved = self._resolve_method_overload(overloads, arg_types, type_subst)
            if resolved is None:
                arg_type_strs = ", ".join(str(t) for t in arg_types)
                raise SemanticError(f"No matching overload for {expr.method}({arg_type_strs})")

            expr.resolved_function_info = resolved
            for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, resolved.params)):
                expr.args[i] = self.compat.coerce_expr(arg, arg_types[i], ptype, f"{pname} argument",
                                                        coercion_ctx=CoercionContext.ARG)
            return resolved.return_type

    def _analyze_user_record_method(self, expr: TpyMethodCall, obj_type: TpyType) -> TpyType | None:
        """Analyze a user-defined record method call. Returns type or None if not applicable."""
        if not (isinstance(obj_type, NamedType) and obj_type.is_record):
            return None

        record_info = self.ctx.registry.get_record(obj_type.name)
        if not record_info:
            return None

        overloads, inherited_subst = self.protocols.lookup_record_method_overloads(record_info, expr.method)
        if not overloads:
            return None

        # Build type substitution, resolving inherited TypeParamRefs with instance type args
        instance_subst = self.type_ops.build_type_substitution(obj_type)
        if inherited_subst and instance_subst:
            type_subst = {
                k: self.type_ops.substitute_type_params(v, instance_subst)
                for k, v in inherited_subst.items()
            }
        elif inherited_subst:
            type_subst = inherited_subst
        else:
            type_subst = instance_subst

        if len(overloads) == 1:
            method_info = overloads[0]
            resolved = self.type_ops.substitute_method_type_params(method_info, type_subst) if type_subst else method_info
            if len(expr.args) != len(resolved.params):
                raise SemanticError(
                    f"Method '{expr.method}' expects {len(resolved.params)} arguments, "
                    f"got {len(expr.args)}"
                )
            expr.resolved_function_info = resolved
            for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, resolved.params)):
                arg_type = self.expr.analyze_expr(arg)
                expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                        coercion_ctx=CoercionContext.ARG)
            return resolved.return_type
        else:
            arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
            resolved_overloads = [
                self.type_ops.substitute_method_type_params(m, type_subst) if type_subst else m
                for m in overloads
            ]
            resolved = resolve_overload(
                resolved_overloads, arg_types,
                protocol_checker=self.protocols.type_conforms_to_protocol,
            )
            if resolved is not None:
                expr.resolved_function_info = resolved
                for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, resolved.params)):
                    expr.args[i] = self.compat.coerce_expr(arg, arg_types[i], ptype, f"{pname} argument",
                                                            coercion_ctx=CoercionContext.ARG)
                return resolved.return_type
            param_types_str = ", ".join(str(t) for t in arg_types)
            raise SemanticError(
                f"No matching overload for '{expr.method}' with argument types ({param_types_str})"
            )

    def _analyze_protocol_or_bound_method(self, expr: TpyMethodCall, obj_type: TpyType) -> TpyType | None:
        """Analyze method calls on protocol-typed values or bounded type parameters."""
        if is_protocol_type(obj_type):
            method_sig = self.protocols.get_protocol_method_signature(obj_type, expr.method)
            if method_sig is None:
                raise SemanticError(f"Protocol '{obj_type.name}' has no method '{expr.method}'")

            params, return_type = method_sig
            if len(expr.args) != len(params):
                raise SemanticError(
                    f"Method '{expr.method}' expects {len(params)} arguments, "
                    f"got {len(expr.args)}"
                )
            for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, params)):
                arg_type = self.expr.analyze_expr(arg)
                expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                        coercion_ctx=CoercionContext.ARG)
            return return_type

        if isinstance(obj_type, TypeParamRef):
            bound = self.type_ops.get_type_param_bound(obj_type.name)
            if bound is not None and is_protocol_type(bound):
                method_sig = self.protocols.get_protocol_method_signature(bound, expr.method, self_type=obj_type)
                if method_sig is None:
                    raise self.ctx.error(f"Protocol '{bound.name}' has no method '{expr.method}'", expr)

                params, return_type = method_sig
                if len(expr.args) != len(params):
                    raise self.ctx.error(
                        f"Method '{expr.method}' expects {len(params)} arguments, "
                        f"got {len(expr.args)}",
                        expr
                    )
                for i, (arg, (pname, ptype)) in enumerate(zip(expr.args, params)):
                    arg_type = self.expr.analyze_expr(arg)
                    expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                            coercion_ctx=CoercionContext.ARG)
                return return_type

        return None

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
        resolved_overloads = [
            self.type_ops.substitute_method_type_params(m, type_subst) if type_subst else m
            for m in overloads
        ]
        resolved = resolve_overload(
            resolved_overloads, arg_types,
            protocol_checker=self.protocols.type_conforms_to_protocol,
        )
        if resolved is not None:
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
        resolved_overloads = [
            self.type_ops.substitute_method_type_params(m, type_subst) if type_subst else m
            for m in overloads
        ]
        return resolve_overload(
            resolved_overloads, arg_types,
            protocol_checker=self.protocols.type_conforms_to_protocol,
        )

    def _analyze_builtin_function_overloads(self, expr: TpyCall, overloads: list[FunctionInfo]) -> TpyType:
        """Type-check a call to a builtin function using unified FunctionInfo overloads."""
        arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
        protocol_checker = self.protocols.type_conforms_to_protocol

        matched = resolve_overload(overloads, arg_types, protocol_checker)
        if matched is not None:
            # Apply coercions to arguments where needed
            for i, (arg, arg_t, (pname, ptype)) in enumerate(zip(expr.args, arg_types, matched.params)):
                if arg_t != ptype:
                    expr.args[i] = self.compat.coerce_expr(arg, arg_t, ptype,
                                                            f"argument '{pname}'",
                                                            coercion_ctx=CoercionContext.ARG)
            return matched.return_type

        # No matching overload found
        arg_type_strs = ", ".join(str(t) for t in arg_types)
        raise self.ctx.error(f"No matching overload for {expr.func}({arg_type_strs})", expr)

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
