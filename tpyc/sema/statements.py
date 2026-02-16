"""
TurboPython Statement Analysis

Statement analysis including variable declarations, assignments, and control flow.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType, OwnType, ReadonlyType,
    ListType, PendingListType, NamedType, CharType, StrType, TypeParamRef,
    ListLiteralInfo, PtrType, ConstPtrType, NoneType, OptionalType,
    unwrap_readonly,
    INT32, VOID, BIGINT, is_protocol_type,
)
from ..parse import (
    TpyExpr,
    TpyStmt, TpyVarDecl, TpyAssign, TpyAugAssign, TpyExprStmt, TpyReturn,
    TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue, TpyAssert, TpyRaiseStopIteration,
    TpyGlobal,
    TpyCall, TpyMethodCall, TpyArrayLiteral, TpyCoerce,
    TpySubscript, TpyStrLiteral, TpyName,
    TpyFieldAccess, TpyFunction,
)
from ..coercions import CoercionContext
from ..namespace import BindingKind
from ..parse.nodes import VarLinkage
from .diagnostics import SemanticError
from .narrowing import NarrowingTracker
from .scope_tracker import ScopeTracker
from .init_tracker import InitTracker
from .reassignment_inference import ReassignmentInference

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .compatibility import TypeCompatibility
    from .list_literals import ListLiteralTracker
    from .expressions import ExpressionAnalyzer
    from .protocols import ProtocolChecker

from tpyc import modules as builtin_modules


class StatementAnalyzer:
    """Statement analysis."""

    def __init__(
        self,
        ctx: SemanticContext,
        type_ops: TypeOperations,
        compat: TypeCompatibility,
        list_tracker: ListLiteralTracker,
        protocols: ProtocolChecker,
        narrowing: NarrowingTracker,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.compat = compat
        self.list_tracker = list_tracker
        self.protocols = protocols
        self.narrowing = narrowing
        self.scopes = ScopeTracker(ctx, compat)
        self.init = InitTracker(ctx)
        self.reassign = ReassignmentInference(ctx, compat)
        # Set via set_cross_deps() to break circular dependency
        self.expr: ExpressionAnalyzer | None = None

    def set_cross_deps(self, expr: ExpressionAnalyzer) -> None:
        """Wire circular dependencies (must be called before analyze_stmt)."""
        self.expr = expr

    @staticmethod
    def _is_readonly_call_expr(expr: TpyExpr) -> bool:
        """Check if a call expression is known to be readonly (for narrowing)."""
        if isinstance(expr, TpyCall) and expr.func == "super":
            return True
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            info = expr.resolved_function_info
            return info is not None and info.is_readonly
        return False

    def _save_ns_var_types(self) -> dict[str, TpyType]:
        """Save namespace variable types for later restoration."""
        result: dict[str, TpyType] = {}
        if self.ctx.current_ns:
            for name, binding in self.ctx.current_ns.all_bindings().items():
                if binding.kind == BindingKind.VARIABLE:
                    result[name] = binding.type
        return result

    def _restore_ns_var_types(self, saved: dict[str, TpyType]) -> None:
        """Restore namespace variable types from a saved snapshot."""
        if self.ctx.current_ns:
            for name, typ in saved.items():
                self.ctx.current_ns.update_variable_type(name, typ)

    def _sync_ns_var_type(self, name: str, typ: TpyType) -> None:
        """Sync a single variable's namespace type to match scope."""
        if self.ctx.current_ns:
            self.ctx.current_ns.update_variable_type(name, typ)

    def _enforce_readonly_assignment_target(self, target: TpyExpr) -> None:
        """Reject assignments through readonly references (type-based check)."""
        if isinstance(target, (TpyFieldAccess, TpySubscript)):
            obj_type = self.ctx.get_expr_type(target.obj)
            if obj_type is not None:
                check_type = obj_type
                if isinstance(check_type, OptionalType):
                    check_type = check_type.inner
                if isinstance(check_type, ReadonlyType):
                    raise self.ctx.error("Cannot mutate readonly reference", target)

    def analyze_stmt(self, stmt: TpyStmt) -> None:
        """Analyze a statement."""
        if isinstance(stmt, TpyVarDecl):
            self._analyze_var_decl(stmt)
        elif isinstance(stmt, TpyAssign):
            self._analyze_assign(stmt)
        elif isinstance(stmt, TpyAugAssign):
            self._analyze_aug_assign(stmt)
        elif isinstance(stmt, TpyExprStmt):
            self.expr.analyze_expr(stmt.expr)
            if self.narrowing.expr_has_unknown_call(stmt.expr):
                self.ctx.non_none_exprs.clear()
        elif isinstance(stmt, TpyReturn):
            if stmt.value:
                ret_type = self.expr.analyze_expr(stmt.value)
                expected = self.ctx.current_function.return_type if self.ctx.current_function else VOID
                stmt.value = self.compat.coerce_expr(stmt.value, ret_type, expected, "return value",
                                                      coercion_ctx=CoercionContext.RETURN, is_return=True)
                # Check for lvalue returned as Own[T] without explicit copy()
                if isinstance(expected, OwnType) and self.compat.is_lvalue(stmt.value):
                    if not self.compat.is_copy_call(stmt.value):
                        raise self.ctx.error(
                            f"Cannot return lvalue as Own[{expected.wrapped}] without explicit copy(). "
                            f"Use 'return copy(...)' instead.",
                            stmt.value
                        )
                # Check for dangling reference (returning local/temporary as reference)
                self.compat.check_dangling_reference(stmt.value, expected, stmt.loc)
            self.init.mark_terminated()
        elif isinstance(stmt, TpyIf):
            self.expr.analyze_expr(stmt.condition)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            then_facts, else_facts = self.narrowing.condition_name_facts(stmt.condition)
            then_expr_facts, else_expr_facts = self.narrowing.condition_expr_facts(stmt.condition)
            scope_before = set(self.ctx.current_scope.bindings.keys())
            assigned_before = frozenset(self.ctx.definitely_assigned)
            before = self.init.save()
            # Save binding types for ReadonlyType merge after branches
            bindings_before = dict(self.ctx.current_scope.bindings)
            ns_types_before = self._save_ns_var_types()
            self.ctx.non_none_vars.update(then_facts)
            self.ctx.non_none_exprs.update(then_expr_facts)
            for s in stmt.then_body:
                self.analyze_stmt(s)
            then_state = self.init.save()
            bindings_after_then = dict(self.ctx.current_scope.bindings)
            # Restore bindings for else branch
            self.ctx.current_scope.bindings.update(bindings_before)
            self._restore_ns_var_types(ns_types_before)
            self.init.restore(before)
            self.ctx.non_none_vars.update(else_facts)
            self.ctx.non_none_exprs.update(else_expr_facts)
            for s in stmt.else_body:
                self.analyze_stmt(s)
            else_state = self.init.save()
            bindings_after_else = dict(self.ctx.current_scope.bindings)
            self.init.merge_branches(then_state, else_state)
            # Merge ReadonlyType: if readonly on EITHER branch, keep readonly
            for name in set(bindings_after_then) | set(bindings_after_else):
                then_type = bindings_after_then.get(name)
                else_type = bindings_after_else.get(name)
                if then_type is not None and else_type is not None:
                    then_is_ro = isinstance(then_type, ReadonlyType)
                    else_is_ro = isinstance(else_type, ReadonlyType)
                    if then_is_ro and not else_is_ro:
                        merged = ReadonlyType(unwrap_readonly(else_type))
                        self.ctx.current_scope.define(name, merged)
                        self._sync_ns_var_type(name, merged)
                    elif else_is_ro and not then_is_ro:
                        merged = ReadonlyType(unwrap_readonly(then_type))
                        self.ctx.current_scope.define(name, merged)
                        self._sync_ns_var_type(name, merged)
            # Detect variables first declared inside branches that need
            # pre-declaration. Skip when both branches terminate (no code
            # after the if needs the variable).
            if not self.ctx.init_terminated:
                branch_new = set(self.ctx.current_scope.bindings.keys()) - scope_before
                newly_assigned = self.ctx.definitely_assigned - assigned_before
                predecl = (branch_new & newly_assigned) - self.ctx.global_declarations
            else:
                predecl = set()
            if predecl:
                self.ctx.if_branch_decls[id(stmt)] = {
                    name: self.ctx.current_scope.lookup(name)
                    for name in sorted(predecl)
                }
        elif isinstance(stmt, TpyWhile):
            self.expr.analyze_expr(stmt.condition)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            then_facts, _ = self.narrowing.condition_name_facts(stmt.condition)
            then_expr_facts, _ = self.narrowing.condition_expr_facts(stmt.condition)
            before = self.init.save()
            # Save namespace types -- loop_scope() restores scope bindings
            # automatically, but namespace mutations inside the loop persist.
            ns_types_before_while = self._save_ns_var_types()
            with self.scopes.loop_scope():
                self.init.apply_loop_entry_facts(
                    before,
                    condition_non_none=then_facts,
                    condition_non_none_exprs=then_expr_facts,
                )
                for s in stmt.body:
                    self.analyze_stmt(s)
            # Vars reassigned from unknown inside the body lose non-null provenance
            body_end_nn_ptr = frozenset(self.ctx.non_null_ptr_vars)
            self.init.restore(before)
            self.ctx.non_null_ptr_vars &= body_end_nn_ptr
            # Restore namespace to pre-loop state (scope was already restored
            # by loop_scope context manager)
            self._restore_ns_var_types(ns_types_before_while)
        elif isinstance(stmt, TpyForEach):
            iterable_type = self.expr.analyze_expr(stmt.iterable)
            is_readonly_iterable = isinstance(iterable_type, ReadonlyType)
            inner_iterable_type = unwrap_readonly(iterable_type)
            elem_type = self.list_tracker.get_iterable_element_type(inner_iterable_type, loc=stmt.loc)
            # Elements from a readonly iterable inherit readonly status
            if is_readonly_iterable and not elem_type.is_value_type():
                elem_type = ReadonlyType(unwrap_readonly(elem_type))
            before = self.init.save()
            ns_types_before_foreach = self._save_ns_var_types()
            with self.scopes.loop_scope() as inner_scope:
                self.init.apply_loop_entry_facts(before)
                # OptIterator and __iter__-based types produce fresh values each iteration
                is_native_iterator = builtin_modules.get_native_iterator_element_type(inner_iterable_type, registry=self.ctx.registry) is not None
                is_iter_based = builtin_modules.get_iter_element_type(inner_iterable_type, registry=self.ctx.registry) is not None
                if is_native_iterator or is_iter_based:
                    iter_depth = inner_scope.depth
                elif self.compat.is_lvalue(stmt.iterable):
                    # For-each var references container's storage -- use container's depth.
                    iter_depth = self.scopes.get_expr_scope_depth(stmt.iterable)
                else:
                    # For rvalue iterables (calls), C++ extends the temporary's lifetime
                    # to the for statement, but it dies when the loop ends. Use body depth
                    # so that escaping to any outer-scoped variable is caught.
                    iter_depth = inner_scope.depth
                # Track provenance for non-value-type loop vars from param-derived iterables
                track_loop_prov = (
                    not unwrap_readonly(elem_type).is_value_type()
                    and self.compat.is_param_derived_expr(stmt.iterable)
                )
                if track_loop_prov:
                    self.init.add_loop_var_provenance(stmt.var)
                with self.scopes.loop_var(inner_scope, stmt.var, elem_type, iter_depth, is_foreach=True):
                    for s in stmt.body:
                        self.analyze_stmt(s)
                if track_loop_prov:
                    self.init.remove_loop_var_provenance(stmt.var)
            body_end_nn_ptr = frozenset(self.ctx.non_null_ptr_vars)
            self.init.restore(before)
            self.ctx.non_null_ptr_vars &= body_end_nn_ptr
            self._restore_ns_var_types(ns_types_before_foreach)
        elif isinstance(stmt, TpyBreak):
            if self.ctx.loop_depth == 0:
                raise self.ctx.error("'break' outside loop", stmt)
            self.init.mark_terminated()
        elif isinstance(stmt, TpyContinue):
            if self.ctx.loop_depth == 0:
                raise self.ctx.error("'continue' outside loop", stmt)
            self.init.mark_terminated()
        elif isinstance(stmt, TpyAssert):
            self.expr.analyze_expr(stmt.condition)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            if stmt.message is not None:
                self.expr.analyze_expr(stmt.message)
                if not isinstance(stmt.message, TpyStrLiteral):
                    raise self.ctx.error("assert message must be a string literal", stmt)
            then_facts, _ = self.narrowing.condition_name_facts(stmt.condition)
            then_expr_facts, _ = self.narrowing.condition_expr_facts(stmt.condition)
            self.ctx.non_none_vars.update(then_facts)
            self.ctx.non_none_exprs.update(then_expr_facts)
        elif isinstance(stmt, TpyGlobal):
            self._analyze_global_stmt(stmt)
        elif isinstance(stmt, TpyRaiseStopIteration):
            func = self.ctx.current_function
            if not isinstance(func, TpyFunction) or func.name != "__next__":
                raise self.ctx.error("'raise StopIteration' can only be used inside a __next__ method", stmt)
            self.init.mark_terminated()

    def _analyze_global_stmt(self, stmt: TpyGlobal) -> None:
        """Analyze a `global x, y` statement."""
        from .context import MODULE_INIT_CONTEXT
        # Must be inside a function, not at module level
        if self.ctx.is_top_level or isinstance(self.ctx.current_function, type(MODULE_INIT_CONTEXT)):
            raise self.ctx.error("'global' declaration is only allowed inside a function", stmt)
        for name in stmt.names:
            # Name must exist in global scope
            global_type = self.ctx.global_scope.lookup(name)
            if global_type is None:
                raise self.ctx.error(f"name '{name}' is not defined at module level", stmt)
            # Must not shadow a function parameter
            func = self.ctx.current_function
            if isinstance(func, TpyFunction):
                for pname, _ in func.params:
                    if pname == name:
                        raise self.ctx.error(
                            f"name '{name}' is a parameter and cannot be declared global", stmt)
            self.ctx.global_declarations.add(name)

    def _check_nonvalue_rebinding(self, name: str, node: TpyStmt) -> None:
        """Error if reassigning a non-value-type param, loop variable, or global."""
        existing_type = self.ctx.current_scope.lookup(name)
        if existing_type is None or unwrap_readonly(existing_type).is_value_type():
            return
        # Check function parameters
        func = self.ctx.current_function
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                if pname == name and not unwrap_readonly(ptype).is_value_type():
                    raise self.ctx.error(
                        f"Cannot reassign parameter '{name}' of type '{unwrap_readonly(ptype)}'; "
                        f"assign to a new local variable instead",
                        node
                    )
        # Check for-each loop variables
        if name in self.ctx.loop_vars:
            raise self.ctx.error(
                f"Cannot reassign loop variable '{name}' of type '{existing_type}'; "
                f"assign to a new local variable instead",
                node
            )
        # Check global-declared non-value-type variables
        if name in self.ctx.global_declarations:
            raise self.ctx.error(
                f"Cannot reassign global variable '{name}' of non-value type '{existing_type}'",
                node
            )

    def _analyze_var_decl(self, stmt: TpyVarDecl) -> None:
        """Analyze a variable declaration."""
        # Validate the type annotation if present
        # Allow TypeParamRef when inside a generic record's methods
        if stmt.type:
            try:
                self.type_ops.validate_type(stmt.type, allow_type_param_ref=bool(self.ctx.record_ctx.type_params), loc=stmt.loc)
            except SemanticError as e:
                raise self.ctx.error(str(e), stmt)

        # Own[T] is only valid for function parameters and return types, not variables
        if stmt.type and isinstance(stmt.type, OwnType):
            raise self.ctx.error(
                f"Own[{stmt.type.wrapped}] cannot be used as a variable type. "
                f"Use '{stmt.type.wrapped}' instead (Own[T] is for parameters and return types only)",
                stmt
            )

        # readonly[T] is only valid for function parameters, not variables
        if stmt.type and isinstance(stmt.type, ReadonlyType):
            raise self.ctx.error(
                f"readonly[{stmt.type.wrapped}] cannot be used as a variable type. "
                f"Readonly on locals is deduced from initialization",
                stmt
            )

        # Protocol types can only be used for function parameters, not variables
        if stmt.type and is_protocol_type(stmt.type):
            raise self.ctx.error(
                f"Protocol type '{stmt.type.name}' cannot be used as a variable type. "
                f"Protocols are only valid for function parameters",
                stmt
            )

        # Detect native global import: x: T = native_c_global("name") / native_global("name")
        if isinstance(stmt.init, TpyCall) and self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(stmt.init.func)
            if (binding and binding.kind == BindingKind.IMPORTED_NAME
                    and binding.import_source
                    and binding.import_source[0] == "tpy"
                    and binding.import_source[1] in ("native_c_global", "native_global")):
                func_name = binding.import_source[1]
                if not self.ctx.is_top_level:
                    raise self.ctx.error(
                        f"{func_name}() can only be used at module level",
                        stmt
                    )
                if stmt.type is None:
                    raise self.ctx.error(
                        f"{func_name}() requires a type annotation",
                        stmt
                    )
                native_name = None
                if len(stmt.init.args) == 1:
                    if not isinstance(stmt.init.args[0], TpyStrLiteral):
                        raise self.ctx.error(
                            f"{func_name}() argument must be a string literal",
                            stmt
                        )
                    native_name = stmt.init.args[0].value
                elif len(stmt.init.args) > 1:
                    raise self.ctx.error(
                        f"{func_name}() takes 0 or 1 arguments",
                        stmt
                    )
                stmt.linkage = VarLinkage.NATIVE_C if func_name == "native_c_global" else VarLinkage.NATIVE
                stmt.native_name = native_name
                stmt.init = None
                return

        # Handle `global x` declarations: treat as reassignment of the global variable
        is_global_declared = stmt.name in self.ctx.global_declarations
        if is_global_declared:
            existing_type = self.ctx.global_scope.lookup(stmt.name)
            if stmt.type is not None:
                raise self.ctx.error(
                    f"Cannot add type annotation to global variable '{stmt.name}' from inside a function",
                    stmt
                )
        else:
            # Check if this is a reassignment (variable already exists in scope)
            existing_type = self.ctx.current_scope.lookup(stmt.name)
            # Top-level typed globals are pre-registered before statement analysis.
            # For an earlier unannotated write to the same name, treat this as a
            # fresh local write and let a later annotation retro-validate history.
            is_preregistered_global_write = (
                self.ctx.is_top_level
                and stmt.type is None
                and stmt.init is not None
                and stmt.name not in self.ctx.current_scope.bindings
                and stmt.name in self.ctx.global_scope.bindings
                and stmt.name not in self.ctx.authoritative_types
            )
            if is_preregistered_global_write:
                existing_type = None

        # Disallow reassignment of non-value-type params and loop vars
        if existing_type is not None:
            self._check_nonvalue_rebinding(stmt.name, stmt)

        if stmt.init:
            # Handle empty list literal or generic type constructor with explicit type annotation
            # Note: [] * N is collapsed to [] in the parser
            is_empty_literal = isinstance(stmt.init, TpyArrayLiteral) and not stmt.init.elements
            is_generic_constructor = (isinstance(stmt.init, TpyCall) and
                                      not stmt.init.args and
                                      stmt.init.call_type is None and
                                      builtin_modules.lookup_generic_type(stmt.init.func) is not None)

            if (is_empty_literal or is_generic_constructor) and stmt.type:
                # Check if annotation matches the constructor's generic type
                annotation_matches = False
                if is_generic_constructor:
                    lookup = builtin_modules.lookup_generic_type(stmt.init.func)
                    annotation_matches = (lookup is not None and
                                          stmt.type.qualified_name() == lookup.qualified_name)
                else:
                    # Empty literal [] can match list[T] annotation
                    annotation_matches = isinstance(stmt.type, ListType)

                if annotation_matches:
                    if isinstance(stmt.type, ListType):
                        # list[T]: Use PendingListType for potential Array optimization
                        elem_type = stmt.type.element_type
                        # Set call_type so codegen generates explicit type (e.g., std::vector<int>())
                        if is_generic_constructor:
                            stmt.init.call_type = stmt.type  # type: ignore
                        if self.ctx.current_function is None:
                            # Global context: return ListType directly
                            init_type = ListType(elem_type)
                        else:
                            # Function-local context: create PendingListType
                            literal_id = self.ctx.literal_counter
                            self.ctx.literal_counter += 1
                            info = ListLiteralInfo(
                                literal_id=literal_id,
                                expr=stmt.init,
                                element_type=elem_type,
                                size=0,
                                is_global=self.ctx.is_top_level,
                                has_explicit_annotation=True,
                                explicit_type=stmt.type
                            )
                            self.ctx.list_literals[literal_id] = info
                            self.ctx.pending_resolutions.append(literal_id)
                            init_type = PendingListType(elem_type, 0, literal_id)
                    else:
                        # Other generic types (Array, etc.): use annotation directly
                        init_type = stmt.type
                        # Set call_type so codegen knows the concrete template type
                        if is_generic_constructor:
                            stmt.init.call_type = stmt.type  # type: ignore
                    # Cache expr_type since we bypassed _analyze_expr
                    self.ctx.set_expr_type(stmt.init, init_type)
                else:
                    func_name = stmt.init.func if is_generic_constructor else "[]"
                    raise self.ctx.error(
                        f"{func_name} requires matching type annotation, got {stmt.type}", stmt
                    )
            else:
                init_type = self.expr.analyze_expr_with_hint(stmt.init, stmt.type)


            # Track list literal to variable mapping for mutation detection
            if isinstance(init_type, PendingListType):
                literal_id = init_type.literal_id
                self.ctx.variable_to_literal[stmt.name] = literal_id
                info = self.ctx.list_literals[literal_id]
                info.variable_name = stmt.name

                # If explicit annotation is provided, record it
                if stmt.type and isinstance(stmt.init, TpyArrayLiteral):
                    info.has_explicit_annotation = True
                    info.explicit_type = stmt.type

            if stmt.type:
                if existing_type is not None:
                    self.reassign.check_conflicting_annotation(
                        stmt.name,
                        stmt.type,
                        stmt,
                        new_line=(stmt.loc.line if stmt.loc else None),
                    )
                    ann_line = stmt.loc.line if stmt.loc else None
                    self.reassign.retro_validate_against_annotation(stmt.name, stmt.type, annotation_line=ann_line)
                # Special case: single-char string literal can be assigned to Char
                if (isinstance(stmt.type, CharType) and isinstance(init_type, StrType) and
                    isinstance(stmt.init, TpyStrLiteral) and len(stmt.init.value) == 1):
                    pass  # Allow str literal -> Char
                else:
                    # Unwrap ReadonlyType for coercion -- readonly is tracked
                    # via type deduction, not the compatibility check.
                    inner_init = unwrap_readonly(init_type)
                    stmt.init = self.compat.coerce_expr(stmt.init, inner_init, stmt.type,
                                                         f"variable '{stmt.name}'",
                                                         coercion_ctx=CoercionContext.INIT)
                var_type = stmt.type
                # Inherit ReadonlyType from init expression
                if isinstance(init_type, ReadonlyType) and not var_type.is_value_type():
                    var_type = ReadonlyType(var_type)
                self.reassign.set_authoritative_annotation(
                    stmt.name,
                    stmt.type,
                    line=(stmt.loc.line if stmt.loc else None),
                )
            elif existing_type:
                # Unwrap ReadonlyType for reassignment type resolution and
                # coercion -- this is a binding, not passing by reference.
                inner_existing = unwrap_readonly(existing_type)
                inner_init = unwrap_readonly(init_type)
                var_type = self.reassign.resolve_reassignment_target_type(
                    stmt.name, inner_existing, inner_init, init_expr=stmt.init
                )
                # Reassignment: check if we need to upgrade IntLiteralType
                if isinstance(inner_existing, IntLiteralType) and isinstance(var_type, (Int32Type, BigIntType)):
                    # Upgrade from IntLiteralType to concrete type
                    # Update var_types so codegen knows the resolved type
                    orig_decl = self.ctx.var_decl_by_name.get(stmt.name)
                    if orig_decl:
                        self.ctx.var_types[id(orig_decl)] = var_type
                else:
                    # Normal reassignment: use existing type, check compatibility
                    stmt.init = self.compat.coerce_expr(stmt.init, inner_init, var_type,
                                                         f"reassignment to '{stmt.name}'",
                                                         coercion_ctx=CoercionContext.ASSIGN)
                # Readonly status flows from the value expression
                if isinstance(init_type, ReadonlyType) and not var_type.is_value_type():
                    var_type = ReadonlyType(var_type)
                if var_type != existing_type:
                    # Keep original declaration's resolved type in sync for codegen.
                    orig_decl = self.ctx.var_decl_by_name.get(stmt.name)
                    if orig_decl:
                        self.ctx.var_types[id(orig_decl)] = unwrap_readonly(var_type)
            else:
                # New variable: resolve IntLiteralType.
                if isinstance(init_type, IntLiteralType):
                    var_type = self.ctx.default_int_for_literal(init_type, warn_node=stmt.init)
                    self.ctx.literal_default_vars.add(stmt.name)
                # Unwrap OwnType - Own[T] indicates ownership transfer, not variable type
                elif isinstance(init_type, OwnType):
                    var_type = init_type.wrapped
                # None literal without annotation -- can't infer the Optional type
                elif isinstance(init_type, NoneType):
                    var_type = init_type
                    self.ctx.unresolved_none_vars.add(stmt.name)
                else:
                    var_type = init_type
            # Track inferred writes for potential future retro-validation.
            self.reassign.record_write(stmt.name, stmt.init, init_type)
        elif stmt.type:
            if isinstance(stmt.type, OptionalType):
                # Optional without initializer is allowed (defaults to None/nullptr)
                var_type = stmt.type
            elif not stmt.type.is_value_type():
                raise self.ctx.error(
                    f"Variable '{stmt.name}' of type '{stmt.type}' must have an initializer",
                    stmt
                )
            else:
                var_type = stmt.type
            self.reassign.set_authoritative_annotation(
                stmt.name,
                stmt.type,
                line=(stmt.loc.line if stmt.loc else None),
            )
        else:
            raise self.ctx.error(f"Variable '{stmt.name}' has no type annotation and no initializer", stmt)

        if is_global_declared:
            # Update global scope type; bind in current scope for local reads
            self.ctx.global_scope.define(stmt.name, var_type)
            self.ctx.current_scope.define(stmt.name, var_type)
        else:
            self.ctx.current_scope.define(stmt.name, var_type)
        # Any write to the root invalidates identity facts rooted at that variable.
        self.narrowing.kill_facts_rooted_at(stmt.name)
        if stmt.init and self.narrowing.expr_has_unknown_call(stmt.init):
            self.ctx.non_none_exprs.clear()
        if stmt.init:
            self.init.mark_assigned(stmt.name)
        self.narrowing.update_after_write(stmt.name, var_type, init_type if stmt.init else None, stmt.init)
        # Record scope depth for new variables (not reassignments of outer-scope
        # vars). Uses scope lookup rather than var_scope_depth existence, so that
        # stale entries from discarded inner scopes get overwritten correctly.
        #
        # Note: when an outer-scoped variable is reassigned with an rvalue inside
        # an inner scope (e.g. `p = Point()` in a loop body where `p` was declared
        # outside), the depth stays at the outer scope. This is safe because the
        # codegen uses a rebind slot at the declaration scope for rvalue rebinds.
        if existing_type is None:
            self.ctx.var_scope_depth[stmt.name] = self.ctx.current_scope.depth
        # Update rvalue status for hoist eligibility (both new vars and reassignments)
        if stmt.init:
            if self.compat.is_lvalue(stmt.init):
                self.ctx.rvalue_vars.discard(stmt.name)
            else:
                self.ctx.rvalue_vars.add(stmt.name)
        # Track provenance for non-value types and pointer types
        # (pointers are value types but carry address provenance)
        if stmt.init and (not var_type.is_value_type() or isinstance(var_type, (PtrType, ConstPtrType))):
            self.init.mark_provenance(stmt.name, self.compat.is_param_derived_expr(stmt.init))
        # Track non-null pointer provenance for null-check elision
        if stmt.init and isinstance(var_type, (PtrType, ConstPtrType)):
            # Unwrap coercion (e.g. Ptr[T] -> ConstPtr[T]) to find the source expression
            init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
            is_non_null = (isinstance(init_inner, TpyCall)
                           and init_inner.call_type is not None
                           and init_inner.call_type.is_pointer()
                           and len(init_inner.args) > 0)
            if not is_non_null and isinstance(init_inner, TpyName):
                is_non_null = init_inner.name in self.ctx.non_null_ptr_vars
            self.init.mark_non_null_ptr(stmt.name, is_non_null)

        # Scope escape check for variable declarations (new and reassignment)
        if stmt.init and not var_type.is_value_type():
            self.scopes.check_escape(stmt.name, stmt.init, stmt)
        if self.ctx.current_ns:
            self.ctx.current_ns.bind_variable(stmt.name, var_type)
        # Track top-level declarations with line number for order-aware codegen
        # Use earliest declaration line (min) so uses between redeclarations work
        if self.ctx.is_top_level:
            decl_line = stmt.loc.line if stmt.loc else 0
            if stmt.name not in self.ctx.top_level_decls:
                self.ctx.top_level_decls[stmt.name] = decl_line
            else:
                self.ctx.top_level_decls[stmt.name] = min(self.ctx.top_level_decls[stmt.name], decl_line)
        # Track first var_decl for later type updates on reassignment-driven inference.
        if existing_type is None:
            self.ctx.var_decl_by_name[stmt.name] = stmt

    def _analyze_assign(self, stmt: TpyAssign) -> None:
        """Analyze an assignment."""
        target_type = self.expr.analyze_expr(stmt.target)
        value_type = self.expr.analyze_expr(stmt.value)
        self._enforce_readonly_assignment_target(stmt.target)
        value_has_unknown_call = self.narrowing.expr_has_unknown_call(stmt.value)
        if isinstance(stmt.target, (TpyFieldAccess, TpySubscript)):
            declared_target_type = self.narrowing.declared_type_for_expr(stmt.target)
            if declared_target_type is not None:
                target_type = declared_target_type
        if isinstance(stmt.target, TpyName):
            # Match var-decl flow: reject forbidden rebinding before any type mutation.
            self._check_nonvalue_rebinding(stmt.target.name, stmt)
            declared_target_type = self.ctx.current_scope.lookup(stmt.target.name)
            if declared_target_type is not None:
                target_type = declared_target_type
            # Unwrap ReadonlyType for reassignment type resolution -- this is a
            # binding, not passing by reference.
            inner_target = unwrap_readonly(target_type)
            inner_value = unwrap_readonly(value_type)
            target_type = self.reassign.resolve_reassignment_target_type(
                stmt.target.name, inner_target, inner_value, init_expr=stmt.value
            )
            # Readonly status flows from the value expression
            if isinstance(value_type, ReadonlyType) and not target_type.is_value_type():
                target_type = ReadonlyType(target_type)
            self.ctx.current_scope.define(stmt.target.name, target_type)
            if self.ctx.current_ns:
                self.ctx.current_ns.update_variable_type(stmt.target.name, target_type)
            self.ctx.set_expr_type(stmt.target, target_type)
            self.reassign.record_write(stmt.target.name, stmt.value, inner_value)
            var_decl = self.ctx.var_decl_by_name.get(stmt.target.name)
            if var_decl:
                self.ctx.var_types[id(var_decl)] = unwrap_readonly(target_type)

        # Disallow reassignment of non-value-type params and loop vars
        if isinstance(stmt.target, TpyName):
            # Update rvalue status for hoist eligibility
            if self.compat.is_lvalue(stmt.value):
                self.ctx.rvalue_vars.discard(stmt.target.name)
            else:
                self.ctx.rvalue_vars.add(stmt.target.name)

        # Prevent assignment to read-only types via MutableSequence protocol check
        if isinstance(stmt.target, TpySubscript):
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            elem_type = obj_type.get_element_type()
            if elem_type is not None:
                # Check if type conforms to MutableSequence[elem_type]
                mutable_seq = NamedType("MutableSequence", (elem_type,), is_protocol=True)
                if not self.protocols.type_conforms_to_protocol(obj_type, mutable_seq):
                    raise self.ctx.error(f"Cannot assign to elements of {obj_type} (read-only)", stmt)

        # Prevent assignment through ConstPtr (read-only pointer)
        if isinstance(stmt.target, TpyName):
            pass  # TpyName targets are fine
        else:
            if isinstance(stmt.target, TpyFieldAccess):
                obj_type = self.ctx.get_expr_type(stmt.target.obj)
                if isinstance(obj_type, ConstPtrType):
                    raise self.ctx.error("Cannot assign through ConstPtr (read-only pointer)", stmt)

        stmt.value = self.compat.coerce_expr(stmt.value, value_type, target_type, "assignment",
                                              coercion_ctx=CoercionContext.ASSIGN)
        if value_has_unknown_call:
            self.ctx.non_none_exprs.clear()

        if isinstance(stmt.target, TpyFieldAccess):
            if self.compat.needs_copy_warning(stmt.value, target_type):
                if isinstance(target_type, TypeParamRef):
                    msg = f"may copy {target_type} into field if not a value type; use copy() to make this explicit"
                else:
                    msg = f"copies {value_type} into field; use copy() to make this explicit"
                self.ctx.warning(msg, stmt)

        if isinstance(stmt.target, TpySubscript):
            if self.compat.needs_copy_warning(stmt.value, target_type):
                if isinstance(target_type, TypeParamRef):
                    msg = f"may copy {target_type} into container if not a value type; use copy() to make this explicit"
                else:
                    msg = f"copies {value_type} into container; use copy() to make this explicit"
                self.ctx.warning(msg, stmt)

        # Scope escape check for assignments to named variables
        if isinstance(stmt.target, TpyName) and not target_type.is_value_type():
            self.scopes.check_escape(stmt.target.name, stmt.value, stmt)

        # Track provenance for non-value-type and pointer-type name targets
        if isinstance(stmt.target, TpyName) and (not target_type.is_value_type() or isinstance(target_type, (PtrType, ConstPtrType))):
            self.init.mark_provenance(stmt.target.name, self.compat.is_param_derived_expr(stmt.value))
        # Track non-null pointer provenance for null-check elision
        if isinstance(stmt.target, TpyName) and isinstance(target_type, (PtrType, ConstPtrType)):
            # Unwrap coercion (e.g. Ptr[T] -> ConstPtr[T]) to find the source expression
            val_inner = stmt.value.expr if isinstance(stmt.value, TpyCoerce) else stmt.value
            is_non_null = (isinstance(val_inner, TpyCall)
                           and val_inner.call_type is not None
                           and val_inner.call_type.is_pointer()
                           and len(val_inner.args) > 0)
            if not is_non_null and isinstance(val_inner, TpyName):
                is_non_null = val_inner.name in self.ctx.non_null_ptr_vars
            self.init.mark_non_null_ptr(stmt.target.name, is_non_null)

        # Mark as definitely assigned for plain name targets
        if isinstance(stmt.target, TpyName):
            self.narrowing.kill_facts_rooted_at(stmt.target.name)
            self.init.mark_assigned(stmt.target.name)
            self.narrowing.update_after_write(stmt.target.name, target_type, value_type, stmt.value)
        else:
            self.narrowing.kill_facts_for_target(stmt.target)

    def _analyze_aug_assign(self, stmt: TpyAugAssign) -> None:
        """Analyze an augmented assignment (+=, -=, etc.)."""
        from .operators import OperatorResolver
        target_type = self.expr.analyze_expr(stmt.target)
        value_type = self.expr.analyze_expr(stmt.value)
        self._enforce_readonly_assignment_target(stmt.target)
        self.narrowing.kill_facts_for_target(stmt.target)
        if (
            isinstance(stmt.target, TpyName)
            and isinstance(target_type, BigIntType)
            and isinstance(value_type, Int32Type)
            and stmt.target.name in self.ctx.literal_default_vars
        ):
            type_name = str(value_type)
            self.ctx.warning(
                f"Augmented assignment does not narrow '{stmt.target.name}' from int to {type_name}; "
                f"variable remains int (BigInt). Annotate or initialize '{stmt.target.name}' as {type_name} "
                f"to keep {type_name} arithmetic.",
                stmt,
            )
        # Both must be numeric types for arithmetic augmented assignment
        if not isinstance(target_type, (Int32Type, BigIntType, IntLiteralType, FloatType)):
            raise self.ctx.error(
                f"Augmented assignment target must be a numeric type, got {target_type}",
                stmt,
            )
        if not isinstance(value_type, (Int32Type, BigIntType, IntLiteralType, FloatType)):
            raise self.ctx.error(
                f"Augmented assignment value must be a numeric type, got {value_type}",
                stmt,
            )
        # Special case: FixedInt += BigInt should use the target's ops (value gets converted)
        # This preserves checked arithmetic and avoids unnecessary promotion to BigInt
        resolve_value_type = value_type
        if isinstance(target_type, Int32Type) and isinstance(value_type, BigIntType):
            resolve_value_type = target_type
        # Resolve the binary operation for codegen
        operators = OperatorResolver(self.ctx)
        if result := operators.resolve_binop(target_type, stmt.op, resolve_value_type):
            stmt.resolved_binop = result
