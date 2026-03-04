"""
TurboPython Statement Analysis

Statement analysis including variable declarations, assignments, and control flow.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType, OwnType, ReadonlyType,
    FinalType, FixedIntType, BoolType, StrViewType, StringType,
    ListType, DictType, ArrayType, SpanType, PendingListType, PendingStrType, NamedType, CharType, StrType, TypeParamRef,
    ListLiteralInfo, StrVarInfo, PtrType, is_readonly_ptr, NoneType, OptionalType, UnionType,
    EnumType, unwrap_readonly, is_any_str_type, TupleType,
    INT32, VOID, BIGINT, STRVIEW, is_protocol_type,
)
from ..parse import (
    TpyExpr,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyExprStmt, TpyReturn,
    TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue, TpyAssert, TpyRaiseStopIteration,
    TpyGlobal,
    TpyCall, TpyMethodCall, TpyArrayLiteral, TpyDictLiteral, TpyCoerce,
    TpySubscript, TpyStrLiteral, TpyName, TpyTupleLiteral,
    TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyUnaryOp,
    TpyFieldAccess, TpyFunction, TupleElemCapture,
)
from ..coercions import CoercionContext
from ..namespace import BindingKind
from ..parse.nodes import VarLinkage
from .diagnostics import SemanticError
from .narrowing import NarrowingTracker
from .scope_tracker import ScopeTracker
from .init_tracker import InitTracker
if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .compatibility import TypeCompatibility
    from .local_deduction import LocalTypeDeduction
    from .list_literals import IterableHelper
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
        deduction: LocalTypeDeduction,
        iterable: IterableHelper,
        protocols: ProtocolChecker,
        narrowing: NarrowingTracker,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.compat = compat
        self.deduction = deduction
        self.iterable = iterable
        self.protocols = protocols
        self.narrowing = narrowing
        self.scopes = ScopeTracker(ctx, compat)
        self.init = InitTracker(ctx)
        # Set via set_cross_deps() to break circular dependency
        self.expr: ExpressionAnalyzer | None = None

    def set_cross_deps(self, expr: ExpressionAnalyzer) -> None:
        """Wire circular dependencies (must be called before analyze_stmt)."""
        self.expr = expr

    def _warn_all_caps_without_final(self, name: str, type_hint: str, node: TpyStmt) -> None:
        """Warn on ALL_CAPS module-level variables without Final annotation."""
        if (not name.startswith("_")
                and name.replace("_", "").isalpha()
                and name == name.upper()
                and len(name) >= 2):
            self.ctx.warning(
                f"ALL_CAPS variable '{name}' without Final annotation; "
                f"use Final[{type_hint}] if this is a constant",
                node
            )

    def _warn_unnecessary_return_copy(self, value: TpyExpr) -> None:
        """Warn when return copy(x) is used but x is at last use (auto-move suffices)."""
        if not (isinstance(value, TpyCall) and len(value.args) == 1
                and value.resolved_function_info
                and value.resolved_function_info.qualified_name == "tpy.copy"):
            return
        inner = value.args[0]
        if (isinstance(inner, TpyName)
                and id(inner) in self.ctx.all_last_uses
                and self.compat._is_movable_var(inner.name)):
            self.ctx.warning(
                f"unnecessary copy() -- '{inner.name}' is at its last use and would be moved automatically",
                value,
            )

    def _check_own_lvalue_return(self, own_type: OwnType, expr: TpyExpr, context: str) -> None:
        """Check that an lvalue returned as Own[T] has explicit copy() or is auto-moved.

        Args:
            own_type: The Own[T] type being returned into.
            expr: The expression being returned.
            context: Description for error messages, e.g. "return type" or "tuple element 1".
        """
        if self.compat.is_copy_call(expr):
            return
        if not self.compat.is_lvalue(expr):
            return
        is_auto_moved = (isinstance(expr, TpyName)
                         and id(expr) in self.ctx.all_last_uses
                         and self.compat._is_movable_var(expr.name))
        if is_auto_moved:
            return
        expr_type = self.ctx.get_expr_type(expr)
        is_nocopy = expr_type is not None and self.ctx.is_type_nocopy(expr_type)
        if is_nocopy:
            reason = self.ctx.nocopy_reason(expr_type)
            is_movable = (isinstance(expr, TpyName)
                          and self.compat._is_movable_var(expr.name))
            if is_movable:
                raise self.ctx.error(
                    f"{reason} is used after this point "
                    f"and cannot be moved into {context} Own[{own_type.wrapped}]. "
                    f"Remove later uses or restructure the code.",
                    expr
                )
            raise self.ctx.error(
                f"{reason} cannot be returned as "
                f"{context} Own[{own_type.wrapped}]. "
                f"Only the original owner can be moved at its last use.",
                expr
            )
        raise self.ctx.error(
            f"Cannot return lvalue as {context} Own[{own_type.wrapped}] without explicit copy(). "
            f"Use 'copy(...)' instead.",
            expr
        )

    def _is_in_constructor(self) -> bool:
        """Check if currently analyzing an __init__ method body."""
        func = self.ctx.current_function
        return (func is not None
                and getattr(func, 'name', None) == "__init__"
                and getattr(func, 'is_method', False))

    def _annotate_tuple_elem_capture(
        self, literal: TpyTupleLiteral, tuple_type: TupleType,
        *, is_return: bool = False, is_field: bool = False
    ) -> None:
        """Annotate each element of a tuple literal with its capture mode.

        Args:
            literal: The tuple literal AST node to annotate.
            tuple_type: The resolved TupleType for the literal.
            is_return: True if this literal is in a return statement.
            is_field: True if this literal is assigned to a class field.
        """
        V = TupleElemCapture.VALUE
        R = TupleElemCapture.REF
        CR = TupleElemCapture.CONST_REF

        is_readonly = (self.ctx.current_function is not None
                       and getattr(self.ctx.current_function, 'is_readonly', False))

        literal.elem_capture = []
        for i, et in enumerate(tuple_type.element_types):
            if i >= len(literal.elements):
                literal.elem_capture.append(V)
                continue
            elem = literal.elements[i]

            # Value types, Own[T], and TypeParamRef are always VALUE
            if et.is_value_type() or isinstance(et, (OwnType, TypeParamRef)):
                literal.elem_capture.append(V)
                continue

            # Field context: all reference-type elements are owned (VALUE)
            # Warn if not an explicit copy() -- same as scalar field assignment
            if is_field:
                if self.compat.needs_copy_warning(elem, et):
                    self.ctx.warning(
                        f"copies {et} into field (tuple element {i}); "
                        f"use copy() to make this explicit",
                        elem
                    )
                literal.elem_capture.append(V)
                continue

            # Return context
            if is_return:
                if self.compat.is_dangling_return(elem):
                    # Will error separately in check_dangling_reference
                    literal.elem_capture.append(V)
                elif self.compat.is_const_ref_source(elem):
                    if is_readonly:
                        literal.elem_capture.append(CR)
                    else:
                        raise self.ctx.error(
                            f"Cannot return readonly source as tuple element {i}. "
                            f"Type '{et}' would be returned by mutable reference, "
                            f"but the source is readonly. "
                            f"Use Own[{et}] with copy() to return by value.",
                            elem
                        )
                elif is_readonly:
                    literal.elem_capture.append(CR)
                else:
                    literal.elem_capture.append(R)
                continue

            # Local context: is_const_ref_source handles ReadonlyType
            # (including constructor params which are typed as ReadonlyType)
            if not self.compat.is_lvalue(elem):
                literal.elem_capture.append(V)
            elif self.compat.is_const_ref_source(elem):
                literal.elem_capture.append(CR)
            else:
                literal.elem_capture.append(R)

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

    def _sync_promoted_var_types(self, names: set[str] | None = None) -> None:
        """Sync scope and namespace with var_types after a control-flow restore.

        After restoring scope/namespace to a pre-block state (if-branch,
        while, for-each), variables whose canonical declaration type was
        widened inside the block (e.g., Int32 promoted to BigInt, or None
        promoted to Optional[T]) need to be re-synced so that subsequent
        analysis sees the correct type.

        Args:
            names: Variable names to check. If None, checks all tracked
                   variable declarations in the current function.
        """
        items = (
            self.ctx.var_decl_by_name.items() if names is None
            else ((n, self.ctx.var_decl_by_name[n]) for n in names if n in self.ctx.var_decl_by_name)
        )
        for name, var_decl in items:
            canonical = self.ctx.var_types.get(id(var_decl))
            if canonical is None:
                continue
            current = self.ctx.current_scope.lookup(name)
            # Preserve ReadonlyType from branch merge: var_types stores
            # unwrapped types, so re-wrap with ReadonlyType if the merge
            # determined this variable should be readonly.
            if isinstance(current, ReadonlyType):
                if unwrap_readonly(current) == canonical:
                    continue
                target = ReadonlyType(canonical)
            else:
                target = canonical
            if target != current:
                self.ctx.current_scope.define(name, target)
            self._sync_ns_var_type(name, target)

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

    def _resolve_enum_iterable(self, stmt: TpyForEach) -> EnumType | None:
        """Check if for-each iterates over an enum type (e.g. `for c in Color`).

        Returns the EnumType if so, None otherwise.
        """
        iterable = stmt.iterable
        if not isinstance(iterable, TpyName):
            return None
        binding = self.ctx.current_ns.lookup(iterable.name) if self.ctx.current_ns else None
        if binding is None:
            return None
        if binding.kind == BindingKind.ENUM and binding.enum_type is not None:
            return binding.enum_type
        if binding.kind == BindingKind.IMPORTED_NAME and binding.import_source:
            src_mod, original_name = binding.import_source
            enum_type = self.ctx.registry.get_enum(original_name)
            if enum_type is not None:
                return enum_type
        return None

    def analyze_stmt(self, stmt: TpyStmt) -> None:
        """Analyze a statement."""
        if isinstance(stmt, TpyVarDecl):
            self._analyze_var_decl(stmt)
        elif isinstance(stmt, TpyTupleUnpack):
            self._analyze_tuple_unpack(stmt)
        elif isinstance(stmt, TpyAssign):
            self._analyze_assign(stmt)
        elif isinstance(stmt, TpyAugAssign):
            self._analyze_aug_assign(stmt)
        elif isinstance(stmt, TpyDelItem):
            self._analyze_del_item(stmt)
        elif isinstance(stmt, TpyExprStmt):
            self.expr.analyze_expr(stmt.expr)
        elif isinstance(stmt, TpyReturn):
            if stmt.value:
                expected = self.ctx.current_function.return_type if self.ctx.current_function else VOID
                ret_type = self.expr.analyze_expr_with_hint(stmt.value, expected)
                stmt.value = self.compat.coerce_expr(stmt.value, ret_type, expected, "return value",
                                                      coercion_ctx=CoercionContext.RETURN, is_return=True)
                # Track return-type context for pending list deduction
                self.deduction.mark_list_return_context(stmt.value, expected)
                # Check for lvalue returned as Own[T] without explicit copy()
                if isinstance(expected, OwnType):
                    if self.compat.is_copy_call(stmt.value):
                        self._warn_unnecessary_return_copy(stmt.value)
                    else:
                        self._check_own_lvalue_return(expected, stmt.value, "return type")
                # Check Own[T] elements in tuple literals
                if (isinstance(expected, TupleType)
                        and isinstance(stmt.value, TpyTupleLiteral)):
                    for i, et in enumerate(expected.element_types):
                        if isinstance(et, OwnType) and i < len(stmt.value.elements):
                            self._check_own_lvalue_return(et, stmt.value.elements[i],
                                                          f"tuple element {i}")
                    # Annotate per-element capture mode (ref/value/const_ref)
                    self._annotate_tuple_elem_capture(
                        stmt.value, expected, is_return=True)
                # Check for dangling reference (returning local/temporary as reference)
                self.compat.check_dangling_reference(stmt.value, expected, stmt.loc)
            self.init.mark_terminated()
        elif isinstance(stmt, TpyIf):
            self.expr.analyze_expr(stmt.condition)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            then_type_facts, else_type_facts = self.narrowing.condition_type_facts(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(then_type_facts)
            stmt.else_type_facts = self._filter_union_codegen_facts(else_type_facts)
            scope_before = set(self.ctx.current_scope.bindings.keys())
            assigned_before = frozenset(self.ctx.definitely_assigned)
            before = self.init.save()
            # Save binding types for ReadonlyType merge after branches
            bindings_before = dict(self.ctx.current_scope.bindings)
            ns_types_before = self._save_ns_var_types()
            self.ctx.narrowed_types.update(then_type_facts)
            for s in stmt.then_body:
                self.analyze_stmt(s)
            then_state = self.init.save()
            bindings_after_then = dict(self.ctx.current_scope.bindings)
            # Restore bindings for else branch
            self.ctx.current_scope.bindings.update(bindings_before)
            self._restore_ns_var_types(ns_types_before)
            self.init.restore(before)
            self.ctx.narrowed_types.update(else_type_facts)
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
            # Sync scope/namespace with var_types for variables whose
            # declaration type was promoted inside a branch.
            self._sync_promoted_var_types(
                set(bindings_after_then) | set(bindings_after_else)
            )
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
            then_type_facts, _ = self.narrowing.condition_type_facts(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(then_type_facts)
            before = self.init.save()
            # Save namespace types -- loop_scope() restores scope bindings
            # automatically, but namespace mutations inside the loop persist.
            ns_types_before_while = self._save_ns_var_types()
            with self.scopes.loop_scope():
                self.init.apply_loop_entry_facts(
                    before,
                    condition_type_facts=then_type_facts,
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
            self._sync_promoted_var_types()
        elif isinstance(stmt, TpyForEach):
            # Check for enum iteration: `for c in Color`
            enum_type = self._resolve_enum_iterable(stmt)
            if enum_type is not None:
                stmt.enum_iterable = enum_type
                elem_type = enum_type
                before = self.init.save()
                ns_types_before_foreach = self._save_ns_var_types()
                with self.scopes.loop_scope() as inner_scope:
                    self.init.apply_loop_entry_facts(before)
                    with self.scopes.loop_var(inner_scope, stmt.var, elem_type, inner_scope.depth, is_foreach=True):
                        for s in stmt.body:
                            self.analyze_stmt(s)
                body_end_nn_ptr = frozenset(self.ctx.non_null_ptr_vars)
                self.init.restore(before)
                self.ctx.non_null_ptr_vars &= body_end_nn_ptr
                self._restore_ns_var_types(ns_types_before_foreach)
                self._sync_promoted_var_types()
            else:
                iterable_type = self.expr.analyze_expr(stmt.iterable)
                is_readonly_iterable = isinstance(iterable_type, ReadonlyType)
                inner_iterable_type = unwrap_readonly(iterable_type)
                # Resolve TypeParamRef to its bound for element type extraction
                resolved_for_iter = inner_iterable_type
                if isinstance(inner_iterable_type, TypeParamRef):
                    bound = self.type_ops.get_type_param_bound(inner_iterable_type.name)
                    if bound is not None and is_protocol_type(bound):
                        resolved_for_iter = bound
                elem_type = self.iterable.get_iterable_element_type(resolved_for_iter, loc=stmt.loc)
                # Elements from a readonly iterable inherit readonly status
                if is_readonly_iterable and not elem_type.is_value_type():
                    elem_type = ReadonlyType(unwrap_readonly(elem_type))
                before = self.init.save()
                ns_types_before_foreach = self._save_ns_var_types()
                with self.scopes.loop_scope() as inner_scope:
                    self.init.apply_loop_entry_facts(before)
                    # OptIterator and __iter__-based types produce fresh values each iteration
                    is_native_iterator = builtin_modules.get_native_iterator_element_type(inner_iterable_type, registry=self.ctx.registry) is not None
                    is_span_based = builtin_modules.get_span_element_type(inner_iterable_type, registry=self.ctx.registry) is not None
                    is_iter_based = builtin_modules.get_iter_element_type(inner_iterable_type, registry=self.ctx.registry) is not None
                    is_protocol_iter = is_protocol_type(resolved_for_iter) and resolved_for_iter.name in ("Iterator", "Iterable")
                    is_protocol_span = is_protocol_type(resolved_for_iter) and resolved_for_iter.name == "ReadOnlySpanLike"
                    if is_span_based or is_protocol_span:
                        # __span__ returns a view into the container's storage
                        if self.compat.is_lvalue(stmt.iterable):
                            iter_depth = self.scopes.get_expr_scope_depth(stmt.iterable)
                        else:
                            iter_depth = inner_scope.depth
                    elif is_native_iterator or is_iter_based or is_protocol_iter:
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
                self._sync_promoted_var_types()
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
            then_type_facts, _ = self.narrowing.condition_type_facts(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(then_type_facts)
            self.ctx.narrowed_types.update(then_type_facts)
        elif isinstance(stmt, TpyGlobal):
            self._analyze_global_stmt(stmt)
        elif isinstance(stmt, TpyRaiseStopIteration):
            func = self.ctx.current_function
            if not isinstance(func, TpyFunction) or func.name != "__next__":
                raise self.ctx.error("'raise StopIteration' can only be used inside a __next__ method", stmt)
            self.init.mark_terminated()

    def _filter_union_codegen_facts(
        self, facts: dict[str, TpyType],
    ) -> dict[str, TpyType]:
        """Keep only union-origin narrowing facts for codegen.

        Optional narrowing is handled implicitly by std::optional in C++,
        so only UnionType variables need explicit std::get<T> extraction.
        """
        return {
            name: ty for name, ty in facts.items()
            if isinstance(unwrap_readonly(self.narrowing.declared_type_for_name(name)), UnionType)
        }

    def _analyze_global_stmt(self, stmt: TpyGlobal) -> None:
        """Analyze a `global x, y` statement."""
        from .context import MODULE_INIT_CONTEXT
        # Must be inside a function, not at module level
        if self.ctx.is_top_level or isinstance(self.ctx.current_function, type(MODULE_INIT_CONTEXT)):
            raise self.ctx.error("'global' declaration is only allowed inside a function", stmt)
        for name in stmt.names:
            # Cannot use 'global' with Final variables
            if name in self.ctx.final_globals:
                raise self.ctx.error(
                    f"Cannot use 'global' with Final variable '{name}'", stmt)
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

    def _is_constant_expr(self, expr: TpyExpr) -> bool:
        """Check if an expression is a compile-time constant for Final globals.

        Accepts: literals, unary ops on literals (e.g. -42, not True),
        references to other Final globals.
        Does not accept arithmetic (checked ops aren't constexpr).
        """
        if isinstance(expr, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyStrLiteral)):
            return True
        if isinstance(expr, TpyUnaryOp):
            return self._is_constant_expr(expr.operand)
        if isinstance(expr, TpyName) and expr.name in self.ctx.analyzed_finals:
            return True
        return False

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
        # Resolve type aliases in annotation (for cross-module imported aliases)
        # Recursive to handle nested types like list[Shape], Optional[Shape]
        if stmt.type and self.ctx.registry.type_aliases:
            def _resolve(t: TpyType) -> TpyType:
                if isinstance(t, NamedType) and not t.is_protocol and not t.is_module_type:
                    alias = self.ctx.registry.get_type_alias(t.name)
                    if alias is not None:
                        return alias
                return t.map_inner_types(_resolve)
            stmt.type = _resolve(stmt.type)

        # Resolve enum types in annotation (NamedType -> EnumType).
        # Also replaces stale EnumType from the parser registry with the
        # sema registry's version (which may be IntEnumType).
        if stmt.type and self.ctx.registry.enums:
            def _resolve_enum(t: TpyType) -> TpyType:
                if isinstance(t, NamedType) and not t.is_protocol:
                    enum = self.ctx.registry.get_enum(t.name)
                    if enum is not None:
                        return enum
                if isinstance(t, EnumType):
                    enum = self.ctx.registry.get_enum(t.name)
                    if enum is not None:
                        return enum
                return t.map_inner_types(_resolve_enum)
            stmt.type = _resolve_enum(stmt.type)

        # Resolve type to set is_protocol flag on imported protocol NamedTypes
        if stmt.type:
            stmt.type = self.type_ops.resolve_type(stmt.type)

        # Validate the type annotation if present
        # Allow TypeParamRef inside generic functions or generic record methods
        if stmt.type:
            try:
                in_generic = bool(
                    (isinstance(self.ctx.current_function, TpyFunction) and self.ctx.current_function.type_params)
                    or self.ctx.record_ctx.type_params
                )
                self.type_ops.validate_type(stmt.type, allow_type_param_ref=in_generic, loc=stmt.loc, allow_forward_ref=False)
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

        # Final[T] validation
        # Detect FinalType from annotation (covers function-level where register_globals didn't run)
        if stmt.type and isinstance(stmt.type, FinalType):
            stmt.type = stmt.type.wrapped
            if isinstance(stmt.type, StrType):
                stmt.type = STRVIEW
            stmt.is_final = True
        if stmt.is_final:
            if not self.ctx.is_top_level:
                raise self.ctx.error(
                    f"Final can only be used at module level",
                    stmt
                )
            if stmt.name in self.ctx.analyzed_finals:
                raise self.ctx.error(
                    f"Cannot re-declare Final variable '{stmt.name}'",
                    stmt
                )
            if not stmt.init:
                raise self.ctx.error(
                    f"Final variable '{stmt.name}' must have an initializer",
                    stmt
                )
            inner = stmt.type
            if not isinstance(inner, (FixedIntType, BigIntType, FloatType, BoolType, StrViewType, CharType)):
                raise self.ctx.error(
                    f"Final[{inner}] is not supported; "
                    f"only primitive types (int, float, bool, str, StrView, Char, IntN) are allowed",
                    stmt
                )
            if not self._is_constant_expr(stmt.init):
                # Give a specific hint for imported names
                if (isinstance(stmt.init, TpyName)
                        and stmt.init.name in self.ctx.user_imported_variables):
                    src_mod, _ = self.ctx.user_imported_variables[stmt.init.name]
                    raise self.ctx.error(
                        f"Final variable '{stmt.name}' requires a compile-time constant initializer; "
                        f"cross-module Final references are not yet supported "
                        f"('{stmt.init.name}' is imported from '{src_mod}')",
                        stmt
                    )
                raise self.ctx.error(
                    f"Final variable '{stmt.name}' requires a compile-time constant initializer",
                    stmt
                )
            self.ctx.analyzed_finals.add(stmt.name)

        if self.ctx.is_top_level and not stmt.is_final:
            type_hint = str(stmt.type) if stmt.type else "<type>"
            self._warn_all_caps_without_final(stmt.name, type_hint, stmt)

        # Protocol types can only be used for function parameters, not variables
        # Exception: @dynamic protocols can be used as variable types
        if stmt.type and is_protocol_type(stmt.type):
            protocol_info = self.ctx.registry.get_protocol(stmt.type.name)
            if not protocol_info or not protocol_info.is_dynamic:
                raise self.ctx.error(
                    f"Protocol type '{stmt.type.name}' cannot be used as a variable type. "
                    f"Only @dynamic protocols can be used as variable types",
                    stmt
                )

        # Detect native global import: x: T = native_c_global("name") / native_global("name")
        if isinstance(stmt.init, TpyCall) and self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(stmt.init.func)
            if (binding and binding.kind == BindingKind.IMPORTED_NAME
                    and binding.import_source
                    and binding.import_source[0] == "tpy.extern"
                    and binding.import_source[1] in ("native_c_global", "native_global", "native_c_global_array")):
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
                if func_name == "native_c_global":
                    stmt.linkage = VarLinkage.NATIVE_C
                elif func_name == "native_c_global_array":
                    stmt.linkage = VarLinkage.NATIVE_C_ARRAY
                else:
                    stmt.linkage = VarLinkage.NATIVE
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

        # Block reassignment of Final globals at module level
        # (inside functions, local shadowing is allowed)
        if self.ctx.is_top_level and not stmt.is_final and stmt.name in self.ctx.final_globals:
            raise self.ctx.error(
                f"Cannot reassign Final variable '{stmt.name}'",
                stmt
            )

        # Disallow reassignment of non-value-type params and loop vars
        if existing_type is not None:
            self._check_nonvalue_rebinding(stmt.name, stmt)

        if stmt.init:
            # Handle empty list literal or generic type constructor with explicit type annotation
            # Note: [] * N is collapsed to [] in the parser
            is_empty_literal = isinstance(stmt.init, TpyArrayLiteral) and not stmt.init.elements
            is_empty_dict_literal = isinstance(stmt.init, TpyDictLiteral) and not stmt.init.keys
            is_generic_constructor = (isinstance(stmt.init, TpyCall) and
                                      not stmt.init.args and
                                      stmt.init.call_type is None and
                                      builtin_modules.lookup_generic_type(stmt.init.func) is not None)

            # Empty dict literal with annotation: d: dict[K, V] = {}
            if is_empty_dict_literal and stmt.type:
                if isinstance(stmt.type, DictType):
                    init_type = stmt.type
                    self.ctx.set_expr_type(stmt.init, init_type)
                else:
                    raise self.ctx.error(
                        f"Empty dict literal requires dict type annotation, got {stmt.type}",
                        stmt,
                    )
            elif (is_empty_literal or is_generic_constructor) and stmt.type:
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
                # Use annotation as hint, or existing type for reassignments
                type_hint = stmt.type if stmt.type else existing_type
                init_type = self.expr.analyze_expr_with_hint(stmt.init, type_hint)


            # Track list literal to variable mapping for mutation detection.
            # Only bind when the init is an actual list literal or empty constructor,
            # not a name reference (aliases are handled by register_list_alias).
            if isinstance(init_type, PendingListType) and not isinstance(stmt.init, TpyName):
                literal_id = init_type.literal_id
                self.ctx.variable_to_literal[stmt.name] = literal_id
                info = self.ctx.list_literals[literal_id]
                info.variable_name = stmt.name
                info.decl_line = stmt.loc.line if stmt.loc else None

                # If explicit annotation is provided, record it
                if stmt.type and isinstance(stmt.init, TpyArrayLiteral):
                    info.has_explicit_annotation = True
                    info.explicit_type = stmt.type

            if stmt.type:
                if existing_type is not None:
                    self.deduction.check_conflicting_annotation(
                        stmt.name,
                        stmt.type,
                        stmt,
                        new_line=(stmt.loc.line if stmt.loc else None),
                    )
                    ann_line = stmt.loc.line if stmt.loc else None
                    self.deduction.retro_validate_against_annotation(stmt.name, stmt.type, annotation_line=ann_line)
                # Special case: single-char string literal can be assigned to Char
                if (isinstance(stmt.type, CharType) and is_any_str_type(init_type) and
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
                self.deduction.set_authoritative_annotation(
                    stmt.name,
                    stmt.type,
                    line=(stmt.loc.line if stmt.loc else None),
                )
            elif existing_type:
                # Unwrap ReadonlyType for reassignment type resolution and
                # coercion -- this is a binding, not passing by reference.
                inner_existing = unwrap_readonly(existing_type)
                inner_init = unwrap_readonly(init_type)
                # PendingListType reassignment: different sizes force list
                if isinstance(inner_existing, PendingListType):
                    if isinstance(inner_init, PendingListType):
                        if inner_existing.size != inner_init.size:
                            self.deduction.mark_list_different_size(inner_existing.literal_id)
                            self.deduction.mark_list_different_size(inner_init.literal_id)
                        else:
                            self.deduction.link_list_literals(inner_existing.literal_id, inner_init.literal_id)
                    var_type = existing_type
                # PendingStrType reassignment: track view-compatibility, keep pending
                elif isinstance(inner_existing, PendingStrType):
                    if is_any_str_type(inner_init):
                        if not self.deduction.is_view_compatible_source(stmt.init, inner_init):
                            self.deduction.mark_str_reassigned_from_owned(stmt.name)
                        else:
                            self.deduction.track_str_reassign_source(stmt.name, inner_init)
                    var_type = existing_type
                else:
                    var_type = self.deduction.resolve_reassignment_target_type(
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
                        if isinstance(var_type, OptionalType):
                            var_type = OptionalType(ReadonlyType(var_type.inner))
                        else:
                            var_type = ReadonlyType(var_type)
                    if var_type != existing_type:
                        # Keep original declaration's resolved type in sync for codegen.
                        resolved = unwrap_readonly(var_type)
                        orig_decl = self.ctx.var_decl_by_name.get(stmt.name)
                        if orig_decl:
                            self.ctx.var_types[id(orig_decl)] = resolved
                        # Retroactively update declared_var_types for earlier lines
                        # so # tpyc: type() reflects the final variable type.
                        for key in self.ctx.declared_var_types:
                            if key[1] == stmt.name:
                                self.ctx.declared_var_types[key] = resolved
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
            self.deduction.record_write(stmt.name, stmt.init, init_type)
            # Annotate tuple literal element capture modes (local context)
            if isinstance(stmt.init, TpyTupleLiteral) and isinstance(var_type, TupleType):
                self._annotate_tuple_elem_capture(stmt.init, var_type)
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
            self.deduction.set_authoritative_annotation(
                stmt.name,
                stmt.type,
                line=(stmt.loc.line if stmt.loc else None),
            )
        else:
            raise self.ctx.error(f"Variable '{stmt.name}' has no type annotation and no initializer", stmt)

        # String variable PendingStrType inference (function-local only, new vars)
        if (isinstance(var_type, StrType) and stmt.init is not None
                and not self.ctx.is_top_level and not is_global_declared
                and existing_type is None):
            str_var_id = self.ctx.str_var_counter
            self.ctx.str_var_counter += 1
            is_owned = not self.deduction.is_view_compatible_source(stmt.init, init_type)
            sv_info = StrVarInfo(str_var_id=str_var_id, variable_name=stmt.name,
                                decl_line=stmt.loc.line if stmt.loc else None,
                                initialized_from_owned=is_owned)
            self.ctx.str_vars[str_var_id] = sv_info
            self.ctx.variable_to_str_var[stmt.name] = str_var_id
            self.ctx.pending_str_resolutions.append(str_var_id)
            var_type = PendingStrType(str_var_id)
        # Alias: new var initialized from an existing PendingStrType local.
        # Gets its own ID with source tracking; if the source later resolves
        # to str, this alias is retroactively promoted during resolution.
        elif (isinstance(var_type, PendingStrType) and stmt.init is not None
                and not self.ctx.is_top_level and not is_global_declared
                and existing_type is None):
            str_var_id = self.ctx.str_var_counter
            self.ctx.str_var_counter += 1
            sv_info = StrVarInfo(str_var_id=str_var_id, variable_name=stmt.name,
                                decl_line=stmt.loc.line if stmt.loc else None,
                                source_str_var_id=var_type.str_var_id)
            self.ctx.str_vars[str_var_id] = sv_info
            self.ctx.variable_to_str_var[stmt.name] = str_var_id
            self.ctx.pending_str_resolutions.append(str_var_id)
            var_type = PendingStrType(str_var_id)
        # Alias: new var initialized from an existing PendingListType local (b = a).
        # Gets its own literal info with source tracking; if either side later
        # resolves to list, the other is retroactively promoted during resolution.
        elif (isinstance(var_type, PendingListType) and stmt.init is not None
                and isinstance(stmt.init, TpyName)
                and not self.ctx.is_top_level and not is_global_declared
                and existing_type is None):
            var_type = self.deduction.register_list_alias(
                stmt.name, var_type,
                decl_line=(stmt.loc.line if stmt.loc else None),
            )

        if is_global_declared:
            # Update global scope type; bind in current scope for local reads
            self.ctx.global_scope.define(stmt.name, var_type)
            self.ctx.current_scope.define(stmt.name, var_type)
        else:
            self.ctx.current_scope.define(stmt.name, var_type)
        if stmt.init:
            self.init.mark_assigned(stmt.name)
        self.narrowing.update_after_write(stmt.name, var_type, init_type if stmt.init else None, stmt.init)
        # Union assignment narrowing: narrow to concrete member on initial declaration
        if existing_type is None:
            inner_var = unwrap_readonly(var_type)
            if (isinstance(inner_var, UnionType) and init_type is not None
                    and init_type in inner_var.members):
                self.ctx.narrowed_types[stmt.name] = init_type
                facts = {stmt.name: init_type}
                stmt.then_type_facts = self._filter_union_codegen_facts(facts)
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
                # Move-through: lvalue alias at last use of source promotes to rvalue.
                # Both target and source must be non-reassigned Tier 1 locals
                # (reassigned vars become T* pointer-locals in codegen).
                if (isinstance(stmt.init, TpyName)
                        and existing_type is None
                        and stmt.name not in self.ctx.current_reassigned_vars
                        and stmt.init.name not in self.ctx.current_reassigned_vars
                        and id(stmt.init) in self.ctx.all_last_uses
                        and self.compat._is_movable_var(stmt.init.name)
                        and var_type is not None
                        and not var_type.is_value_type()
                        and not (isinstance(var_type, OptionalType) and var_type.uses_pointer_repr())):
                    self.ctx.rvalue_vars.add(stmt.name)
                    self.ctx.move_through_vars.add(stmt.name)
                else:
                    self.ctx.rvalue_vars.discard(stmt.name)
            else:
                self.ctx.rvalue_vars.add(stmt.name)
        # Track provenance for non-value types and pointer types
        # (pointers are value types but carry address provenance)
        if stmt.init and (not var_type.is_value_type() or isinstance(var_type, PtrType)):
            self.init.mark_provenance(stmt.name, self.compat.is_param_derived_expr(stmt.init))
        # Track non-null pointer provenance for null-check elision
        if stmt.init and isinstance(var_type, PtrType):
            # Unwrap coercion (e.g. Ptr[T] -> ReadOnlyPtr[T]) to find the source expression
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
        # Record declared type for test type-annotation validation.
        if stmt.loc:
            self.ctx.declared_var_types[(stmt.loc.line, stmt.name)] = var_type

    def _analyze_tuple_unpack(self, stmt: TpyTupleUnpack) -> None:
        """Analyze tuple unpacking: a, b = expr."""
        rhs_type = self.expr.analyze_expr(stmt.value)

        if not isinstance(rhs_type, TupleType):
            raise self.ctx.error(
                f"Cannot unpack non-tuple type {rhs_type}", stmt)

        n_targets = len(stmt.targets)
        n_elems = len(rhs_type.element_types)
        if n_targets != n_elems:
            raise self.ctx.error(
                f"Cannot unpack tuple of {n_elems} elements into "
                f"{n_targets} targets", stmt)

        for i, name in enumerate(stmt.targets):
            elem_type = rhs_type.element_types[i]
            owned = isinstance(elem_type, OwnType)
            stmt.is_owned.append(owned)
            if owned:
                elem_type = elem_type.wrapped
            is_ref = (not owned and not elem_type.is_value_type()
                      and not isinstance(elem_type, TypeParamRef))
            stmt.is_ref.append(is_ref)

            stmt.target_types.append(elem_type)

            if name is None:
                stmt.is_new.append(True)
                continue

            if self.ctx.is_top_level:
                # At module level, targets become globals with namespace-scope
                # definitions. Mark is_new=False so codegen emits assignment
                # (the declaration is handled by gen_global_decl).
                self.ctx.global_scope.define(name, elem_type)
                self.ctx.current_scope.define(name, elem_type)
                self.init.mark_assigned(name)
                if self.ctx.current_ns:
                    self.ctx.current_ns.bind_variable(name, elem_type)
                decl_line = stmt.loc.line if stmt.loc else 0
                if name not in self.ctx.top_level_decls:
                    self.ctx.top_level_decls[name] = decl_line
                self._warn_all_caps_without_final(name, str(elem_type), stmt)
                stmt.is_new.append(False)
                continue

            existing = self.ctx.current_scope.lookup(name)
            # Don't treat globals as existing unless explicitly declared
            # with 'global' -- unpack should create locals by default
            if (existing is not None
                    and name not in self.ctx.global_declarations
                    and name not in self.ctx.current_scope.bindings
                    and name in self.ctx.global_scope.bindings):
                existing = None
            if existing is not None:
                self._check_nonvalue_rebinding(name, stmt)
                self.compat.check_type_compatible(
                    elem_type, existing, "tuple unpacking", source_expr=stmt)
                stmt.is_new.append(False)
            else:
                self.ctx.current_scope.define(name, elem_type)
                self.init.mark_assigned(name)
                stmt.is_new.append(True)

    def _analyze_assign(self, stmt: TpyAssign) -> None:
        """Analyze an assignment."""
        target_type = self.expr.analyze_expr(stmt.target)
        value_type = self.expr.analyze_expr_with_hint(stmt.value, target_type)
        self._enforce_readonly_assignment_target(stmt.target)
        if isinstance(stmt.target, (TpyFieldAccess, TpySubscript)):
            declared_target_type = self.narrowing.declared_type_for_expr(stmt.target)
            if declared_target_type is not None:
                target_type = declared_target_type
        if isinstance(stmt.target, TpyName):
            # Block reassignment of Final globals at module level
            if self.ctx.is_top_level and stmt.target.name in self.ctx.final_globals:
                raise self.ctx.error(
                    f"Cannot reassign Final variable '{stmt.target.name}'",
                    stmt
                )
            # Match var-decl flow: reject forbidden rebinding before any type mutation.
            self._check_nonvalue_rebinding(stmt.target.name, stmt)
            declared_target_type = self.ctx.current_scope.lookup(stmt.target.name)
            if declared_target_type is not None:
                target_type = declared_target_type
            # Unwrap ReadonlyType for reassignment type resolution -- this is a
            # binding, not passing by reference.
            inner_target = unwrap_readonly(target_type)
            inner_value = unwrap_readonly(value_type)
            # PendingListType reassignment: different sizes force list
            if isinstance(inner_target, PendingListType):
                if isinstance(inner_value, PendingListType):
                    if inner_target.size != inner_value.size:
                        self.deduction.mark_list_different_size(inner_target.literal_id)
                        self.deduction.mark_list_different_size(inner_value.literal_id)
                    else:
                        self.deduction.link_list_literals(inner_target.literal_id, inner_value.literal_id)
                # target_type stays PendingListType
            # PendingStrType reassignment: track view-compatibility, keep pending
            elif isinstance(inner_target, PendingStrType):
                if is_any_str_type(inner_value):
                    if not self.deduction.is_view_compatible_source(stmt.value, inner_value):
                        self.deduction.mark_str_reassigned_from_owned(stmt.target.name)
                    else:
                        self.deduction.track_str_reassign_source(stmt.target.name, inner_value)
                # target_type stays PendingStrType
            else:
                target_type = self.deduction.resolve_reassignment_target_type(
                    stmt.target.name, inner_target, inner_value, init_expr=stmt.value
                )
                # Readonly status flows from the value expression
                if isinstance(value_type, ReadonlyType) and not target_type.is_value_type():
                    target_type = ReadonlyType(target_type)
            self.ctx.current_scope.define(stmt.target.name, target_type)
            if self.ctx.current_ns:
                self.ctx.current_ns.update_variable_type(stmt.target.name, target_type)
            self.ctx.set_expr_type(stmt.target, target_type)
            self.deduction.record_write(stmt.target.name, stmt.value, inner_value)
            if not isinstance(inner_target, (PendingStrType, PendingListType)):
                resolved = unwrap_readonly(target_type)
                var_decl = self.ctx.var_decl_by_name.get(stmt.target.name)
                if var_decl:
                    self.ctx.var_types[id(var_decl)] = resolved
                # Retroactively update declared_var_types for earlier lines
                # so # tpyc: type() reflects the final variable type.
                if resolved != unwrap_readonly(inner_target):
                    for key in self.ctx.declared_var_types:
                        if key[1] == stmt.target.name:
                            self.ctx.declared_var_types[key] = resolved

        # Disallow reassignment of non-value-type params and loop vars
        if isinstance(stmt.target, TpyName):
            # Update rvalue status for hoist eligibility
            if self.compat.is_lvalue(stmt.value):
                self.ctx.rvalue_vars.discard(stmt.target.name)
            else:
                self.ctx.rvalue_vars.add(stmt.target.name)

        # Tuples are immutable -- reject element assignment
        if isinstance(stmt.target, TpySubscript):
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            if isinstance(obj_type, TupleType):
                raise self.ctx.error("Tuples are immutable; cannot assign to tuple elements", stmt)
            # Dict subscript assignment is always allowed (no read-only dict variant)
            if not isinstance(unwrap_readonly(obj_type), DictType):
                elem_type = obj_type.get_element_type()
                if elem_type is not None:
                    # Check if type conforms to MutableSequence[elem_type]
                    mutable_seq = NamedType("MutableSequence", (elem_type,), is_protocol=True)
                    if not self.protocols.type_conforms_to_protocol(obj_type, mutable_seq):
                        raise self.ctx.error(f"Cannot assign to elements of {obj_type} (read-only)", stmt)

        # Prevent assignment through read-only pointer
        if isinstance(stmt.target, TpyName):
            pass  # TpyName targets are fine
        else:
            if isinstance(stmt.target, TpyFieldAccess):
                obj_type = self.ctx.get_expr_type(stmt.target.obj)
                if is_readonly_ptr(obj_type):
                    raise self.ctx.error("Cannot assign through read-only pointer", stmt)

        stmt.value = self.compat.coerce_expr(stmt.value, value_type, target_type, "assignment",
                                              coercion_ctx=CoercionContext.ASSIGN)
        # Annotate tuple literal element capture modes
        if isinstance(stmt.value, TpyTupleLiteral) and isinstance(target_type, TupleType):
            is_field = isinstance(stmt.target, TpyFieldAccess)
            self._annotate_tuple_elem_capture(
                stmt.value, target_type, is_field=is_field)
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
        if isinstance(stmt.target, TpyName) and (not target_type.is_value_type() or isinstance(target_type, PtrType)):
            self.init.mark_provenance(stmt.target.name, self.compat.is_param_derived_expr(stmt.value))
        # Track non-null pointer provenance for null-check elision
        if isinstance(stmt.target, TpyName) and isinstance(target_type, PtrType):
            # Unwrap coercion (e.g. Ptr[T] -> ReadOnlyPtr[T]) to find the source expression
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
            self.init.mark_assigned(stmt.target.name)
            self.narrowing.update_after_write(stmt.target.name, target_type, value_type, stmt.value)

    def _analyze_del_item(self, stmt: TpyDelItem) -> None:
        """Analyze del obj[key] statement."""
        for subscript in stmt.targets:
            # Analyze obj and index separately to avoid triggering __getitem__
            # validation (del doesn't read the element, only deletes it).
            self.expr.analyze_expr(subscript.obj)
            self._enforce_readonly_assignment_target(subscript)
            obj_type = self.ctx.get_expr_type(subscript.obj)
            actual = unwrap_readonly(obj_type)
            # Reject known-immutable/fixed-size types before analyzing the index
            if isinstance(actual, TupleType):
                raise self.ctx.error(
                    "Tuples are immutable; cannot delete tuple elements", stmt)
            if isinstance(actual, ArrayType):
                raise self.ctx.error(
                    "Arrays are fixed-size; cannot delete array elements", stmt)
            if isinstance(actual, SpanType):
                raise self.ctx.error(
                    "Spans are read-only views; cannot delete span elements", stmt)
            # Check that the type has __delitem__
            record_info = self.ctx.registry.get_record_for_type(actual)
            if record_info:
                overloads = record_info.get_method_overloads("__delitem__")
                if not overloads:
                    raise self.ctx.error(
                        f"'del' is not supported for type {actual}; "
                        f"define __delitem__ to enable element deletion", stmt)
            else:
                raise self.ctx.error(
                    f"'del' is not supported for type {actual}", stmt)
            # Analyze the index expression only after confirming __delitem__ exists
            self.expr.analyze_expr(subscript.index)

    def _analyze_aug_assign(self, stmt: TpyAugAssign) -> None:
        """Analyze an augmented assignment (+=, -=, etc.)."""
        # Block augmented assignment of Final globals at module level
        if isinstance(stmt.target, TpyName) and self.ctx.is_top_level and stmt.target.name in self.ctx.final_globals:
            raise self.ctx.error(
                f"Cannot reassign Final variable '{stmt.target.name}'",
                stmt
            )
        from .operators import OperatorResolver
        target_type = self.expr.analyze_expr(stmt.target)
        value_type = self.expr.analyze_expr_with_hint(stmt.value, target_type)
        self._enforce_readonly_assignment_target(stmt.target)
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
        # Target must be numeric, owned string, or list (for +=).
        # StrView is excluded -- it's non-owning, so += would dangle.
        is_numeric_target = isinstance(target_type, (Int32Type, BigIntType, IntLiteralType, FloatType))
        is_str_target = isinstance(target_type, (StrType, StringType, PendingStrType))
        is_list_target = isinstance(target_type, ListType)
        # PendingStrType += promotes to owned str
        if isinstance(target_type, PendingStrType) and isinstance(stmt.target, TpyName):
            self.deduction.mark_str_augassign(stmt.target.name)
        # list += only supports += (extend semantics)
        if is_list_target:
            if stmt.op != "+":
                raise self.ctx.error(
                    f"Only '+=' is supported for list types, got '{stmt.op}='",
                    stmt,
                )
            if not isinstance(value_type, ListType) or value_type.element_type != target_type.element_type:
                raise self.ctx.error(
                    f"Cannot extend {target_type} with {value_type}",
                    stmt,
                )
            stmt.is_list_extend = True
            return
        if not is_numeric_target and not is_str_target:
            raise self.ctx.error(
                f"Augmented assignment target must be a numeric or string type, got {target_type}",
                stmt,
            )
        if is_numeric_target and not isinstance(value_type, (Int32Type, BigIntType, IntLiteralType, FloatType)):
            raise self.ctx.error(
                f"Augmented assignment value must be a numeric type, got {value_type}",
                stmt,
            )
        if is_str_target and not is_any_str_type(value_type):
            raise self.ctx.error(
                f"Augmented assignment value must be a string type, got {value_type}",
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
