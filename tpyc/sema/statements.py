"""
TurboPython Statement Analysis

Statement analysis including variable declarations, assignments, and control flow.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, OwnType, ReadonlyType,
    FinalType,
    PendingListType, PendingDictType, make_list, PendingSetType, PendingStrType, PendingBytesType, PendingViewType, NominalType, TypeParamRef,
    ListLiteralInfo, DictLiteralInfo, SetLiteralInfo, ViewVarInfo, PtrType, is_readonly_ptr, NoneType, OptionalType, UnionType, UnknownElementType,
    unwrap_readonly, unwrap_own, unwrap_qualifiers, is_any_str_type, is_any_bytes_type, TupleType,
    LiteralType,
    ViewTypeFamily, VIEW_TYPE_FAMILIES, STR_FAMILY, BYTES_FAMILY,
    PendingGenericInstanceType, contains_fn_type,
    INT32, VOID, BIGINT, FLOAT, STRVIEW, BYTES, BYTESVIEW, is_protocol_type, is_protocol_union, final_type_str_to_strview,
    is_final_allowed_inner, FINAL_INNER_TYPE_ERROR,
    qualify_exception_name, is_return_exception, is_exception_type,
    FunctionInfo, ParamInfo, RecordInfo,
    make_ref, unwrap_ref_type, RefType,
    is_integer_type, is_any_int_type, is_numeric_type, is_readonly_span,
    is_float_type, is_any_float_type)
from ..parse import (
    TpyExpr,
    TpyStmt, TpyVarDecl, TpyTupleUnpack, TpyAssign, TpyAugAssign, TpyDelItem, TpyDelVar, TpyExprStmt, TpyReturn, TpyYield,
    TpyIf, TpyWhile, TpyForEach, TpyBreak, TpyContinue, TpyAssert,
    TpyRaise, TpyExceptHandler, TpyTry, TpyWith,
    TpyGlobal, TpyNonlocal, TpyNestedDef,
    TpyCall, TpyMethodCall, TpyArrayLiteral, TpyListComprehension, TpyDictLiteral, TpyCoerce,
    TpySubscript, TpySlice, TpyStrLiteral, TpyName, TpyTupleLiteral,
    TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyUnaryOp,
    TpyFieldAccess, TpyFunction, TupleElemCapture,
    TpyMatch, TpyBinOp, TpyIfExpr,
)
from ..coercions import CoercionContext
from ..namespace import BindingKind
from ..prescan import ScanResult, scan_reassigned_vars
from ..liveness import analyze_last_uses
from ..parse.nodes import VarLinkage
from .context import addr_taken_roots
from ..diagnostics import SemanticError, NOCOPY_REMEDIATION_HINT
from .match import MatchAnalyzer
from .narrowing import NarrowingTracker
from .scope_tracker import ScopeTracker
from .init_tracker import InitTracker
from .value_range import ValueRange
if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .compatibility import TypeCompatibility
    from .local_deduction import LocalTypeDeduction
    from .list_literals import IterableHelper
    from .expressions import ExpressionAnalyzer
    from .protocols import ProtocolChecker

from .context import BorrowKind, MODULE_INIT_CONTEXT, PENDING_CONTAINER_TYPES, _storage_key, _borrow_storage_root
from .expressions import _collect_body_name_refs, _collect_body_local_defs
from .local_deduction import collect_pending_source_types
from .operators import OperatorResolver
from tpyc import modules as builtin_modules
from tpyc import qnames
from ..type_def_registry import (
    is_dict, is_array, is_span, is_list,
    is_char_type, is_str_type, is_string_type, is_str_view_type,
    is_bytes_type, is_bytearray_type, is_bytes_view_type,
    is_borrowing_view_type,
    is_fixed_int_type, is_big_int_type,
    find_factory_by_simple_name, protocol_info_of,
)


def _needs_provenance_tracking(t: TpyType) -> bool:
    """Whether a local of this type participates in param-provenance / trusted-call tracking.

    Non-value types are reference types and always track provenance.
    PtrType is a value type but carries an address. Borrowing views
    (StrView / BytesView / Span / SpanIter) are value types with an
    interior pointer into their source storage. PendingViewType covers
    unresolved str/bytes locals -- if they settle into view semantics
    the tracking is required; if they settle into owned (String/bytes)
    the extra tracking is harmless.
    """
    return (not t.is_value_type()
            or isinstance(t, PtrType)
            or is_borrowing_view_type(t)
            or isinstance(t, PendingViewType))


def _is_dangling_temporary_arg(expr: TpyExpr) -> bool:
    """Check if an expression is a temporary whose storage won't survive.

    Function calls and binary ops produce temporaries destroyed at
    end-of-statement. Literals (string, int, array, etc.) are either
    static or materialized by codegen into named locals. Names and field
    accesses have addressable storage.
    """
    if isinstance(expr, TpyCoerce):
        return _is_dangling_temporary_arg(expr.expr)
    if isinstance(expr, (TpyCall, TpyMethodCall, TpyBinOp)):
        return True
    if isinstance(expr, TpyIfExpr):
        return (_is_dangling_temporary_arg(expr.then_expr)
                or _is_dangling_temporary_arg(expr.else_expr))
    return False


def _register_call_result_borrow(ctx: SemanticContext, borrower: str, expr: TpyExpr) -> None:
    """Register borrow from function call return value (8b).

    When a function has return_borrows_from facts, the result variable
    borrows from the indicated argument(s). None means unanalyzed -- skip.
    """
    if isinstance(expr, TpyCall):
        fi = expr.resolved_function_info
        args = expr.args
        obj = None
    elif isinstance(expr, TpyMethodCall):
        fi = expr.resolved_function_info
        args = expr.args
        obj = expr.obj
    elif isinstance(expr, TpyFieldAccess) and expr.property_getter_call is not None:
        # Property getter is a method call; use its return_borrows_from facts
        fi = expr.property_getter_call.resolved_function_info
        args = expr.property_getter_call.args
        obj = expr.property_getter_call.obj
    else:
        return
    if fi is None or fi.return_borrows_from is None:
        return
    bt = ctx.func.borrow_tracker
    for idx in fi.return_borrows_from:
        if idx == -1 and obj is not None:
            root = _borrow_storage_root(obj)
            if root is not None:
                bt.add_borrow(root, borrower, BorrowKind.ELEMENT)
            elif _is_dangling_temporary_arg(obj):
                ctx.warning(
                    f"Result borrows from temporary receiver object; "
                    f"the temporary is destroyed at end-of-statement",
                    expr,
                )
        elif idx >= 0 and idx < len(args):
            root = _borrow_storage_root(args[idx])
            if root is not None:
                bt.add_borrow(root, borrower, BorrowKind.ELEMENT)
            elif _is_dangling_temporary_arg(args[idx]):
                ctx.warning(
                    f"Result borrows from temporary argument '{fi.params[idx].name}'; "
                    f"the temporary is destroyed at end-of-statement",
                    expr,
                )



def _format_aug_target(target: TpyExpr) -> str:
    """Format an aug-assign target as a short label for error messages.

    e.g. items[0] -> 'items[...]', obj.x -> 'obj.x', otherwise 'target'.
    """
    if isinstance(target, TpyFieldAccess):
        obj = target.obj.name if isinstance(target.obj, TpyName) else "..."
        return f"'{obj}.{target.field}'"
    if isinstance(target, TpySubscript):
        obj = target.obj.name if isinstance(target.obj, TpyName) else "..."
        return f"'{obj}[...]'"
    return "target"


def _root_name_of_expr(expr: TpyExpr) -> str | None:
    """Extract the root TpyName from a chain of field/subscript accesses.

    e.g. p.inner.v -> "p", c.items[0] -> "c", x -> "x".
    Returns None for non-name roots (calls, literals, etc.).

    Unbound-self field access (BaseN.field, set by sema) reports "self"
    since the implicit receiver is `this` -- the syntactic root name is
    the ancestor class, but the mutation travels through self.
    """
    while isinstance(expr, (TpyFieldAccess, TpySubscript)):
        if (isinstance(expr, TpyFieldAccess)
                and expr.unbound_self_parent_type is not None):
            return "self"
        expr = expr.obj
    return expr.name if isinstance(expr, TpyName) else None


def _is_self_call_deferred(
    expr_obj: TpyExpr, obj_root: str | None,
    loop_var_iterable: dict[str, str],
) -> bool:
    """Check if a method call receiver traces to self through field accesses or loop vars.

    When True, self-mutation is deferred to Phase 2 via call edges
    (receiver_is_self=True) instead of being marked directly in Phase 1.
    This enables readonly inference for methods that call non-mutating
    methods on fields or loop elements.
    """
    if obj_root == "self":
        # Verify the chain is purely field accesses (no subscripts like
        # self.items[0].method()). _root_name_of_expr strips both FieldAccess
        # and Subscript, so obj_root=="self" doesn't rule out subscripts.
        # Subscript-rooted calls are not deferred because the call edge in
        # calls.py also only walks TpyFieldAccess.
        chain = expr_obj
        while isinstance(chain, TpyFieldAccess):
            chain = chain.obj
        return isinstance(chain, TpyName) and chain.name == "self"
    if obj_root is not None:
        # loop_var.method() where loop_var iterates over self.field
        iterable = loop_var_iterable.get(obj_root)
        if iterable is not None:
            root = iterable.split(".")[0] if "." in iterable else iterable
            if root == "self":
                return True
    return False


# Map raw owned types (by qname) and pending view types (by class) to their
# ViewTypeFamily. Owned types share the NominalType class, so dispatch on qname.
_VIEW_OWNED_QNAME_TO_FAMILY: dict[str, ViewTypeFamily] = {
    _f.owned_type.qualified_name(): _f for _f in VIEW_TYPE_FAMILIES
}
_VIEW_PENDING_CLASS_TO_FAMILY: dict[type, ViewTypeFamily] = {
    _f.pending_type_class: _f for _f in VIEW_TYPE_FAMILIES
}


def _view_family_for_type(var_type: TpyType) -> ViewTypeFamily | None:
    """Return the ViewTypeFamily for a str/bytes/pending-view type, or None."""
    family = _VIEW_PENDING_CLASS_TO_FAMILY.get(type(var_type))
    if family is not None:
        return family
    qn = var_type.qualified_name() if isinstance(var_type, NominalType) else None
    return _VIEW_OWNED_QNAME_TO_FAMILY.get(qn) if qn else None


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
        expr: ExpressionAnalyzer,
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
        self.expr = expr
        self.match = MatchAnalyzer(ctx, self, expr)

    def _resolve_obj_storage(self, obj: TpyExpr) -> str | None:
        """Resolve the borrow-tracker storage key for a mutation target's object.

        For simple names, resolves aliases via effective_storage().
        For single-level field access (self.items), returns the dotted key directly
        (no alias resolution -- field paths are not aliased in the tracker).
        """
        if isinstance(obj, TpyName):
            return self.ctx.func.borrow_tracker.effective_storage(obj.name)
        return _storage_key(obj)

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
                and self.compat._is_owned_var(inner.name)):
            self.ctx.warning(
                f"unnecessary copy() -- '{inner.name}' is at its last use and would be moved automatically",
                value,
            )

    def _warn_str_field_return_copy(self, value: TpyExpr, expected: TpyType) -> None:
        """Warn when a method returns self.field where field is str.

        This copies the string; suggest StrView (zero-copy view) or String
        (explicit owned) so the user makes an intentional choice.
        Suppressed for dunder methods (__str__, __repr__).
        """
        if not is_str_type(expected):
            return
        func = self.ctx.func.current_function
        if func is None or not func.is_method:
            return
        # Dunder methods (__str__, __repr__) have a fixed str contract
        if func.name.startswith("__") and func.name.endswith("__"):
            return
        inner = value
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        if not (isinstance(inner, TpyFieldAccess)
                and isinstance(inner.obj, TpyName)
                and inner.obj.name == "self"):
            return
        field_name = inner.field
        self.ctx.warning(
            f"returns a copy of str field 'self.{field_name}'; "
            f"use -> StrView for zero-copy access or -> String to silence this warning",
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
            self.compat.check_own_consumption(expr)
            return
        if not self.compat.is_lvalue(expr):
            return
        # Value types are always safe to return as Own -- they're copied, not
        # aliased. Expressions already typed as Own (e.g. stepped slice
        # returning Own[list[T]]) produce owned values regardless.
        # Unwrap TpyCoerce to get the source type (coerce records the target).
        inner = expr
        while isinstance(inner, TpyCoerce):
            inner = inner.expr
        raw_type = self.ctx.get_raw_expr_type(inner)
        if raw_type is not None:
            unwrapped = unwrap_ref_type(raw_type)
            if isinstance(unwrapped, OwnType) or unwrapped.is_value_type():
                return
        # In a consuming method, self.field is owned (ownership propagation)
        # and can be moved out of the struct. The codegen wraps in std::move.
        if (self.ctx.in_consuming_method
                and isinstance(expr, TpyFieldAccess)
                and isinstance(expr.obj, TpyName) and expr.obj.name == "self"):
            return
        is_auto_moved = (isinstance(expr, TpyName)
                         and id(expr) in self.ctx.all_last_uses
                         and self.compat._is_owned_var(expr.name))
        if is_auto_moved:
            self.compat.check_own_consumption(expr)
            return
        expr_type = self.ctx.get_expr_type(expr)
        is_nocopy = expr_type is not None and self.ctx.is_type_nocopy(expr_type)
        if is_nocopy:
            reason = self.ctx.nocopy_reason(expr_type)
            is_movable = (isinstance(expr, TpyName)
                          and self.compat._is_owned_var(expr.name))
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
        func = self.ctx.func.current_function
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

        is_readonly = (self.ctx.func.current_function is not None
                       and getattr(self.ctx.func.current_function, 'is_readonly', False))

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
                elem_val_type = self.ctx.get_raw_expr_type(elem)
                should_warn = False
                if elem_val_type is not None and isinstance(elem_val_type, (RefType, OwnType)):
                    if not self.compat.is_copy_call(elem):
                        should_warn = isinstance(elem_val_type, RefType) or isinstance(elem, TpyName)
                elif self._is_non_owned_var_copy(elem, et):
                    should_warn = True
                else:
                    elem_stripped = self.ctx.get_expr_type(elem)
                    if not self.compat.is_copy_call(elem):
                        if (isinstance(elem_stripped, OptionalType) and not elem_stripped.inner.is_value_type()):
                            should_warn = True
                        elif (isinstance(elem_stripped, UnionType) and elem_stripped.uses_pointer_repr()
                              and not self.ctx.is_recursive_union(elem_stripped)):
                            should_warn = True
                if should_warn:
                    if self.ctx.is_type_non_copyable(et):
                        raise self.ctx.error(
                            f"cannot copy non-copyable type '{et}' into field "
                            f"(tuple element {i}){NOCOPY_REMEDIATION_HINT}",
                            elem
                        )
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
                    # Returning a non-value element by reference takes its address.
                    # Mark source params as needing T& (not const T&).
                    for tup_root in addr_taken_roots(elem):
                        self.ctx.mark_param_mutated(tup_root)
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
        if self.ctx.func.current_ns:
            for name, binding in self.ctx.func.current_ns.all_bindings().items():
                if binding.kind == BindingKind.VARIABLE:
                    result[name] = binding.type
        return result

    def _restore_ns_var_types(self, saved: dict[str, TpyType]) -> None:
        """Restore namespace variable types from a saved snapshot."""
        if self.ctx.func.current_ns:
            for name, typ in saved.items():
                self.ctx.func.current_ns.update_variable_type(name, typ)

    def _sync_ns_var_type(self, name: str, typ: TpyType) -> None:
        """Sync a single variable's namespace type to match scope."""
        if self.ctx.func.current_ns:
            self.ctx.func.current_ns.update_variable_type(name, typ)

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
            self.ctx.func.var_decl_by_name.items() if names is None
            else ((n, self.ctx.func.var_decl_by_name[n]) for n in names if n in self.ctx.func.var_decl_by_name)
        )
        for name, var_decl in items:
            canonical = self.ctx.var_types.get(id(var_decl))
            if canonical is None:
                continue
            current = self.ctx.func.current_scope.lookup(name)
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
                self.ctx.func.current_scope.define(name, target)
            self._sync_ns_var_type(name, target)

    def _enforce_readonly_assignment_target(self, target: TpyExpr) -> None:
        """Reject assignments through readonly references, frozen fields, and readonly field declarations."""
        # BaseN.field = v goes through `this`, but the syntactic receiver
        # (BaseN) has no value type, so the obj_type branch below can't
        # catch it -- gate on the current method's @readonly flag directly.
        if (isinstance(target, TpyFieldAccess)
                and target.unbound_self_parent_type is not None):
            cur = self.ctx.func.current_function
            if isinstance(cur, TpyFunction) and cur.is_readonly:
                raise self.ctx.error("Cannot mutate readonly reference", target)
        if isinstance(target, (TpyFieldAccess, TpySubscript)):
            obj_type = self.ctx.get_expr_type(target.obj)
            if obj_type is not None:
                check_type = obj_type
                if isinstance(check_type, OptionalType):
                    check_type = check_type.inner
                if isinstance(check_type, ReadonlyType):
                    raise self.ctx.error("Cannot mutate readonly reference", target)
                # isinstance narrowing strips ReadonlyType from the expr type;
                # the scope binding preserves it, so check there.
                if isinstance(target.obj, TpyName) and self.ctx.is_readonly_name(target.obj.name):
                    raise self.ctx.error("Cannot mutate readonly reference", target)
                # Frozen dataclass / readonly field: reject assignment except self.field in __init__
                if isinstance(target, TpyFieldAccess):
                    actual = unwrap_readonly(check_type)
                    if isinstance(actual, NominalType):
                        info = self.ctx.registry.get_record(actual.name)
                        if info is not None:
                            cur = self.ctx.func.current_function
                            rec = self.ctx.record_ctx.record
                            in_own_init = (
                                isinstance(cur, TpyFunction) and cur.name == "__init__"
                                and isinstance(target.obj, TpyName) and target.obj.name == "self"
                                and rec is not None and rec.name == actual.name
                            )
                            if info.is_frozen and not in_own_init:
                                raise self.ctx.error(
                                    f"Cannot assign to field '{target.field}' of frozen dataclass '{actual.name}'",
                                    target,
                                )
                            # Walk the class hierarchy to find readonly fields
                            # (own and inherited). __init__ of the declaring
                            # class or any subclass is exempt.
                            in_any_init = (
                                isinstance(cur, TpyFunction) and cur.name == "__init__"
                                and isinstance(target.obj, TpyName) and target.obj.name == "self"
                                and rec is not None
                            )
                            if not in_any_init:
                                # Walk self + MRO ancestors for a readonly field declaration.
                                records_to_check = [info, *self.ctx.registry.iter_ancestor_records(info)]
                                for check in records_to_check:
                                    for fld in check.fields:
                                        if fld.name == target.field and isinstance(fld.type, ReadonlyType):
                                            raise self.ctx.error(
                                                f"Cannot assign to readonly field '{target.field}'",
                                                target,
                                            )

    def _find_consuming_iter(self, iterable_type: TpyType) -> FunctionInfo | None:
        """Find the consuming __iter__ overload for a type, if any.

        Only matches concrete types (list, user records with auto_own __iter__).
        Skips pending/unresolved types to avoid mismatched codegen.
        """
        # Only match concrete types, not pending/unresolved
        if isinstance(iterable_type, (PendingListType, PendingGenericInstanceType)):
            return None
        # Value-type elements: moving is identical to copying, so consuming
        # the container's internal structure is pure overhead.
        elem = builtin_modules.get_iterable_element_type(iterable_type, registry=self.ctx.registry)
        if elem is not None and elem.is_value_type():
            return None
        record_info = self.ctx.registry.get_record_for_type(iterable_type)
        if record_info is None:
            return None
        overloads = record_info.get_method_overloads("__iter__")
        for fi in overloads:
            if fi.is_consuming:
                # Build type substitution for generic types
                type_subst = self.type_ops.build_type_substitution(iterable_type)
                if type_subst:
                    fi = self.type_ops.substitute_method_type_params(fi, type_subst)
                return fi
        return None

    def _resolve_enum_iterable(self, stmt: TpyForEach) -> NominalType | None:
        """Check if for-each iterates over an enum type (e.g. `for c in Color`).

        Returns the enum NominalType if so, None otherwise.
        """
        iterable = stmt.iterable
        if not isinstance(iterable, TpyName):
            return None
        binding = self.ctx.func.current_ns.lookup(iterable.name) if self.ctx.func.current_ns else None
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
        try:
            self._analyze_stmt_dispatch(stmt)
        finally:
            # Flush post-access ptr narrowing queued during expression analysis.
            # See FunctionTrackingState.pending_non_null_ptr_vars for rationale
            # (deferred to statement boundary so within-statement sibling
            # accesses keep their individual null checks).
            pending = self.ctx.func.pending_non_null_ptr_vars
            if pending:
                self.ctx.func.non_null_ptr_vars |= pending
                pending.clear()

    def _analyze_stmt_dispatch(self, stmt: TpyStmt) -> None:
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
        elif isinstance(stmt, TpyDelVar):
            self._analyze_del_var(stmt)
        elif isinstance(stmt, TpyExprStmt):
            self.expr.analyze_expr(stmt.expr)
        elif isinstance(stmt, TpyReturn):
            if stmt.value:
                expected = unwrap_ref_type(self.ctx.func.current_function.return_type) if self.ctx.func.current_function else VOID
                ret_type = self.expr.analyze_expr_with_hint(stmt.value, expected)
                stmt.value_type = ret_type
                stmt.value = self.compat.coerce_expr(stmt.value, ret_type, expected, "return value",
                                                      coercion_ctx=CoercionContext.RETURN, is_return=True)
                # Track return-type context for pending list deduction
                self.deduction.mark_list_return_context(stmt.value, expected)
                # Warn when a method copies a str field on return
                self._warn_str_field_return_copy(stmt.value, expected)
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
                # Returning a non-value type by reference takes the source's address.
                # Mark both loop vars and params so they keep T& (not const T&/const T*).
                if isinstance(stmt.value, TpyName):
                    self.ctx.mark_loop_var_mutated(stmt.value.name)
                    # Escape tracking: returning a nested def marks it as escaping
                    if stmt.value.name in self.ctx.func.nested_def_names:
                        self.ctx.func.nested_def_escapes.add(stmt.value.name)
                # Returning a borrowing view does not give the caller write
                # access to the source, so we record borrow provenance but
                # must not raise self_mutated -- that would suppress auto-const
                # on the enclosing method and break const callers.
                if expected is not None:
                    returns_borrowing_view = is_borrowing_view_type(expected)
                    if not expected.is_value_type() or returns_borrowing_view:
                        for ret_root in addr_taken_roots(stmt.value):
                            if not returns_borrowing_view:
                                self.ctx.mark_param_mutated(ret_root)
                            self.ctx.mark_param_returned(ret_root)  # 8b: track which param storage the return borrows
                        # 8b rule 3: transitive return -- if returning the result of a call
                        # whose return_borrows_from is known, propagate the borrow contract.
                        # e.g. `return inner(items)` where inner borrows param 0 -> mark items.
                        ret_inner = stmt.value.expr if isinstance(stmt.value, TpyCoerce) else stmt.value
                        if isinstance(ret_inner, (TpyCall, TpyMethodCall)):
                            fi_ret = ret_inner.resolved_function_info
                            if fi_ret is not None and fi_ret.return_borrows_from:
                                ret_args = ret_inner.args
                                ret_obj = getattr(ret_inner, 'obj', None)
                                for idx in fi_ret.return_borrows_from:
                                    if idx == -1 and ret_obj is not None:
                                        src = _borrow_storage_root(ret_obj)
                                    elif idx >= 0 and idx < len(ret_args):
                                        src = _borrow_storage_root(ret_args[idx])
                                    else:
                                        src = None
                                    if src is not None:
                                        # Read-only sources (readonly callees, view returns)
                                        # don't propagate mutation to their borrowed-from arg.
                                        if not returns_borrowing_view and not fi_ret.is_readonly:
                                            self.ctx.mark_param_mutated(src)
                                        self.ctx.mark_param_returned(src)
            self.init.mark_terminated()
        elif isinstance(stmt, TpyYield):
            self._analyze_yield(stmt)
        elif isinstance(stmt, TpyIf):
            assigned_before_cond = frozenset(self.ctx.func.definitely_assigned)
            saved_sc_and = self.ctx.sc_and_walrus.copy()
            saved_sc_or = self.ctx.sc_or_walrus.copy()
            self.ctx.sc_and_walrus = set()
            self.ctx.sc_or_walrus = set()
            self.expr.analyze_expr(stmt.condition)
            always_walrus = self.ctx.func.definitely_assigned - assigned_before_cond
            sc_and = self.ctx.sc_and_walrus   # safe in then-body
            sc_or = self.ctx.sc_or_walrus     # safe in else-body
            self.ctx.sc_and_walrus = saved_sc_and
            self.ctx.sc_or_walrus = saved_sc_or
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            then_type_facts, else_type_facts = self.narrowing.condition_type_facts(stmt.condition)
            ptr_nn_then, ptr_nn_else = self.narrowing.condition_ptr_null_facts(stmt.condition)
            range_true, range_false = self.narrowing.condition_range_facts(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(then_type_facts)
            stmt.else_type_facts = self._filter_union_codegen_facts(else_type_facts)
            scope_before = set(self.ctx.func.current_scope.bindings.keys())
            assigned_before = frozenset(self.ctx.func.definitely_assigned)
            before = self.init.save()
            consumed_before = self.ctx.func.current_consumed_own_params.copy()
            # Save binding types for ReadonlyType merge after branches
            bindings_before = dict(self.ctx.func.current_scope.bindings)
            ns_types_before = self._save_ns_var_types()
            # Then-body: condition was true -> && operands all evaluated,
            # but || RHS may have been skipped (LHS alone was truthy)
            self.ctx.func.definitely_assigned |= always_walrus | sc_and
            self.ctx.func.narrowed_types.update(then_type_facts)
            self.ctx.func.non_null_ptr_vars |= ptr_nn_then
            self._apply_range_facts(range_true)
            for s in stmt.then_body:
                self.analyze_stmt(s)
            then_state = self.init.save()
            consumed_after_then = self.ctx.func.current_consumed_own_params.copy()
            then_terminated = self.ctx.func.init_terminated
            bindings_after_then = dict(self.ctx.func.current_scope.bindings)
            # Restore bindings for else branch
            self.ctx.func.current_scope.bindings.update(bindings_before)
            self._restore_ns_var_types(ns_types_before)
            self.init.restore(before)
            # Else-body: condition was false -> || operands all evaluated,
            # but && RHS may have been skipped (LHS alone was falsy)
            self.ctx.func.definitely_assigned |= always_walrus | sc_or
            self.ctx.func.current_consumed_own_params = consumed_before.copy()
            self.ctx.func.narrowed_types.update(else_type_facts)
            self.ctx.func.non_null_ptr_vars |= ptr_nn_else
            self._apply_range_facts(range_false)
            for s in stmt.else_body:
                self.analyze_stmt(s)
            else_state = self.init.save()
            consumed_after_else = self.ctx.func.current_consumed_own_params.copy()
            else_terminated = self.ctx.func.init_terminated
            bindings_after_else = dict(self.ctx.func.current_scope.bindings)
            self.init.merge_branches(then_state, else_state)
            # Merge consumed Own[T] params: must be consumed on ALL non-terminated paths
            if then_terminated and else_terminated:
                self.ctx.func.current_consumed_own_params = consumed_after_then | consumed_after_else
            elif then_terminated:
                self.ctx.func.current_consumed_own_params = consumed_after_else
            elif else_terminated:
                self.ctx.func.current_consumed_own_params = consumed_after_then
            else:
                self.ctx.func.current_consumed_own_params = consumed_after_then & consumed_after_else
            # Merge ReadonlyType: if readonly on EITHER branch, keep readonly
            for name in set(bindings_after_then) | set(bindings_after_else):
                then_type = bindings_after_then.get(name)
                else_type = bindings_after_else.get(name)
                if then_type is not None and else_type is not None:
                    then_is_ro = isinstance(then_type, ReadonlyType)
                    else_is_ro = isinstance(else_type, ReadonlyType)
                    if then_is_ro and not else_is_ro:
                        merged = ReadonlyType(unwrap_readonly(else_type))
                        self.ctx.func.current_scope.define(name, merged)
                        self._sync_ns_var_type(name, merged)
                    elif else_is_ro and not then_is_ro:
                        merged = ReadonlyType(unwrap_readonly(then_type))
                        self.ctx.func.current_scope.define(name, merged)
                        self._sync_ns_var_type(name, merged)
            # Sync scope/namespace with var_types for variables whose
            # declaration type was promoted inside a branch.
            self._sync_promoted_var_types(
                set(bindings_after_then) | set(bindings_after_else)
            )
            # Detect variables first declared inside branches that need
            # pre-declaration. Skip when both branches terminate (no code
            # after the if needs the variable).
            if not self.ctx.func.init_terminated:
                branch_new = set(self.ctx.func.current_scope.bindings.keys()) - scope_before
                newly_assigned = self.ctx.func.definitely_assigned - assigned_before
                predecl = (branch_new & newly_assigned) - self.ctx.func.global_declarations
            else:
                predecl = set()
            if predecl:
                self.ctx.if_branch_decls[id(stmt)] = {
                    name: self.ctx.func.current_scope.lookup(name)
                    for name in sorted(predecl)
                }
        elif isinstance(stmt, TpyWhile):
            assigned_before_cond = frozenset(self.ctx.func.definitely_assigned)
            saved_sc_and = self.ctx.sc_and_walrus.copy()
            saved_sc_or = self.ctx.sc_or_walrus.copy()
            self.ctx.sc_and_walrus = set()
            self.ctx.sc_or_walrus = set()
            self.expr.analyze_expr(stmt.condition)
            always_walrus_w = self.ctx.func.definitely_assigned - assigned_before_cond
            all_walrus_w = always_walrus_w | self.ctx.sc_and_walrus | self.ctx.sc_or_walrus
            self.ctx.sc_and_walrus = saved_sc_and
            self.ctx.sc_or_walrus = saved_sc_or
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            then_type_facts, _ = self.narrowing.condition_type_facts(stmt.condition)
            ptr_nn, _ = self.narrowing.condition_ptr_null_facts(stmt.condition)
            range_true, _ = self.narrowing.condition_range_facts(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(then_type_facts)
            before = self.init.save()
            consumed_before_loop = self.ctx.func.current_consumed_own_params.copy()
            # Save namespace types -- loop_scope() restores scope bindings
            # automatically, but namespace mutations inside the loop persist.
            ns_types_before_while = self._save_ns_var_types()
            with self.scopes.loop_scope():
                self.init.apply_loop_entry_facts(
                    before,
                    condition_type_facts=then_type_facts,
                )
                # Applied separately from apply_loop_entry_facts because
                # that method only handles type narrowing, not ptr non-null.
                self.ctx.func.non_null_ptr_vars |= ptr_nn
                self._apply_range_facts(range_true)
                for s in stmt.body:
                    self.analyze_stmt(s)
            self.init.apply_loop_exit_facts(before)
            # Re-add all walrus vars (condition always evaluates fully)
            self.ctx.func.definitely_assigned |= all_walrus_w
            # Loop might not execute — consumption inside is not definite
            self.ctx.func.current_consumed_own_params = consumed_before_loop
            # Restore namespace to pre-loop state (scope was already restored
            # by loop_scope context manager)
            self._restore_ns_var_types(ns_types_before_while)
            self._sync_promoted_var_types()
            for s in stmt.orelse:
                self.analyze_stmt(s)
        elif isinstance(stmt, TpyForEach):
            # Check for enum iteration: `for c in Color`
            enum_type = self._resolve_enum_iterable(stmt)
            if enum_type is not None:
                stmt.enum_iterable = enum_type
                elem_type = enum_type
                before = self.init.save()
                consumed_before_loop = self.ctx.func.current_consumed_own_params.copy()
                ns_types_before_foreach = self._save_ns_var_types()
                with self.scopes.loop_scope() as inner_scope:
                    self.init.apply_loop_entry_facts(before)
                    with self.scopes.loop_var(inner_scope, stmt.var, elem_type, inner_scope.depth, is_foreach=True):
                        for s in stmt.body:
                            self.analyze_stmt(s)
                self.init.apply_loop_exit_facts(before)
                self.ctx.func.current_consumed_own_params = consumed_before_loop
                self._restore_ns_var_types(ns_types_before_foreach)
                self._sync_promoted_var_types()
                self._propagate_for_loop_scope(stmt, inner_scope, elem_type)
                for s in stmt.orelse:
                    self.analyze_stmt(s)
            else:
                iterable_type = self.expr.analyze_expr(stmt.iterable)
                is_readonly_iterable = isinstance(iterable_type, ReadonlyType)
                inner_iterable_type = unwrap_readonly(unwrap_own(unwrap_ref_type(iterable_type)))
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
                elem_type = self._infer_new_local_type(
                    stmt.var, elem_type, None, None,
                    line=(stmt.loc.line if stmt.loc else None),
                )
                stmt.elem_type = make_ref(elem_type)

                # Auto-consuming decision is deferred until after body analysis
                # (see below) so we know whether the loop var is mutated.

                is_direct_next_iter = builtin_modules.get_error_return_next_element_type(inner_iterable_type, registry=self.ctx.registry) is not None
                is_iter_based = builtin_modules.get_iter_element_type(inner_iterable_type, registry=self.ctx.registry) is not None
                is_protocol_iter = is_protocol_type(resolved_for_iter) and resolved_for_iter.qualified_name() in ("typing.Iterator", "typing.Iterable")
                before = self.init.save()
                consumed_before_loop = self.ctx.func.current_consumed_own_params.copy()
                ns_types_before_foreach = self._save_ns_var_types()
                with self.scopes.loop_scope() as inner_scope:
                    self.init.apply_loop_entry_facts(before)
                    # Track range facts for loop variable from range() calls
                    self._track_for_range_facts(stmt)
                    bt = self.ctx.func.borrow_tracker
                    if isinstance(stmt.iterable, TpyName):
                        bt.add_borrow(stmt.iterable.name, "__for_iter", BorrowKind.ITER)
                        self.ctx.func.loop_var_iterable[stmt.var] = stmt.iterable.name
                        # Protocol-typed and TypeParamRef params used as for-loop iterables
                        # require mutable access: .__next__() mutates iterator state.
                        # Mark them mutated so the generated param gets T& not const T&.
                        inner = unwrap_ref_type(unwrap_readonly(iterable_type))
                        if (isinstance(inner, (TypeParamRef,)) or is_protocol_type(inner)):
                            self.ctx.mark_param_mutated(stmt.iterable.name)
                    elif isinstance(stmt.iterable, TpyFieldAccess):
                        # Property getter: register ITER borrow via return_borrows_from
                        # on the receiver object (more precise than the field-path key).
                        gc = stmt.iterable.property_getter_call
                        if gc is not None:
                            fi_gc = gc.resolved_function_info
                            if fi_gc is not None and fi_gc.return_borrows_from:
                                for idx in fi_gc.return_borrows_from:
                                    if idx == -1 and gc.obj is not None:
                                        src = _borrow_storage_root(gc.obj)
                                        if src is not None:
                                            bt.add_borrow(src, "__for_iter", BorrowKind.ITER)
                                            self.ctx.func.loop_var_iterable[stmt.var] = src
                                    elif idx >= 0 and idx < len(gc.args):
                                        src = _borrow_storage_root(gc.args[idx])
                                        if src is not None:
                                            bt.add_borrow(src, "__for_iter", BorrowKind.ITER)
                                            self.ctx.func.loop_var_iterable[stmt.var] = src
                        else:
                            key = _storage_key(stmt.iterable)
                            if key is not None:
                                bt.add_borrow(key, "__for_iter", BorrowKind.ITER)
                                self.ctx.func.loop_var_iterable[stmt.var] = key
                    elif isinstance(stmt.iterable, (TpyCall, TpyMethodCall)):
                        # 8b: iterable is a call whose return borrows from source arg(s).
                        # Register ITER borrow directly on those source containers so that
                        # structural mutations during the loop generate conflict warnings.
                        fi_iter = stmt.iterable.resolved_function_info
                        if fi_iter is not None and fi_iter.return_borrows_from:
                            call_args = stmt.iterable.args
                            call_obj = getattr(stmt.iterable, 'obj', None)
                            for idx in fi_iter.return_borrows_from:
                                arg = None
                                if idx == -1 and call_obj is not None:
                                    src = _borrow_storage_root(call_obj)
                                    arg = call_obj
                                elif idx >= 0 and idx < len(call_args):
                                    src = _borrow_storage_root(call_args[idx])
                                    arg = call_args[idx]
                                else:
                                    src = None
                                if src is not None:
                                    bt.add_borrow(src, "__for_iter", BorrowKind.ITER)
                                    self.ctx.func.loop_var_iterable[stmt.var] = src
                                elif arg is not None and _is_dangling_temporary_arg(arg):
                                    # Call results returning non-value types are
                                    # materialized into named variables by codegen
                                    # (for by-reference passing), so they survive
                                    # the for-loop. Only warn for value-type temporaries
                                    # (e.g. str -> string_view conversion) where the
                                    # underlying storage is truly destroyed.
                                    is_materialized = False
                                    if isinstance(arg, (TpyCall, TpyMethodCall)):
                                        arg_fi = arg.resolved_function_info
                                        if arg_fi is not None and not arg_fi.return_type.is_value_type():
                                            is_materialized = True
                                    if not is_materialized:
                                        if idx == -1:
                                            detail = "temporary receiver object"
                                        else:
                                            detail = f"temporary argument '{fi_iter.params[idx].name}'"
                                        self.ctx.warning(
                                            f"Iterator borrows from {detail}; "
                                            f"the temporary is destroyed before iteration begins",
                                            stmt.iterable,
                                        )
                    if is_direct_next_iter or is_protocol_iter:
                        iter_depth = inner_scope.depth
                    elif is_iter_based:
                        # __iter__() either references the container's storage
                        # (NativeIterable types -- builtins like list/dict/str
                        # and span-backed types like SpanIter) or creates a
                        # fresh owned iterator (user-defined iterators).
                        # For the former, the loop-var lifetime is the
                        # container's; for the latter, it's loop-body scope.
                        #
                        # Known gap (no failing test): user records whose
                        # `__iter__()` returns `SpanIter` (e.g. ArrayList,
                        # Stack with `__iter__(self) -> SpanIter[T]`) are NOT
                        # `is_native_iterable`, but their iterator does
                        # reference the container's storage. Provenance
                        # tracking only activates for non-value loop vars
                        # from param-derived iterables, so the gap is
                        # currently invisible. If it surfaces, recover by
                        # also checking whether `__iter__()` returns a
                        # `SpanIter` here.
                        references_container = builtin_modules.is_native_iterable(
                            inner_iterable_type, registry=self.ctx.registry
                        )
                        if references_container:
                            if self.compat.is_lvalue(stmt.iterable):
                                iter_depth = self.scopes.get_expr_scope_depth(stmt.iterable)
                            else:
                                iter_depth = inner_scope.depth
                        else:
                            iter_depth = inner_scope.depth
                    elif self.compat.is_lvalue(stmt.iterable):
                        # For-each var references container's storage -- use container's depth.
                        iter_depth = self.scopes.get_expr_scope_depth(stmt.iterable)
                    else:
                        # For rvalue iterables (calls), C++ extends the temporary's lifetime
                        # to the for statement, but it dies when the loop ends. Use body depth
                        # so that escaping to any outer-scoped variable is caught.
                        iter_depth = inner_scope.depth
                    # Track provenance for loop vars whose type participates in
                    # provenance tracking, when iterating over a param-derived iterable.
                    track_loop_prov = (
                        _needs_provenance_tracking(unwrap_readonly(elem_type))
                        and self.compat.is_param_derived_expr(stmt.iterable)
                    )
                    if track_loop_prov:
                        self.init.add_loop_var_provenance(stmt.var)
                    self.ctx.func.mutated_loop_vars.discard(stmt.var)
                    self.ctx.func.consumed_loop_vars.discard(stmt.var)
                    self.ctx.func.deferred_loop_copy_warnings.pop(stmt.var, None)
                    with self.scopes.loop_var(inner_scope, stmt.var, elem_type, iter_depth, is_foreach=True):
                        for s in stmt.body:
                            self.analyze_stmt(s)
                    if track_loop_prov:
                        self.init.remove_loop_var_provenance(stmt.var)
                # Set const-ref binding when the loop var was never mutated.
                # mutated_loop_vars was cleared for stmt.var before entering the
                # loop body, so it only reflects mutations from this loop.
                # Only for non-value types or expensive-to-copy value types
                # (BigInt, String, tuples with expensive elements). Cheap
                # primitives (int32_t, bool, double, etc.) are better copied
                # into a register than referenced through a pointer.
                # For synthetic tuple-unpack loop vars, skip const binding when
                # the unpack has elements that need mutable references --
                # const tuple prevents T& bindings via std::get.
                unwrapped = unwrap_qualifiers(elem_type)
                worth_const_ref = (not unwrapped.is_value_type()
                                   or unwrapped.is_expensive_copy())
                needs_mut_unpack = False
                if stmt.is_tuple_unpack and stmt.body:
                    first = stmt.body[0]
                    if isinstance(first, TpyTupleUnpack) and any(first.is_ref):
                        needs_mut_unpack = True
                if (worth_const_ref
                        and stmt.var not in self.ctx.func.mutated_loop_vars
                        and stmt.var not in self.ctx.func.consumed_loop_vars
                        and not needs_mut_unpack):
                    stmt.const_loop_var = True
                # Auto-consuming iteration: use consuming __iter__ when the
                # container is at last use and elements are either mutated
                # or consumed (copied into owned storage via Own[T] params,
                # append, etc.). The container is dead after the loop, so
                # moving it into OwnIter is free (pointer swap). Consumed
                # elements become movable at last use, avoiding copies.
                loop_var_needs_ownership = (
                    stmt.var in self.ctx.func.mutated_loop_vars
                    or stmt.var in self.ctx.func.consumed_loop_vars
                )
                if (loop_var_needs_ownership
                        and not stmt.hoist_loop_var
                        and not unwrapped.is_value_type()
                        and isinstance(stmt.iterable, TpyName)
                        and id(stmt.iterable) in self.ctx.all_last_uses
                        and self.compat._is_owned_var(stmt.iterable.name)):
                    consuming_fi = self._find_consuming_iter(inner_iterable_type)
                    if consuming_fi is not None:
                        stmt.consuming_iter_fi = consuming_fi
                        # Suppress copy warnings for the loop variable --
                        # elements will be moved, not copied.
                        deferred = self.ctx.func.deferred_loop_copy_warnings.pop(stmt.var, None)
                        if deferred:
                            for idx in sorted(deferred, reverse=True):
                                del self.ctx.diagnostics[idx]
                # Also suppress copy warnings when elem_type is Own[T]
                # (e.g. Iterable[Own[T]] params) -- elements will be moved.
                if (stmt.consuming_iter_fi is None
                        and isinstance(elem_type, OwnType)):
                    deferred = self.ctx.func.deferred_loop_copy_warnings.pop(stmt.var, None)
                    if deferred:
                        for idx in sorted(deferred, reverse=True):
                            del self.ctx.diagnostics[idx]
                self.init.apply_loop_exit_facts(before)
                # Loop might not execute — consumption inside is not definite
                self.ctx.func.current_consumed_own_params = consumed_before_loop
                self._restore_ns_var_types(ns_types_before_foreach)
                self._sync_promoted_var_types()
                self._propagate_for_loop_scope(stmt, inner_scope, elem_type)
                for s in stmt.orelse:
                    self.analyze_stmt(s)
        elif isinstance(stmt, TpyBreak):
            if self.ctx.func.loop_depth == 0:
                raise self.ctx.error("'break' outside loop", stmt)
            self.init.mark_terminated()
        elif isinstance(stmt, TpyContinue):
            if self.ctx.func.loop_depth == 0:
                raise self.ctx.error("'continue' outside loop", stmt)
            self.init.mark_terminated()
        elif isinstance(stmt, TpyAssert):
            self.expr.analyze_expr(stmt.condition)
            self.narrowing.warn_truthy_value_optionals(stmt.condition)
            if stmt.message is not None:
                msg_type = self.expr.analyze_expr(stmt.message)
                if not is_any_str_type(msg_type):
                    raise self.ctx.error("assert message must be a string", stmt)
            then_type_facts, _ = self.narrowing.condition_type_facts(stmt.condition)
            ptr_nn, _ = self.narrowing.condition_ptr_null_facts(stmt.condition)
            range_true, _ = self.narrowing.condition_range_facts(stmt.condition)
            stmt.then_type_facts = self._filter_union_codegen_facts(then_type_facts)
            self.ctx.func.narrowed_types.update(then_type_facts)
            self.ctx.func.non_null_ptr_vars |= ptr_nn
            self._apply_range_facts(range_true)
        elif isinstance(stmt, TpyGlobal):
            self._analyze_global_stmt(stmt)
        elif isinstance(stmt, TpyRaise):
            self._analyze_raise(stmt)
        elif isinstance(stmt, TpyTry):
            self._analyze_try(stmt)
        elif isinstance(stmt, TpyMatch):
            self.match.analyze_match(stmt)
        elif isinstance(stmt, TpyWith):
            self._analyze_with(stmt)
        elif isinstance(stmt, TpyNonlocal):
            self._analyze_nonlocal(stmt)
        elif isinstance(stmt, TpyNestedDef):
            self._analyze_nested_def(stmt)

    def _apply_range_facts(self, facts: dict[str, 'ValueRange']) -> None:
        """Apply integer range facts, intersecting with any existing ranges."""
        for name, new_range in facts.items():
            existing = self.ctx.func.value_ranges.get(name)
            if existing is not None:
                self.ctx.func.value_ranges[name] = ValueRange.intersect(existing, new_range)
            else:
                self.ctx.func.value_ranges[name] = new_range

    def _track_for_range_facts(self, stmt: TpyForEach) -> None:
        """Set range facts for loop variable when iterating over range().

        Detects: range(len(arr)), range(N).
        """
        iterable = stmt.iterable
        if not isinstance(iterable, TpyCall) or iterable.func_name != "range":
            return

        args = iterable.args
        if len(args) == 1:
            arg = args[0]
            # range(len(arr)) -- symbolic bound
            if (isinstance(arg, TpyCall) and arg.func_name == "len"
                    and len(arg.args) == 1 and isinstance(arg.args[0], TpyName)):
                self.ctx.func.value_ranges[stmt.var] = ValueRange.for_range_index(
                    stop_len_of=arg.args[0].name,
                )
                return
            # range(N) -- literal bound
            if isinstance(arg, TpyIntLiteral):
                self.ctx.func.value_ranges[stmt.var] = ValueRange.for_range_index(
                    stop_literal=arg.value,
                )
                return
            # range(n) -- unknown bound, but still non-negative
            self.ctx.func.value_ranges[stmt.var] = ValueRange.for_range_index()

    def _filter_union_codegen_facts(
        self, facts: dict[str, TpyType],
    ) -> dict[str, TpyType]:
        """Keep union-origin, Literal, and protocol narrowing facts for codegen.

        Optional narrowing is handled implicitly by std::optional in C++,
        so only UnionType variables need explicit std::get<T> extraction.
        LiteralType facts are passed through for dead branch elimination.
        Protocol facts (e.g. Iterable[T] -> NativeIterable[T] via isinstance)
        feed protocol_narrowings so downstream dispatch sees the refined type.
        """
        return {
            name: ty for name, ty in facts.items()
            if (isinstance(unwrap_readonly(self.narrowing.declared_type_for_name(name)), UnionType)
                or isinstance(ty, LiteralType)
                or is_protocol_type(ty))
        }

    def _analyze_raise(self, stmt: TpyRaise) -> None:
        """Analyze a raise statement (return-tier, throw-tier, or bare re-raise)."""
        # Bare raise (re-raise)
        if stmt.exception_type is None and stmt.raise_expr is None:
            if self.ctx.in_except_tier is None:
                raise self.ctx.error(
                    "bare 'raise' is only valid inside an 'except' block", stmt)
            if self.ctx.in_except_tier == "return" and not self.ctx.in_except_has_binding:
                raise self.ctx.error(
                    "bare 'raise' in return-tier except requires 'as' binding "
                    "(e.g. 'except E as e') to capture the error value",
                    stmt)
            self.init.mark_terminated()
            return

        # Expression raise (general expression, e.g. raise <expr>)
        if stmt.raise_expr is not None:
            self._analyze_raise_expr(stmt)
            return

        func = self.ctx.func.current_function
        if not isinstance(func, TpyFunction):
            raise self.ctx.error(
                f"'raise {stmt.exception_type}' can only be used inside a function", stmt)

        bare_exc = stmt.exception_type.rsplit(".", 1)[-1] if "." in stmt.exception_type else stmt.exception_type
        record = self.ctx.registry.find_record(bare_exc)
        if not record:
            # Not a type -- check if it's a variable of exception type
            self._analyze_raise_name_as_expr(stmt)
            return

        qualified_exc = qualify_exception_name(
            stmt.exception_type, self.ctx.registry)
        is_cf = is_return_exception(qualified_exc)

        if is_cf:
            # Return-tier: must be inside @error_return(E) function with matching E
            if func.error_return is None:
                raise self.ctx.error(
                    f"'raise {stmt.exception_type}' requires "
                    f"@error_return({stmt.exception_type}) on the enclosing function",
                    stmt)
            if func.error_return != qualified_exc:
                raise self.ctx.error(
                    f"'raise {stmt.exception_type}' does not match "
                    f"@error_return({stmt.exception_type})", stmt)
        else:
            # Throw-tier: must inherit from Exception
            if not is_exception_type(stmt.exception_type, self.ctx.registry):
                raise self.ctx.error(
                    f"'{stmt.exception_type}' is not an exception type; "
                    f"it must inherit from Exception",
                    stmt)

        # Type-check constructor arguments against __init__ params
        if stmt.args:
            if record.has_init:
                min_args = sum(1 for _, _, d in record.init_params if d is None)
                max_args = len(record.init_params)
                if len(stmt.args) < min_args or len(stmt.args) > max_args:
                    expected = (f"{max_args}" if min_args == max_args
                                else f"{min_args} to {max_args}")
                    raise self.ctx.error(
                        f"'raise {stmt.exception_type}()' expects "
                        f"{expected} arguments, got {len(stmt.args)}",
                        stmt)
                for i, (arg, (pname, ptype, _)) in enumerate(
                        zip(stmt.args, record.init_params)):
                    arg_type = self.expr.analyze_expr_with_hint(arg, ptype)
                    stmt.args[i] = self.compat.coerce_expr(
                        arg, arg_type, ptype, f"argument '{pname}'",
                        coercion_ctx=CoercionContext.ARG)
            elif record.fields:
                raise self.ctx.error(
                    f"'{stmt.exception_type}' has data fields but no __init__; "
                    f"add __init__ to use 'raise {stmt.exception_type}(...)'",
                    stmt)
            else:
                raise self.ctx.error(
                    f"'raise {stmt.exception_type}()' does not accept arguments",
                    stmt)
        # Propagate qualified name to AST
        stmt.exception_type = qualified_exc
        self.init.mark_terminated()

    def _analyze_raise_expr(self, stmt: TpyRaise) -> None:
        """Analyze 'raise <expr>' where expr is a general expression."""
        func = self.ctx.func.current_function
        if not isinstance(func, TpyFunction):
            raise self.ctx.error(
                "'raise' can only be used inside a function", stmt)
        expr_type = self.expr.analyze_expr(stmt.raise_expr)
        type_name = self._raise_expr_type_name(expr_type, stmt)
        if is_return_exception(
                qualify_exception_name(type_name, self.ctx.registry)):
            raise self.ctx.error(
                f"'raise <expr>' cannot be used with ReturnException type "
                f"'{type_name}'; use direct 'raise {type_name}' inside "
                f"an @error_return function instead",
                stmt)
        if not is_exception_type(type_name, self.ctx.registry):
            raise self.ctx.error(
                f"cannot raise expression of type '{type_name}'; "
                f"it must inherit from Exception",
                stmt)
        self.init.mark_terminated()

    def _analyze_raise_name_as_expr(self, stmt: TpyRaise) -> None:
        """Handle 'raise Name' or 'raise Name(args)' where Name is not a type.

        Converts to expression raise if Name is a variable/call of exception type.
        """
        name = stmt.exception_type
        if stmt.args or stmt.is_call_form:
            # raise func() or raise func(args) -- convert to call expression
            call_expr = TpyCall(func=TpyName(name, loc=stmt.loc), args=stmt.args, loc=stmt.loc)
            stmt.raise_expr = call_expr
            stmt.exception_type = None
            stmt.args = []
            self._analyze_raise_expr(stmt)
            return
        # raise name -- convert to variable reference
        name_expr = TpyName(name=name, loc=stmt.loc)
        stmt.raise_expr = name_expr
        stmt.exception_type = None
        self._analyze_raise_expr(stmt)

    def _raise_expr_type_name(self, expr_type: TpyType, stmt: TpyRaise) -> str:
        """Extract the type name from a raise expression's type for validation."""
        t = unwrap_qualifiers(expr_type)
        if isinstance(t, NominalType) and not t.is_protocol:
            return t.name
        raise self.ctx.error(
            f"cannot raise expression of type '{expr_type}'; "
            f"expected an exception type",
            stmt)

    def _classify_try_tier(self, stmt: TpyTry) -> str:
        """Classify a try statement as 'return', 'throw', or 'finally_only'."""
        if not stmt.handlers:
            return "finally_only"

        has_cf = False
        has_throw = False
        has_bare = False
        for h in stmt.handlers:
            if h.exception_type is None:
                has_bare = True
                continue
            proto = self.ctx.registry.scan_by_short_name(h.exception_type)
            is_cf_catch_all = (
                proto is not None
                and f"{proto.module}.{proto.name}" == qnames.RETURN_EXCEPTION
            )
            if is_cf_catch_all or is_return_exception(
                    qualify_exception_name(
                        h.exception_type, self.ctx.registry)):
                has_cf = True
            else:
                has_throw = True

        if has_cf and has_throw:
            raise self.ctx.error(
                "cannot mix ReturnException and non-ReturnException exception types "
                "in the same try/except block", stmt)
        if has_cf and has_bare:
            raise self.ctx.error(
                "bare 'except:' cannot be mixed with ReturnException handlers", stmt)

        return "return" if has_cf else "throw"

    def _analyze_try(self, stmt: TpyTry) -> None:
        """Analyze a try/except/else/finally statement.

        Classifies the try block into tiers:
        - 'return': ReturnException handlers -> goto-based dispatch (existing)
        - 'throw': non-ReturnException handlers -> C++ try/catch
        - 'finally_only': no handlers, just finally cleanup
        """
        tier = self._classify_try_tier(stmt)
        stmt.tier = tier

        if tier == "finally_only":
            self._analyze_try_finally_only(stmt)
        elif tier == "return":
            self._analyze_try_return(stmt)
        else:
            self._analyze_try_throw(stmt)

    def _analyze_try_finally_only(self, stmt: TpyTry) -> None:
        """Analyze try/finally with no except handlers."""
        scope_before = set(self.ctx.func.current_scope.bindings.keys())
        for s in stmt.try_body:
            self.analyze_stmt(s)
        try_bindings = dict(self.ctx.func.current_scope.bindings)
        prev_in_finally = self.ctx.in_finally
        self.ctx.in_finally = True
        for s in stmt.finally_body:
            self.analyze_stmt(s)
        self.ctx.in_finally = prev_in_finally
        # Hoist try-body variables so they're accessible in the finally body.
        # Mark as hoisted so non-value types use pointer indirection.
        branch_new = set(try_bindings.keys()) - scope_before
        predecl = branch_new - self.ctx.func.global_declarations
        if predecl:
            self.ctx.if_branch_decls[id(stmt)] = {
                name: try_bindings[name]
                for name in sorted(predecl)
                if name in try_bindings
            }
            self.ctx.func.hoisted_vars |= predecl

    def _analyze_try_return(self, stmt: TpyTry) -> None:
        """Analyze return-tier try/except (ReturnException, goto-based)."""
        # Return tier supports single handler or ReturnException catch-all
        if len(stmt.handlers) != 1:
            raise self.ctx.error(
                "return-tier (ReturnException) try/except supports only a single handler", stmt)
        handler = stmt.handlers[0]

        scope_before = set(self.ctx.func.current_scope.bindings.keys())
        before = self.init.save()
        consumed_before = self.ctx.func.current_consumed_own_params.copy()
        bindings_before = dict(self.ctx.func.current_scope.bindings)
        ns_types_before = self._save_ns_var_types()

        # Detect except ReturnException catch-all
        proto = self.ctx.registry.scan_by_short_name(handler.exception_type)
        is_return_exception_catch_all = (
            proto is not None
            and f"{proto.module}.{proto.name}" == qnames.RETURN_EXCEPTION
        )

        if is_return_exception_catch_all and handler.binding:
            raise self.ctx.error(
                "'except ReturnException as' binding is not supported", stmt)

        if not is_return_exception_catch_all:
            bare_exc = handler.exception_type.rsplit(".", 1)[-1] if "." in handler.exception_type else handler.exception_type
            if not self.ctx.registry.find_record(bare_exc):
                raise self.ctx.error(
                    f"Unknown error type '{handler.exception_type}'", stmt)

        # Set try context so call analysis can allow error_return calls
        prev_try_error = self.ctx.try_except_error_type
        if is_return_exception_catch_all:
            self.ctx.try_except_error_type = "*"
        else:
            self.ctx.try_except_error_type = qualify_exception_name(
                handler.exception_type, self.ctx.registry)

        for s in stmt.try_body:
            self.analyze_stmt(s)

        self.ctx.try_except_error_type = prev_try_error

        for s in stmt.else_body:
            self.analyze_stmt(s)
        then_state = self.init.save()
        consumed_after_then = self.ctx.func.current_consumed_own_params.copy()
        then_terminated = self.ctx.func.init_terminated
        try_bindings = dict(self.ctx.func.current_scope.bindings)

        # Restore to pre-try state for except branch
        self.ctx.func.current_scope.bindings = dict(bindings_before)
        self._restore_ns_var_types(ns_types_before)
        self.init.restore(before)
        self.ctx.func.current_consumed_own_params = consumed_before.copy()

        # Register except binding -- use find_record_by_qname because
        # handler.exception_type may be module-qualified (e.g. from macros).
        if handler.binding:
            exc_record = self.ctx.registry.find_record_by_qname(handler.exception_type)
            if exc_record:
                exc_type = NominalType(exc_record.name, _module_qname=exc_record.qualified_name())
                self.ctx.func.current_scope.bindings[handler.binding] = exc_type
                self.init.mark_assigned(handler.binding)

        # Set in_except_tier for bare raise validation
        prev_except_tier = self.ctx.in_except_tier
        prev_has_binding = self.ctx.in_except_has_binding
        self.ctx.in_except_tier = "return"
        self.ctx.in_except_has_binding = handler.binding is not None
        for s in handler.body:
            self.analyze_stmt(s)
        self.ctx.in_except_tier = prev_except_tier
        self.ctx.in_except_has_binding = prev_has_binding

        if handler.binding and handler.binding in self.ctx.func.current_scope.bindings:
            del self.ctx.func.current_scope.bindings[handler.binding]
        else_state = self.init.save()
        consumed_after_else = self.ctx.func.current_consumed_own_params.copy()
        else_terminated = self.ctx.func.init_terminated

        self.init.merge_branches(then_state, else_state)
        self._merge_consumed_own(
            then_terminated, else_terminated,
            consumed_after_then, consumed_after_else)

        # Analyze finally body (runs on all paths, doesn't affect branch merging)
        prev_in_finally = self.ctx.in_finally
        self.ctx.in_finally = True
        for s in stmt.finally_body:
            self.analyze_stmt(s)
        self.ctx.in_finally = prev_in_finally

        # Hoist all declarations for goto-based dispatch
        all_bindings = dict(try_bindings)
        all_bindings.update(self.ctx.func.current_scope.bindings)
        branch_new = set(all_bindings.keys()) - scope_before
        if handler.binding:
            branch_new.discard(handler.binding)
        predecl = branch_new - self.ctx.func.global_declarations
        if predecl:
            self.ctx.if_branch_decls[id(stmt)] = {
                name: all_bindings[name]
                for name in sorted(predecl)
                if name in all_bindings
            }

    def _analyze_try_throw(self, stmt: TpyTry) -> None:
        """Analyze throw-tier try/except (C++ try/catch)."""
        scope_before = set(self.ctx.func.current_scope.bindings.keys())
        before = self.init.save()
        consumed_before = self.ctx.func.current_consumed_own_params.copy()
        bindings_before = dict(self.ctx.func.current_scope.bindings)
        ns_types_before = self._save_ns_var_types()

        # Validate all handlers and save resolved records for binding.
        # Qualify before lookup so dotted forms (`re.error`) bypass any local
        # class with the same bare name.
        handler_records: list[RecordInfo | None] = []
        for h in stmt.handlers:
            if h.exception_type is not None:
                qualified = qualify_exception_name(
                    h.exception_type, self.ctx.registry)
                record = self.ctx.registry.find_record_by_qname(qualified)
                if not record:
                    raise self.ctx.error(
                        f"Unknown exception type '{h.exception_type}'", stmt)
                if not is_exception_type(qualified, self.ctx.registry):
                    raise self.ctx.error(
                        f"'{h.exception_type}' is not an exception type; "
                        f"it must inherit from Exception", stmt)
                handler_records.append(record)
                h.exception_type = qualified
            else:
                handler_records.append(None)

        # Analyze try body
        for s in stmt.try_body:
            self.analyze_stmt(s)
        # Capture try-body bindings BEFORE else (else vars are scoped to the
        # if(__ok) block in C++ and must not leak into post-try scope)
        try_bindings = dict(self.ctx.func.current_scope.bindings)

        # Analyze else body
        for s in stmt.else_body:
            self.analyze_stmt(s)
        then_state = self.init.save()
        consumed_after_then = self.ctx.func.current_consumed_own_params.copy()
        then_terminated = self.ctx.func.init_terminated

        # Analyze each except handler as a separate branch from pre-try state
        handler_states: list[tuple] = []
        for i, h in enumerate(stmt.handlers):
            self.ctx.func.current_scope.bindings = dict(bindings_before)
            self._restore_ns_var_types(ns_types_before)
            self.init.restore(before)
            self.ctx.func.current_consumed_own_params = consumed_before.copy()

            record = handler_records[i]
            if h.binding and record is not None:
                exc_type = NominalType(record.name, _module_qname=record.qualified_name())
                self.ctx.func.current_scope.bindings[h.binding] = exc_type
                self.init.mark_assigned(h.binding)

            prev_except_tier = self.ctx.in_except_tier
            self.ctx.in_except_tier = "throw"
            for s in h.body:
                self.analyze_stmt(s)
            self.ctx.in_except_tier = prev_except_tier

            if h.binding and h.binding in self.ctx.func.current_scope.bindings:
                del self.ctx.func.current_scope.bindings[h.binding]

            handler_states.append((
                self.init.save(),
                self.ctx.func.current_consumed_own_params.copy(),
                self.ctx.func.init_terminated,
            ))

        # Merge all branches: success path + all handler paths
        all_states = [then_state] + [s for s, _, _ in handler_states]
        all_terminated = [then_terminated] + [t for _, _, t in handler_states]
        all_consumed = [consumed_after_then] + [c for _, c, _ in handler_states]

        # Multi-branch merge: start from first, merge pairwise
        merged = all_states[0]
        for s in all_states[1:]:
            self.init.merge_branches(merged, s)
            merged = self.init.save()

        # Merge consumed Own[T] params
        if all(all_terminated):
            result_consumed: set[str] = set()
            for c in all_consumed:
                result_consumed |= c
        elif any(all_terminated):
            result_consumed = set()
            for t, c in zip(all_terminated, all_consumed):
                if not t:
                    result_consumed = result_consumed & c if result_consumed else c.copy()
        else:
            result_consumed = all_consumed[0].copy()
            for c in all_consumed[1:]:
                result_consumed &= c
        self.ctx.func.current_consumed_own_params = result_consumed

        # Analyze finally body
        prev_in_finally = self.ctx.in_finally
        self.ctx.in_finally = True
        for s in stmt.finally_body:
            self.analyze_stmt(s)
        self.ctx.in_finally = prev_in_finally

        # Throw-tier uses C++ try/catch with proper scoping -- no goto hoisting
        # needed in general. BUT try-body variables must be hoisted when:
        # - finally body exists: finally code runs after the try/catch block
        # - else body exists: else code is emitted after the try/catch block
        # - code continues after try/except and try-body vars are in scope
        #   (all handlers terminate, so post-try code uses try-body vars)
        all_handlers_terminate = all(t for _, _, t in handler_states)
        needs_hoist = (stmt.finally_body or stmt.else_body
                       or (all_handlers_terminate and not self.ctx.func.init_terminated))
        self.ctx.func.current_scope.bindings = dict(try_bindings)
        if needs_hoist:
            branch_new = set(try_bindings.keys()) - scope_before
            for h in stmt.handlers:
                if h.binding:
                    branch_new.discard(h.binding)
            predecl = branch_new - self.ctx.func.global_declarations
            if predecl:
                self.ctx.if_branch_decls[id(stmt)] = {
                    name: try_bindings[name]
                    for name in sorted(predecl)
                    if name in try_bindings
                }
                self.ctx.func.hoisted_vars |= predecl

    def _merge_consumed_own(self, then_terminated: bool, else_terminated: bool,
                            consumed_then: set[str], consumed_else: set[str]) -> None:
        """Merge consumed Own[T] params from two branches."""
        if then_terminated and else_terminated:
            self.ctx.func.current_consumed_own_params = consumed_then | consumed_else
        elif then_terminated:
            self.ctx.func.current_consumed_own_params = consumed_else
        elif else_terminated:
            self.ctx.func.current_consumed_own_params = consumed_then
        else:
            self.ctx.func.current_consumed_own_params = consumed_then & consumed_else

    def _analyze_with(self, stmt: TpyWith) -> None:
        """Analyze a with statement (context managers).

        Checks that each context expression has __enter__ and __exit__ methods.
        Types the as-binding from __enter__ return type. The as-variable is
        visible after the with block (matching CPython semantics).
        """
        for item in stmt.items:
            ctx_type = unwrap_own(unwrap_ref_type(self.expr.analyze_expr(item.context_expr)))

            # Look up __enter__ and __exit__ on the context manager type
            record_info = self.ctx.registry.get_record_for_type(ctx_type)
            err_node = item.context_expr
            if record_info is None:
                raise self.ctx.error(
                    f"Type '{ctx_type}' cannot be used as a context manager"
                    f" (not a record type)", err_node)

            enter_overloads = record_info.methods.get("__enter__")
            if not enter_overloads:
                raise self.ctx.error(
                    f"Type '{ctx_type}' cannot be used as a context manager"
                    f" (missing __enter__ method)", err_node)

            exit_overloads = record_info.methods.get("__exit__")
            if not exit_overloads:
                raise self.ctx.error(
                    f"Type '{ctx_type}' cannot be used as a context manager"
                    f" (missing __exit__ method)", err_node)

            # Get return type of __enter__() -- use the first overload
            enter_info = enter_overloads[0]
            enter_type = unwrap_ref_type(enter_info.return_type)
            item.enter_type = enter_type

            # Register the as-variable if present
            if item.target is not None:
                resolved = self._infer_new_local_type(
                    item.target, enter_type, None, None,
                    line=(stmt.loc.line if stmt.loc else None),
                )
                item.enter_type = resolved
                self.ctx.func.current_scope.define(item.target, resolved)
                self.init.mark_assigned(item.target)

        # Analyze the body -- track new variable declarations so codegen
        # can pre-declare them outside the guard {} scope (C++ scoping).
        scope_before = set(self.ctx.func.current_scope.bindings.keys())

        for s in stmt.body:
            self.analyze_stmt(s)

        # All variables first declared inside the body need pre-declaration
        # since codegen wraps the body in try {} for the with's cleanup pattern.
        branch_new = set(self.ctx.func.current_scope.bindings.keys()) - scope_before
        predecl = branch_new - self.ctx.func.global_declarations
        if predecl:
            self.ctx.if_branch_decls[id(stmt)] = {
                name: self.ctx.func.current_scope.lookup(name)
                for name in sorted(predecl)
                if name in self.ctx.func.current_scope.bindings
            }

    # --- Nested def / nonlocal ---

    def _analyze_nonlocal(self, stmt: TpyNonlocal) -> None:
        """Analyze a nonlocal declaration."""
        if not self.ctx.func.in_nested_def:
            raise self.ctx.error(
                "'nonlocal' is only valid inside a nested function", stmt)
        for name in stmt.names:
            if name not in self.ctx.func.outer_scope_locals:
                raise self.ctx.error(
                    f"No binding for nonlocal '{name}' found in enclosing scope",
                    stmt)
            self.ctx.func.current_nonlocal_names.add(name)

    def _prescan_and_analyze_body(
        self,
        func: TpyFunction,
        params: list[tuple[str, TpyType]],
        scope: 'Scope',
        ns: 'Namespace | None',
    ) -> ScanResult:
        """Shared function body analysis: bind params, prescan, analyze statements.

        Used by both _analyze_function (analyzer.py) and _analyze_nested_def.
        Callers handle scope creation, type resolution, and post-processing.
        """
        # Bind params to scope with Ref for non-value types.
        # analyze_expr strips Ref from return values, so downstream sema
        # sees bare types.  Ref on scope types enables provenance-aware
        # code (assignment warnings, local scope propagation).
        param_names: set[str] = set()
        for pname, ptype in params:
            param_names.add(pname)
            scope_type = make_ref(ptype)
            scope.define(pname, scope_type)
            self.ctx.func.var_scope_depth[pname] = scope.depth
            self.ctx.func.definitely_assigned.add(pname)
            if ns:
                ns.bind_variable(pname, scope_type)

        # Param tracking for mutation analysis
        self.ctx.func.current_param_names = param_names
        self.ctx.func.current_param_name_to_idx = {p: i for i, (p, _) in enumerate(params)}
        # For non-static methods, include "self" in the param index map with
        # sentinel -1 so that _record_mutation_call_edges can track self
        # flowing through free function calls (e.g., helper(self)).
        # Phase 2 (_resolve_single) converts caller_idx == -1 to self_mutated.
        if func.is_method and not func.is_staticmethod:
            self.ctx.func.current_param_name_to_idx["self"] = -1

        # Prescan for reassigned variables + last-use liveness
        scan = scan_reassigned_vars(func.body, pre_declared=param_names)
        self.ctx.all_last_uses |= analyze_last_uses(func.body, scan.alias_sources)
        self.ctx.func.current_reassigned_vars = scan.reassigned.copy()
        self.ctx.func.current_lvalue_reassigned = scan.lvalue_reassigned.copy()
        self.ctx.func.current_aug_assigned_vars = scan.aug_assigned.copy()

        # Analyze body
        for stmt in func.body:
            self.analyze_stmt(stmt)

        return scan

    def _analyze_nested_def(self, stmt: TpyNestedDef) -> None:
        """Analyze a nested function definition."""
        func = stmt.func

        if self.ctx.func.in_nested_def:
            raise self.ctx.error(
                "Nested functions cannot contain further nested functions",
                stmt)

        # Collect outer locals available for capture
        outer_locals = self.ctx.func.definitely_assigned.copy()

        # Pre-scan nonlocal declarations (at any nesting depth) to bind
        # them in the inner scope before body analysis begins.
        nonlocal_names: set[str] = set()
        self._collect_nonlocal_names(func.body, nonlocal_names)

        # Resolve param and return types
        resolved_params_bare = [(p, self.type_ops.resolve_type(t)) for p, t in func.params]
        func.params = [(p, make_ref(t)) for p, t in resolved_params_bare]
        params = resolved_params_bare
        param_names = {p for p, _ in params}
        return_type = self.type_ops.resolve_type(func.return_type)
        func.return_type = make_ref(return_type)

        # Analyze body in isolated scope
        with self.scopes.nested_def_scope(func) as inner_scope:
            self.ctx.func.outer_scope_locals = outer_locals

            # Add nonlocal names to inner scope with types from outer
            for name in nonlocal_names:
                outer_type = inner_scope.parent.lookup(name) if inner_scope.parent else None
                if outer_type is not None:
                    inner_scope.define(name, outer_type)
                    self.ctx.func.definitely_assigned.add(name)
                    if self.ctx.func.current_ns:
                        self.ctx.func.current_ns.bind_variable(name, outer_type)

            self._prescan_and_analyze_body(func, params, inner_scope, self.ctx.func.current_ns)

            # Use the authoritative nonlocal set from body analysis
            # (covers nonlocal declarations at any nesting depth)
            nonlocal_names = self.ctx.func.current_nonlocal_names.copy()
        stmt.nonlocal_names = nonlocal_names

        # Compute captures: free variables that come from outer scope
        free_names = _collect_body_name_refs(func.body)
        local_defs = _collect_body_local_defs(func.body)
        captured = sorted(
            (free_names - param_names - local_defs - nonlocal_names) & outer_locals
        )
        # Nonlocal names are also captures (mutable references)
        for name in sorted(nonlocal_names):
            if name not in captured:
                captured.append(name)
        stmt.captured_names = captured

        # Create FunctionInfo and register as local function
        param_infos = [ParamInfo(name=pname, type=ptype) for pname, ptype in params]
        fi = FunctionInfo(
            name=func.name,
            params=param_infos,
            return_type=return_type,
        )

        # Bind as FUNCTION in the local namespace
        if self.ctx.func.current_ns:
            self.ctx.func.current_ns.bind_function(fi)
        self.ctx.func.definitely_assigned.add(func.name)

        # Track for escape analysis
        self.ctx.func.nested_def_names.add(func.name)
        self.ctx.func.nested_def_nodes[func.name] = stmt

    def _collect_nonlocal_names(self, stmts: list, names: set[str]) -> None:
        """Recursively collect nonlocal declarations from all nesting depths."""
        for s in stmts:
            if isinstance(s, TpyNonlocal):
                names.update(s.names)
            elif not isinstance(s, TpyNestedDef):
                for body in s.sub_bodies():
                    self._collect_nonlocal_names(body, names)

    def _resolve_literal_type(self, t: TpyType) -> TpyType:
        """Resolve IntLiteralType/FloatLiteralType to concrete types."""
        if isinstance(t, IntLiteralType):
            return self.ctx.default_int_for_literal(t)
        if isinstance(t, FloatLiteralType):
            return FLOAT
        return t

    def _propagate_loop_body_vars(self, stmt: TpyStmt,
                                   inner_scope: 'Scope',
                                   skip_var: str | None = None) -> None:
        """Store body-declared variables from a for-loop scope as pending.

        Variables are lazily promoted to the parent scope when first
        referenced after the loop.

        skip_var: loop variable name to exclude (already handled by caller).
        """
        for name, var_type in inner_scope.bindings.items():
            if name == skip_var:
                continue
            if name in self.ctx.func.global_declarations:
                continue
            resolved = self._resolve_literal_type(var_type)
            self.ctx.func.pending_loop_vars[name] = (resolved, stmt, None)

        # Re-parent any pending vars from nested loops to this (outer) loop,
        # so they get pre-declared before this loop if referenced after it.
        for name, (vtype, loop_stmt, orig_stmt) in list(self.ctx.func.pending_loop_vars.items()):
            if loop_stmt is not stmt and name not in inner_scope.bindings:
                self.ctx.func.pending_loop_vars[name] = (vtype, stmt, orig_stmt)

    def _propagate_for_loop_scope(self, stmt: TpyForEach,
                                   inner_scope: 'Scope',
                                   elem_type: TpyType) -> None:
        """Store for-loop variable and body-declared variables as pending."""
        # Loop variable (skip synthetic tuple-unpack vars in non-generators;
        # generators need the synthetic var as a struct field)
        var_name = stmt.var
        is_generator = (isinstance(self.ctx.func.current_function, TpyFunction)
                        and self.ctx.func.current_function.is_generator)
        if ((not stmt.is_tuple_unpack or is_generator)
                and var_name not in self.ctx.func.global_declarations):
            resolved = self._resolve_literal_type(elem_type)
            self.ctx.func.pending_loop_vars[var_name] = (resolved, stmt, stmt)

        self._propagate_loop_body_vars(stmt, inner_scope, skip_var=var_name)

    def _analyze_global_stmt(self, stmt: TpyGlobal) -> None:
        """Analyze a `global x, y` statement."""
        # Must be inside a function, not at module level
        if self.ctx.is_top_level or isinstance(self.ctx.func.current_function, type(MODULE_INIT_CONTEXT)):
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
            func = self.ctx.func.current_function
            if isinstance(func, TpyFunction):
                for pname, _ in func.params:
                    if pname == name:
                        raise self.ctx.error(
                            f"name '{name}' is a parameter and cannot be declared global", stmt)
            self.ctx.func.global_declarations.add(name)

    def validate_compile_time_constant(
        self, init: TpyExpr, target_type: 'TpyType | None', label: str, loc: object,
    ) -> None:
        """Validate that `init` is a compile-time constant; raise a uniform
        diagnostic otherwise. Shared between module-level `Final` decls and
        class constants -- callers pass `label` (e.g. `"Final variable 'X'"`
        or `"class constant 'C.X'"`) to customize the noun phrase.
        """
        bad = self._find_nonconstant_leaf(init, target_type)
        if bad is None:
            return
        prefix = f"{label} requires a compile-time constant initializer"
        if isinstance(bad, TpyName) and bad.name in self.ctx.user_imported_variables:
            src_mod, _ = self.ctx.user_imported_variables[bad.name]
            raise self.ctx.error(
                f"{prefix}; cross-module Final references are not yet supported "
                f"('{bad.name}' is imported from '{src_mod}')",
                loc,
            )
        if bad is not init and isinstance(bad, TpyName):
            raise self.ctx.error(
                f"{prefix}; '{bad.name}' is not a Final constant",
                loc,
            )
        raise self.ctx.error(prefix, loc)

    def _find_nonconstant_leaf(self, expr: TpyExpr, target_type: 'TpyType | None' = None) -> 'TpyExpr | None':
        """Find the first non-constant sub-expression in a Final initializer.

        Returns None if the expression is a valid compile-time constant,
        or the offending sub-expression otherwise.

        Accepts: literals, unary ops on constant operands, references to other
        Final globals, @call_macro expansions that reduce to a constant,
        primitive type constructor calls with constant args, binary ops on
        constant operands (when target_type is numeric/bool), and tuple
        literals with all-constant elements.
        """
        # Unwrap compile-time macro expansions
        macro_exp = getattr(expr, "macro_expansion", None)
        if macro_exp is not None:
            return self._find_nonconstant_leaf(macro_exp, target_type)
        if isinstance(expr, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyStrLiteral)):
            return None
        if isinstance(expr, TpyUnaryOp):
            return self._find_nonconstant_leaf(expr.operand, target_type)
        if isinstance(expr, TpyName) and expr.name in self.ctx.analyzed_finals:
            return None
        if isinstance(expr, TpyBinOp) and is_numeric_type(target_type):
            return (self._find_nonconstant_leaf(expr.left, target_type)
                    or self._find_nonconstant_leaf(expr.right, target_type))
        # Primitive type constructor: Float32(0.5), Int64(SOME_FINAL), etc.
        # Identified by: resolved to an __init__ method, result is a primitive.
        if isinstance(expr, TpyCall) and len(expr.args) == 1:
            fi = expr.resolved_function_info
            if fi is not None and fi.name == '__init__':
                result_type = expr.call_type or self.ctx.get_expr_type(expr)
                if is_numeric_type(result_type) or is_char_type(result_type):
                    return self._find_nonconstant_leaf(expr.args[0], result_type)
        if isinstance(expr, TpyTupleLiteral):
            if isinstance(target_type, TupleType) and len(target_type.element_types) == len(expr.elements):
                for e, t in zip(expr.elements, target_type.element_types):
                    bad = self._find_nonconstant_leaf(e, t)
                    if bad is not None:
                        return bad
                return None
            for e in expr.elements:
                bad = self._find_nonconstant_leaf(e)
                if bad is not None:
                    return bad
            return None
        return expr

    def _check_nonvalue_rebinding(self, name: str, node: TpyStmt) -> None:
        """Error if reassigning a non-value-type param, loop variable, or global."""
        existing_type = self.ctx.func.current_scope.lookup(name)
        if existing_type is None or unwrap_readonly(existing_type).is_value_type():
            return
        # Check function parameters
        func = self.ctx.func.current_function
        if isinstance(func, TpyFunction):
            for pname, ptype in func.params:
                ptype_bare = unwrap_ref_type(ptype)
                if pname == name and not unwrap_readonly(ptype_bare).is_value_type():
                    raise self.ctx.error(
                        f"Cannot reassign parameter '{name}' of type '{unwrap_readonly(ptype_bare)}'; "
                        f"assign to a new local variable instead",
                        node
                    )
        # Check for-each loop variables
        if name in self.ctx.func.loop_vars:
            raise self.ctx.error(
                f"Cannot reassign loop variable '{name}' of type '{existing_type}'; "
                f"assign to a new local variable instead",
                node
            )
        # Check global-declared non-value-type variables
        if name in self.ctx.func.global_declarations:
            raise self.ctx.error(
                f"Cannot reassign global variable '{name}' of non-value type '{existing_type}'",
                node
            )

    def _infer_new_local_view_type(
        self, name: str, var_type: TpyType,
        init_expr: TpyExpr | None, init_type: TpyType | None,
        line: int | None, family: ViewTypeFamily,
    ) -> TpyType:
        """Create a pending view type for a new local of the given family."""
        var_id = self.ctx.next_view_var_id(family)
        vars_reg = self.ctx.view_vars(family)
        var_map = self.ctx.view_var_map(family)

        if isinstance(var_type, PendingViewType):
            # Alias from another pending view type -- collect ALL leaf sources.
            # For `x = a or b` both a and b are sources; if either resolves to
            # owned, x must too (otherwise x would be a view into a
            # potentially-reallocated buffer).
            if init_expr is not None:
                source_ids = [t.var_id for t in collect_pending_source_types(self.ctx, init_expr)
                              if isinstance(t, family.pending_type_class)]
            else:
                source_ids = [var_type.var_id]
            info = ViewVarInfo(var_id=var_id, variable_name=name,
                               decl_line=line, source_var_ids=source_ids)
        else:
            # Fresh from owned type (str or bytes)
            if init_expr is not None:
                is_owned = not self.deduction.is_view_compatible_source(init_expr, init_type)
            else:
                is_owned = False
            # Track source storage for subscript/field views so that
            # mutations on the source fall back to the owned type.
            source_storage: str | None = None
            if not is_owned and init_expr is not None:
                unwrapped_init = init_expr.expr if isinstance(init_expr, TpyCoerce) else init_expr
                if isinstance(unwrapped_init, (TpySubscript, TpyFieldAccess)):
                    root = _borrow_storage_root(unwrapped_init)
                    if root is not None:
                        source_storage = self.ctx.func.borrow_tracker.effective_storage(root)
            info = ViewVarInfo(var_id=var_id, variable_name=name,
                               decl_line=line, initialized_from_owned=is_owned,
                               source_storage=source_storage)
            if source_storage is not None:
                self.ctx.view_source_borrows_map(family).setdefault(source_storage, set()).add(var_id)

        vars_reg[var_id] = info
        var_map[name] = var_id
        self.ctx.view_pending_resolutions(family).append(var_id)
        return family.pending_type_class(var_id)

    def _infer_new_local_type(
        self, name: str, var_type: TpyType,
        init_expr: TpyExpr | None, init_type: TpyType | None,
        line: int | None,
    ) -> TpyType:
        """Apply deferred type inference for a new local variable.

        Handles view-type families (str/bytes -> PendingViewType) and
        PendingListType alias logic.  Skipped at module top-level.

        When init_expr is None (for-loop var, tuple unpack), the source is
        considered view-compatible (initialized_from_owned=False) because
        the container outlives the loop/unpack scope.
        """
        if self.ctx.is_top_level:
            return var_type
        # Float literals resolve to float64 as new local variables
        if isinstance(var_type, FloatLiteralType):
            return FLOAT

        # View-type families (str/bytes): create pending view type for deferred resolution
        family = _view_family_for_type(var_type)
        if family is not None:
            return self._infer_new_local_view_type(name, var_type, init_expr, init_type, line, family)
        elif (isinstance(var_type, PendingListType)
                and init_expr is not None and isinstance(init_expr, TpyName)):
            return self.deduction.register_list_alias(
                name, var_type,
                decl_line=line,
            )

        return var_type

    def _analyze_yield(self, stmt: TpyYield) -> None:
        """Analyze a yield statement in a generator function."""
        func = self.ctx.func.current_function
        if func is None or not func.is_generator:
            raise self.ctx.error("'yield' can only be used inside a generator function", stmt)
        elem_type = func.generator_yield_type
        assert elem_type is not None
        yield_type = self.expr.analyze_expr_with_hint(stmt.value, elem_type)
        stmt.value = self.compat.coerce_expr(
            stmt.value, yield_type, elem_type, "yield value",
            coercion_ctx=CoercionContext.RETURN)
        # Assign a unique yield state number
        self.ctx._yield_counter += 1
        self.ctx.generator_yield_states[id(stmt)] = self.ctx._yield_counter

    def _analyze_var_decl(self, stmt: TpyVarDecl) -> None:
        """Analyze a variable declaration."""
        # In nested defs, assigning to an outer variable requires nonlocal
        if (self.ctx.func.in_nested_def
                and stmt.name in self.ctx.func.outer_scope_locals
                and stmt.name not in self.ctx.func.current_nonlocal_names):
            raise self.ctx.error(
                f"Cannot assign to '{stmt.name}' in nested function"
                f" without 'nonlocal' declaration",
                stmt)
        # Type alias expansion: replace NominalType("Shape") with the alias
        # target (one level; recurses into the target's inner types; _seen
        # guards self-referential aliases). Must run before `resolve_type`
        # because alias targets can contain further type references that
        # resolve_type wants to process uniformly. Enum substitution is
        # handled by `resolve_type` itself -- it replaces bare
        # NominalType("Color") placeholders with the registered enum
        # NominalType (which carries `_module_qname`), whether the enum
        # appears directly or is the target of a resolved alias.
        if stmt.type and self.ctx.registry.type_aliases:
            registry = self.ctx.registry
            recursive_names = self.ctx.recursive_union_names

            def _expand_aliases(t: TpyType, _seen: frozenset[str] = frozenset()) -> TpyType:
                # Alias references are bare parser NominalType placeholders
                # (no _module_qname, no TypeDef entry) -- the qname guard
                # enforces "unresolved only," matching `_resolve_alias` in
                # analyzer.py. `get_type_alias` is the specific check.
                if (isinstance(t, NominalType)
                        and not t.is_protocol
                        and not t._module_qname
                        and t.name not in _seen
                        and t.name not in recursive_names):
                    alias = registry.get_type_alias(t.name)
                    if alias is not None:
                        new_seen = _seen | {t.name}
                        return alias.map_inner_types(
                            lambda inner: _expand_aliases(inner, new_seen)
                        )
                return t.map_inner_types(lambda inner: _expand_aliases(inner, _seen))

            stmt.type = _expand_aliases(stmt.type)

        # resolve_type sets is_protocol flag, upgrades NominalType -> TypeParamRef
        # in generic scopes, substitutes enum placeholders, and handles
        # compile-time-only aliases (FStr etc.).
        if stmt.type:
            stmt.type = self.type_ops.resolve_type(stmt.type)

        # Validate the type annotation if present
        # Allow TypeParamRef inside generic functions or generic record methods
        if stmt.type:
            try:
                in_generic = bool(
                    (isinstance(self.ctx.func.current_function, TpyFunction) and self.ctx.func.current_function.type_params)
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

        # Fn[...] is only valid in parameter position
        if stmt.type and contains_fn_type(stmt.type):
            raise self.ctx.error(
                "Fn type is only valid in parameter position. "
                "Use Callable for fields, returns, and locals",
                stmt
            )

        # Final[T] validation
        # Detect FinalType from annotation (covers function-level where register_globals didn't run)
        if stmt.type and isinstance(stmt.type, FinalType):
            stmt.type = final_type_str_to_strview(stmt.type.wrapped)
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
            if not is_final_allowed_inner(inner):
                raise self.ctx.error(
                    f"Final[{inner}] is not supported; {FINAL_INNER_TYPE_ERROR}",
                    stmt
                )

        if self.ctx.is_top_level and not stmt.is_final:
            type_hint = str(stmt.type) if stmt.type else "<type>"
            self._warn_all_caps_without_final(stmt.name, type_hint, stmt)

        # Protocol types can only be used for function parameters, not variables
        # Exception: @dynamic protocols can be used as variable types
        if stmt.type and is_protocol_type(stmt.type):
            protocol_info = protocol_info_of(stmt.type)
            if not protocol_info or not protocol_info.is_dynamic:
                raise self.ctx.error(
                    f"Protocol type '{stmt.type.name}' cannot be used as a variable type. "
                    f"Only @dynamic protocols can be used as variable types",
                    stmt
                )

        # Detect native global import: x: T = native_global("name")
        if isinstance(stmt.init, TpyCall) and isinstance(stmt.init.func, TpyName) and self.ctx.func.current_ns:
            binding = self.ctx.func.current_ns.lookup(stmt.init.func_name)
            if (binding and binding.kind == BindingKind.IMPORTED_NAME
                    and binding.import_source
                    and binding.import_source[0] == "tpy.extern"
                    and binding.import_source[1] == "native_global"):
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
                # Determine linkage from kwargs
                kw_binding = stmt.init.kwargs.get("binding")
                kw_array = stmt.init.kwargs.get("array")
                is_c_binding = (isinstance(kw_binding, TpyStrLiteral)
                                and kw_binding.value == "C")
                is_array = (isinstance(kw_array, TpyBoolLiteral)
                            and kw_array.value is True)
                if is_c_binding and is_array:
                    stmt.linkage = VarLinkage.NATIVE_C_ARRAY
                elif is_c_binding:
                    stmt.linkage = VarLinkage.NATIVE_C
                else:
                    stmt.linkage = VarLinkage.NATIVE
                stmt.native_name = native_name
                stmt.init = None
                return

        # Handle `global x` declarations: treat as reassignment of the global variable
        is_global_declared = stmt.name in self.ctx.func.global_declarations
        if is_global_declared:
            existing_type = self.ctx.global_scope.lookup(stmt.name)
            if stmt.type is not None:
                raise self.ctx.error(
                    f"Cannot add type annotation to global variable '{stmt.name}' from inside a function",
                    stmt
                )
        else:
            # Check if this is a reassignment (variable already exists in scope)
            existing_type = self.ctx.func.current_scope.lookup(stmt.name)
            # Top-level typed globals are pre-registered before statement analysis.
            # For an earlier unannotated write to the same name, treat this as a
            # fresh local write and let a later annotation retro-validate history.
            is_preregistered_global_write = (
                self.ctx.is_top_level
                and stmt.type is None
                and stmt.init is not None
                and stmt.name not in self.ctx.func.current_scope.bindings
                and stmt.name in self.ctx.global_scope.bindings
                and stmt.name not in self.ctx.func.authoritative_types
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

        init_type: TpyType | None = None
        if stmt.init:
            # Handle empty list literal or generic type constructor with explicit type annotation
            # Note: [] * N is collapsed to [] in the parser
            is_empty_literal = isinstance(stmt.init, TpyArrayLiteral) and not stmt.init.elements
            is_empty_dict_literal = isinstance(stmt.init, TpyDictLiteral) and not stmt.init.keys
            _generic_td = (find_factory_by_simple_name(stmt.init.func_name)
                           if isinstance(stmt.init, TpyCall) and isinstance(stmt.init.func, TpyName) else None)
            is_generic_constructor = (isinstance(stmt.init, TpyCall) and
                                      not stmt.init.args and
                                      stmt.init.call_type is None and
                                      _generic_td is not None and
                                      bool(_generic_td.param_kinds))

            # Empty dict literal with annotation: d: dict[K, V] = {}
            if is_empty_dict_literal and stmt.type:
                if is_dict(stmt.type):
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
                    td = find_factory_by_simple_name(stmt.init.func_name)
                    annotation_matches = (td is not None and
                                          stmt.type.qualified_name() == td.qname)
                else:
                    # Empty literal [] can match list[T] annotation
                    annotation_matches = is_list(stmt.type)

                if annotation_matches:
                    if is_list(stmt.type):
                        # list[T]: Use PendingListType for potential Array optimization
                        elem_type = stmt.type.type_args[0]
                        # Set call_type so codegen generates explicit type (e.g., std::vector<int>())
                        if is_generic_constructor:
                            stmt.init.call_type = stmt.type  # type: ignore
                        if self.ctx.func.current_function is None:
                            # Global context: return ListType directly
                            init_type = make_list(elem_type)
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
                            self.ctx.func.pending_resolutions.append(literal_id)
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
                    func_name = stmt.init.func_name if is_generic_constructor else "[]"
                    raise self.ctx.error(
                        f"{func_name} requires matching type annotation, got {stmt.type}", stmt
                    )
            else:
                # Use annotation as hint, or existing type for reassignments
                type_hint = stmt.type if stmt.type else existing_type
                init_type = self.expr.analyze_expr_with_hint(stmt.init, type_hint)

            # Deferred Final constant check: runs after init analysis so that
            # @call_macro expansions are available via macro_expansion attr.
            if stmt.is_final:
                self.validate_compile_time_constant(
                    stmt.init, stmt.type, f"Final variable '{stmt.name}'", stmt,
                )
                self.ctx.analyzed_finals.add(stmt.name)

            # Track container literal to variable mapping for mutation/type inference.
            # Only bind when the init is an actual literal or empty constructor,
            # not a name reference (aliases are handled by register_list_alias).
            if isinstance(init_type, PENDING_CONTAINER_TYPES) and not isinstance(stmt.init, TpyName):
                self.ctx.track_container_variable(
                    stmt.name, init_type, stmt.loc.line if stmt.loc else None,
                )
                # List-specific: if explicit annotation is provided, record it
                if isinstance(init_type, PendingListType):
                    if stmt.type and isinstance(stmt.init, (TpyArrayLiteral, TpyListComprehension)):
                        info = self.ctx.list_literals[init_type.literal_id]
                        info.has_explicit_annotation = True
                        info.explicit_type = stmt.type

            # Track pending generic instance to variable mapping
            if isinstance(init_type, PendingGenericInstanceType):
                self.ctx.func.variable_to_generic_instance[stmt.name] = init_type.instance_id
                info = self.ctx.func.pending_generic_instances.get(init_type.instance_id)
                if info is not None:
                    info.variable_name = stmt.name
                    info.decl_line = stmt.loc.line if stmt.loc else None

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
                if (is_char_type(stmt.type) and is_any_str_type(init_type) and
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
                # PendingGenericInstanceType: reject reassignment while pending
                if isinstance(inner_existing, PendingGenericInstanceType):
                    raise self.ctx.error(
                        f"Cannot reassign '{stmt.name}' while its generic type is still "
                        f"being inferred; add explicit type arguments to the constructor",
                        stmt,
                    )
                # PendingListType reassignment: different sizes force list
                if isinstance(inner_existing, PendingListType):
                    if isinstance(inner_init, PendingListType):
                        if inner_existing.size != inner_init.size:
                            self.deduction.mark_list_different_size(inner_existing.literal_id)
                            self.deduction.mark_list_different_size(inner_init.literal_id)
                        else:
                            self.deduction.link_list_literals(inner_existing.literal_id, inner_init.literal_id)
                    var_type = existing_type
                # PendingViewType reassignment: track view-compatibility, keep pending
                elif isinstance(inner_existing, PendingViewType):
                    vf = inner_existing.family
                    is_any_check = is_any_str_type if vf is STR_FAMILY else is_any_bytes_type
                    if is_any_check(inner_init):
                        if not self.deduction.is_view_compatible_source(stmt.init, inner_init):
                            self.deduction.mark_view_reassigned_from_owned(stmt.name, vf)
                        else:
                            self.deduction.track_view_reassign_source(stmt.name, inner_init, vf)
                    var_type = existing_type
                else:
                    var_type = self.deduction.resolve_reassignment_target_type(
                        stmt.name, inner_existing, inner_init, init_expr=stmt.init
                    )
                    # Reassignment: check if we need to upgrade IntLiteralType
                    if isinstance(inner_existing, IntLiteralType) and is_integer_type(var_type):
                        # Upgrade from IntLiteralType to concrete type
                        # Update var_types so codegen knows the resolved type
                        orig_decl = self.ctx.func.var_decl_by_name.get(stmt.name)
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
                        orig_decl = self.ctx.func.var_decl_by_name.get(stmt.name)
                        if orig_decl:
                            self.ctx.var_types[id(orig_decl)] = resolved
                        # Retroactively update declared_var_types for earlier lines
                        # so # tpyc: type() reflects the final variable type.
                        for key in self.ctx.declared_var_types:
                            if key[1] == stmt.name:
                                self.ctx.declared_var_types[key] = resolved
            else:
                # New variable: resolve IntLiteralType/FloatLiteralType.
                if isinstance(init_type, IntLiteralType):
                    var_type = self.ctx.default_int_for_literal(init_type, warn_node=stmt.init)
                    self.ctx.func.literal_default_vars.add(stmt.name)
                elif isinstance(init_type, FloatLiteralType):
                    var_type = FLOAT  # float literals always default to float64
                # Preserve OwnType on variables -- Own[T] indicates the variable
                # owns its storage and can be moved at last use.
                # Exceptions: union types need the raw type for isinstance/
                # narrowing codegen; optional pointer types use T* storage.
                elif isinstance(init_type, OwnType):
                    inner = init_type.wrapped
                    if (isinstance(inner, UnionType)
                            or (isinstance(inner, OptionalType) and inner.uses_pointer_repr())):
                        var_type = inner
                    else:
                        var_type = init_type
                # None literal without annotation -- can't infer the Optional type
                elif isinstance(init_type, NoneType):
                    var_type = init_type
                    self.ctx.func.unresolved_none_vars.add(stmt.name)
                else:
                    var_type = init_type
            # Strip OwnType from init_type: ownership of the source variable
            # doesn't transfer to the target. The target determines its own
            # ownership via the OwnType wrapping logic below (line ~2570).
            if isinstance(var_type, OwnType):
                var_type = var_type.wrapped
            # Preserve Ref on non-reassigned function locals from reference
            # sources (call returns, field access, subscript, params).
            # Strip for: reassigned locals (T* codegen), top-level globals.
            if (stmt.name in self.ctx.func.current_reassigned_vars
                    or self.ctx.is_top_level):
                var_type = unwrap_ref_type(var_type)
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

        # Deferred type inference for new locals (PendingViewType, list alias, etc.)
        if not is_global_declared and existing_type is None:
            var_type = self._infer_new_local_type(
                stmt.name, var_type, stmt.init, init_type,
                line=(stmt.loc.line if stmt.loc else None),
            )
            if isinstance(var_type, PendingViewType):
                stmt.type = var_type

        if is_global_declared:
            # Update global scope type; bind in current scope for local reads
            self.ctx.global_scope.define(stmt.name, var_type)
            self.ctx.func.current_scope.define(stmt.name, var_type)
        else:
            self.ctx.func.current_scope.define(stmt.name, var_type)
        # Reassignment revives a consumed variable
        self.ctx.func.consumed_vars.discard(stmt.name)
        # Reassigning a loop variable prevents const-ref binding
        if existing_type is not None:
            self.ctx.mark_loop_var_mutated(stmt.name)
        # Assigning a loop var to a non-value-type local takes &(var) in codegen
        if (stmt.init is not None and isinstance(stmt.init, TpyName)
                and var_type is not None and not var_type.is_value_type()):
            self.ctx.mark_loop_var_mutated(stmt.init.name)
        # Borrow tracking: reassignment breaks aliases in both directions
        self.ctx.mark_all_view_borrowers_mutated(stmt.name)
        bt = self.ctx.func.borrow_tracker
        bt.remove_borrower(stmt.name)
        bt.remove_storage_borrows(stmt.name)
        # Create borrow when the target aliases another variable's storage.
        # Skipped for reassigned vars (they use T* pointer-locals in codegen;
        # general alias tracking would require pointer-alias analysis).
        if (stmt.init is not None
                and stmt.name not in self.ctx.func.current_reassigned_vars
                and var_type is not None):
            if not var_type.is_value_type():
                # Non-value lvalue: y = x, v = items[i], v = obj.field
                init_unwrapped = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
                root = _borrow_storage_root(stmt.init)
                if root is not None:
                    if isinstance(init_unwrapped, TpySubscript):
                        kind = BorrowKind.ELEMENT
                    elif isinstance(init_unwrapped, TpyFieldAccess):
                        kind = BorrowKind.FIELD
                    else:
                        kind = BorrowKind.ALIAS
                    bt.add_borrow(root, stmt.name, kind)
                    # 8a.5: defer marking the source as mutated until the borrower is
                    # actually written through. Deferral applies to:
                    # - ELEMENT borrows (v = items[i])
                    # - ALIAS/FIELD borrows whose root traces back to an ELEMENT borrow
                    #   (w = v, x = w where v = items[i]) -- checked transitively.
                    # PTR/ITER borrows and chains not rooted at an ELEMENT mark immediately.
                    if not (kind == BorrowKind.ELEMENT
                            or bt.is_deferred_borrow(root)):
                        self.ctx.mark_param_mutated(root)
                else:
                    # _borrow_storage_root returned None: init is not a simple name/
                    # subscript/field (e.g. or/and/ternary, or a deep chain like
                    # outer.inner[i]). Use addr_taken_roots to find all root params
                    # and mark them T& (not const T&) since we're aliasing into them.
                    for alias_root in addr_taken_roots(stmt.init):
                        self.ctx.mark_param_mutated(alias_root)
            elif isinstance(var_type, PtrType):
                # take_ptr(x) / Ptr(x) borrows x's storage even though Ptr is a value type
                init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
                if isinstance(init_inner, TpyCall) and len(init_inner.args) > 0:
                    is_ptr_ctor = (init_inner.call_type is not None
                                   and init_inner.call_type.is_pointer())
                    fi = init_inner.resolved_function_info
                    is_vpc = fi is not None and fi.value_ptr_coercion
                    if is_ptr_ctor or is_vpc:
                        root = _borrow_storage_root(init_inner.args[0])
                        if root is not None:
                            bt.add_borrow(root, stmt.name, BorrowKind.PTR)
            elif is_span(var_type):
                # Span from slicing borrows the source container
                # (StrView excluded: str is immutable, no mutations to warn about)
                init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
                if isinstance(init_inner, TpySubscript) and isinstance(init_inner.index, TpySlice):
                    root = _borrow_storage_root(init_inner)
                    if root is not None:
                        bt.add_borrow(root, stmt.name, BorrowKind.ELEMENT)
        # 8b: Register call result borrow for ALL assignments (including reassignments).
        # Unlike general alias tracking, borrow contracts use precise return_borrows_from
        # facts and don't need pointer-alias analysis -- safe to apply to reassigned vars.
        if (stmt.init is not None
                and var_type is not None
                and not var_type.is_value_type()):
            init_unwrapped = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
            _register_call_result_borrow(self.ctx, stmt.name, init_unwrapped)
        # Reassigned non-value locals generate T* local = &(source) in C++.
        # Mark source param as mutated so it stays T& (not const T&), regardless
        # of whether the borrow-tracking block above ran.
        if (stmt.init is not None
                and stmt.name in self.ctx.func.current_reassigned_vars
                and var_type is not None
                and not var_type.is_value_type()):
            for alias_root in addr_taken_roots(stmt.init):
                self.ctx.mark_param_mutated(alias_root)
        if stmt.init:
            self.init.mark_assigned(stmt.name)
        self.narrowing.update_after_write(stmt.name, var_type, init_type if stmt.init else None, stmt.init)
        # Union assignment narrowing: narrow to concrete member on initial declaration
        if existing_type is None:
            inner_var = unwrap_readonly(var_type)
            if (isinstance(inner_var, UnionType) and init_type is not None
                    and init_type in inner_var.members):
                self.ctx.func.narrowed_types[stmt.name] = init_type
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
            self.ctx.func.var_scope_depth[stmt.name] = self.ctx.func.current_scope.depth
        # Update rvalue status for hoist eligibility (both new vars and reassignments)
        if stmt.init:
            if self.compat.is_lvalue(stmt.init):
                # Move-through: lvalue alias at last use of source promotes to rvalue.
                # Both target and source must be non-reassigned Tier 1 locals
                # (reassigned vars become T* pointer-locals in codegen).
                if (isinstance(stmt.init, TpyName)
                        and existing_type is None
                        and stmt.name not in self.ctx.func.current_reassigned_vars
                        and stmt.init.name not in self.ctx.func.current_reassigned_vars
                        and id(stmt.init) in self.ctx.all_last_uses
                        and self.compat._is_owned_var(stmt.init.name)
                        and var_type is not None
                        and not var_type.is_value_type()
                        and not (isinstance(var_type, OptionalType) and var_type.uses_pointer_repr())
                        and not is_protocol_union(var_type)):
                    self.ctx.func.rvalue_vars.add(stmt.name)
                    self.ctx.func.owned_locals.add(stmt.name)
                    self.ctx.func.ever_owned_locals.add(stmt.name)
                    self.ctx.func.move_through_vars.add(stmt.name)
                else:
                    self.ctx.func.rvalue_vars.discard(stmt.name)
                    self.ctx.func.owned_locals.discard(stmt.name)
            else:
                self.ctx.func.rvalue_vars.add(stmt.name)
                # Track ownership: rvalue-init from value-creating expression
                # (constructor, Own return) vs reference-returning call.
                if not self._is_reference_returning_call(stmt.init):
                    self.ctx.func.owned_locals.add(stmt.name)
                    self.ctx.func.ever_owned_locals.add(stmt.name)
                else:
                    self.ctx.func.owned_locals.discard(stmt.name)
        if stmt.init and _needs_provenance_tracking(var_type):
            self.init.mark_provenance(stmt.name, self.compat.is_param_derived_expr(stmt.init))
            self.init.mark_safe_to_return(
                stmt.name, self.compat.is_safe_to_return_expr(stmt.init))
        # Track non-null pointer provenance for null-check elision
        if stmt.init and isinstance(var_type, PtrType):
            # Unwrap coercion (e.g. Ptr[T] -> Ptr[readonly[T]]) to find the source expression
            init_inner = stmt.init.expr if isinstance(stmt.init, TpyCoerce) else stmt.init
            _fi = init_inner.resolved_function_info if isinstance(init_inner, TpyCall) else None
            is_non_null = (isinstance(init_inner, TpyCall)
                           and len(init_inner.args) > 0
                           and ((init_inner.call_type is not None
                                 and init_inner.call_type.is_pointer())
                                or (_fi is not None and _fi.value_ptr_coercion)))
            if not is_non_null and isinstance(init_inner, TpyName):
                is_non_null = init_inner.name in self.ctx.func.non_null_ptr_vars
            self.init.mark_non_null_ptr(stmt.name, is_non_null)

        # Scope escape check for variable declarations (new and reassignment)
        if stmt.init and not var_type.is_value_type():
            self.scopes.check_escape(stmt.name, stmt.init, stmt)
        if self.ctx.func.current_ns:
            self.ctx.func.current_ns.bind_variable(stmt.name, var_type)
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
            self.ctx.func.var_decl_by_name[stmt.name] = stmt
        # Record declared type for test type-annotation validation.
        # Strip Own[T] and Ref[T] for display -- internal annotations, not user-facing.
        if stmt.loc:
            display_type = unwrap_ref_type(unwrap_own(var_type)) if var_type else var_type
            self.ctx.declared_var_types[(stmt.loc.line, stmt.name)] = display_type

    def _is_reference_returning_call(self, expr: TpyExpr | None) -> bool:
        """Check if an expression is a function call that returns a reference.

        Returns True for function/method calls that return non-Own non-value
        types (i.e., they return references to existing storage).
        Returns False for constructors, Own[T] returns, value returns, and
        non-call expressions.
        """
        if expr is None:
            return False
        if isinstance(expr, TpyCoerce):
            return self._is_reference_returning_call(expr.expr)
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            # Constructor calls always create new values
            if isinstance(expr, TpyCall) and expr.call_type is not None:
                return False
            fi = expr.resolved_function_info
            if fi is not None and fi.return_type is not None:
                rt = fi.return_type
                # Own[T] returns are value-creating (ownership transfer)
                if isinstance(rt, OwnType):
                    return False
                # Value types are always by-value
                if rt.is_value_type():
                    return False
                # User-defined record constructors: call_type is None (not set
                # by the constructor resolution path) but fi resolves to __init__
                # or has the record's name as the function name.
                if fi.name == "__init__":
                    return False
                if (isinstance(expr, TpyCall) and isinstance(expr.func, TpyName)
                        and self.ctx.registry.find_record(expr.func.name) is not None):
                    return False
                # Non-Own non-value return = reference
                return True
        return False

    def _analyze_tuple_unpack(self, stmt: TpyTupleUnpack) -> None:
        """Analyze tuple unpacking: a, b = expr."""
        rhs_type = self.expr.analyze_expr(stmt.value)
        rhs_check = rhs_type.wrapped if isinstance(rhs_type, OwnType) else rhs_type

        if not isinstance(rhs_check, TupleType):
            raise self.ctx.error(
                f"Cannot unpack non-tuple type {rhs_type}", stmt)

        rhs_type = rhs_check
        n_targets = len(stmt.targets)
        n_elems = len(rhs_type.element_types)
        if n_targets != n_elems:
            raise self.ctx.error(
                f"Cannot unpack tuple of {n_elems} elements into "
                f"{n_targets} targets", stmt)

        # Per-element expressions for narrowing (range facts, etc.)
        has_elem_exprs = isinstance(stmt.value, TpyTupleLiteral)
        for i, name in enumerate(stmt.targets):
            elem_type = rhs_type.element_types[i]
            elem_expr = stmt.value.elements[i] if has_elem_exprs and i < len(stmt.value.elements) else None
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
                # Block reassignment of Final globals at module level
                if name in self.ctx.final_globals:
                    raise self.ctx.error(
                        f"Cannot reassign Final variable '{name}'",
                        stmt)
                # At module level, targets become globals with namespace-scope
                # definitions. Mark is_new=False so codegen emits assignment
                # (the declaration is handled by gen_global_decl).
                self.ctx.global_scope.define(name, elem_type)
                self.ctx.func.current_scope.define(name, elem_type)
                self.init.mark_assigned(name)
                self.narrowing.update_after_write(name, elem_type, elem_type, elem_expr)
                if self.ctx.func.current_ns:
                    self.ctx.func.current_ns.bind_variable(name, elem_type)
                decl_line = stmt.loc.line if stmt.loc else 0
                if name not in self.ctx.top_level_decls:
                    self.ctx.top_level_decls[name] = decl_line
                self._warn_all_caps_without_final(name, str(elem_type), stmt)
                if stmt.loc:
                    display_type = unwrap_own(elem_type) if elem_type else elem_type
                    self.ctx.declared_var_types[(stmt.loc.line, name)] = display_type
                stmt.is_new.append(False)
                continue

            existing = self.ctx.func.current_scope.lookup(name)
            # Don't treat globals as existing unless explicitly declared
            # with 'global' -- unpack should create locals by default
            if (existing is not None
                    and name not in self.ctx.func.global_declarations
                    and name not in self.ctx.func.current_scope.bindings
                    and name in self.ctx.global_scope.bindings):
                existing = None
            if existing is not None:
                self._check_nonvalue_rebinding(name, stmt)
                self.compat.check_type_compatible(
                    elem_type, existing, "tuple unpacking", source_expr=stmt)
                self.narrowing.update_after_write(name, existing, elem_type, elem_expr)
                stmt.is_new.append(False)
            else:
                elem_type = self._infer_new_local_type(
                    name, elem_type, None, None,
                    line=(stmt.loc.line if stmt.loc else None),
                )
                stmt.target_types[i] = elem_type
                self.ctx.func.current_scope.define(name, elem_type)
                self.init.mark_assigned(name)
                self.narrowing.update_after_write(name, elem_type, elem_type, elem_expr)
                stmt.is_new.append(True)
            if stmt.loc:
                display_type = unwrap_own(elem_type) if elem_type else elem_type
                self.ctx.declared_var_types[(stmt.loc.line, name)] = display_type

        # Determine const-ref eligibility per element for expensive value types.
        # Safe because tuples are immutable -- no in-place mutation possible.
        if not self.ctx.is_top_level:
            source_is_lvalue = isinstance(stmt.value, TpyName)
            source_safe = True
            if source_is_lvalue:
                source_name = stmt.value.name
                source_safe = (source_name not in self.ctx.func.current_reassigned_vars)
            for i, name in enumerate(stmt.targets):
                if name is None:
                    stmt.is_const_ref.append(False)
                    continue
                target_type = stmt.target_types[i]
                eligible = (
                    stmt.is_new[i]
                    and not stmt.is_ref[i]
                    and not stmt.is_owned[i]
                    and not isinstance(target_type, PendingViewType)
                    and target_type.is_value_type()
                    and target_type.is_expensive_copy()
                    and name not in self.ctx.func.current_reassigned_vars
                    and name not in self.ctx.func.current_aug_assigned_vars
                    and source_safe
                )
                stmt.is_const_ref.append(eligible)

    def _analyze_slice_assign(self, stmt: TpyAssign) -> None:
        """Analyze a slice assignment: a[x:y] = rhs or a[x:y:z] = rhs."""
        assert isinstance(stmt.target, TpySubscript)
        sl = stmt.target.index
        assert isinstance(sl, TpySlice)

        stepped = sl.step is not None

        obj_type = self.expr.analyze_expr(stmt.target.obj)
        actual_type = unwrap_own(unwrap_readonly(unwrap_ref_type(obj_type)))

        # Look up __setitem__(basic_slice/slice, value) overload via .py stubs
        result = self.expr._find_slice_setitem(actual_type, stepped=stepped)
        if result is None:
            raise self.ctx.error(
                f"Slice assignment not supported for type '{obj_type}'", stmt)

        _value_param_type, fi = result
        stmt.target.slice_function_info = fi
        stmt.target.is_stepped_slice = stepped

        self._enforce_readonly_assignment_target(stmt.target)

        # Analyze slice bounds and validate they are integer types
        for bound in (sl.lower, sl.upper, sl.step):
            if bound is not None:
                bound_type = self.expr.analyze_expr(bound)
                if not is_any_int_type(bound_type):
                    raise self.ctx.error(
                        f"Slice bound must be an integer, got '{bound_type}'", bound)

        # Use the container's element type as hint so array literals infer correctly.
        # Use the stub's value param type (e.g. Iterable[Own[T]]) for coercion,
        # which accepts any iterable and triggers copy warnings for lvalue sources.
        elem_type = actual_type.get_element_type()
        rhs_hint = make_list(elem_type) if elem_type is not None else _value_param_type
        self.ctx.set_expr_type(stmt.target, rhs_hint)

        value_type = self.expr.analyze_expr_with_hint(stmt.value, rhs_hint)
        stmt.value = self.compat.coerce_expr(stmt.value, value_type, _value_param_type, "slice assignment",
                                             coercion_ctx=CoercionContext.ASSIGN)

        # Mutation tracking
        root = _root_name_of_expr(stmt.target)
        if root is not None:
            self.ctx.mark_loop_var_mutated(root)
            self.ctx.mark_param_mutated(root)
            # Slice assignment replaces a subrange -- structural mutation.
            self.ctx.mark_param_structurally_mutated(root)
        storage = self._resolve_obj_storage(stmt.target.obj)
        if storage is not None:
            if self.ctx.func.borrow_tracker.has_borrow_of_kinds(storage, (BorrowKind.ELEMENT, BorrowKind.PTR, BorrowKind.ITER)):
                self.ctx.warning(
                    f"Mutation of '{storage}' while borrowed"
                    " (slice assignment may invalidate references)", stmt)
            self.ctx.mark_all_view_borrowers_mutated(storage)

    def _analyze_assign(self, stmt: TpyAssign) -> None:
        """Analyze an assignment."""
        # In nested defs, assigning to an outer variable requires nonlocal
        if (self.ctx.func.in_nested_def
                and isinstance(stmt.target, TpyName)
                and stmt.target.name in self.ctx.func.outer_scope_locals
                and stmt.target.name not in self.ctx.func.current_nonlocal_names):
            raise self.ctx.error(
                f"Cannot assign to '{stmt.target.name}' in nested function"
                f" without 'nonlocal' declaration",
                stmt)
        # Slice assignment: a[x:y] = rhs -- handled separately
        if isinstance(stmt.target, TpySubscript) and isinstance(stmt.target.index, TpySlice):
            self._analyze_slice_assign(stmt)
            return
        target_type = self.expr.analyze_expr(stmt.target)
        # Class constants are Final in v1: reject reassignment via `MyClass.X = ...`.
        # Mutable ClassVar with assignable storage lands in Phase 7.
        if (isinstance(stmt.target, TpyFieldAccess)
                and stmt.target.class_constant_owner is not None):
            raise self.ctx.error(
                f"Cannot reassign Final class constant "
                f"'{stmt.target.class_constant_owner.name}.{stmt.target.field}'",
                stmt,
            )
        value_type = self.expr.analyze_expr_with_hint(stmt.value, target_type)
        # Property setter: validate and tag for codegen
        if isinstance(stmt.target, TpyFieldAccess) and stmt.target.is_property_access:
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            actual = unwrap_readonly(obj_type) if obj_type else None
            record = self.ctx.registry.get_record_for_type(actual) if isinstance(actual, NominalType) else None
            prop = self.protocols.lookup_record_property(record, stmt.target.field) if record else None
            if prop and prop.setter:
                stmt.target.property_setter = True
                # Construct setter TpyMethodCall for codegen delegation
                setter_name = f"set_{stmt.target.field}"
                setter_call = TpyMethodCall(
                    obj=stmt.target.obj, method=setter_name, args=[stmt.value])
                setter_call.resolved_function_info = prop.setter
                stmt.target.property_setter_call = setter_call
            elif prop and not prop.setter:
                raise self.ctx.error(
                    f"Property '{stmt.target.field}' is read-only (no setter defined)",
                    stmt,
                )
        self._enforce_readonly_assignment_target(stmt.target)
        # Track mutation of for-each loop variables (prevents const-ref binding)
        root = _root_name_of_expr(stmt.target)
        if root is not None:
            self.ctx.mark_loop_var_mutated(root)
            # Through-reference writes (field/subscript) mutate the param's object;
            # plain name reassignment just rebinds the local.
            if isinstance(stmt.target, (TpyFieldAccess, TpySubscript)):
                self.ctx.mark_param_mutated(root)
        copy_warning_fired = False
        if isinstance(stmt.target, (TpyFieldAccess, TpySubscript)):
            declared_target_type = self.narrowing.declared_type_for_expr(stmt.target)
            if declared_target_type is not None:
                target_type = declared_target_type
            # Unified copy detection: Ref (borrowed), Own (owned at non-last-use),
            # or compound borrowed types (Optional/Union with pointer repr) on
            # the value expression type means storing it into a field/container
            # will copy.  Ref covers params, call returns, field access, subscript.
            # Own covers owned locals at non-last-use.
            # Optional[NonValue] and Union[pointer-repr] use pointer representation
            # (T*, variant<A*,B*>) which is semantically borrowed -- copying into
            # storage (std::optional<T>, variant<A,B>) is a pointer-to-value copy.
            # Skip explicit copy() calls (caller acknowledged the copy) and
            # OwnType from non-name sources (explicit Own return from function).
            is_own_from_name = isinstance(value_type, OwnType) and isinstance(stmt.value, TpyName)
            stripped_value = self.ctx.get_expr_type(stmt.value)
            is_compound_ref = (
                (isinstance(stripped_value, OptionalType) and not stripped_value.inner.is_value_type())
                or (isinstance(stripped_value, UnionType) and stripped_value.uses_pointer_repr()
                    and not self.ctx.is_recursive_union(stripped_value))
            )
            # Ptr[T] target takes the address of the source (`_a(&a)`), no copy.
            target_is_ptr = isinstance(unwrap_qualifiers(target_type), PtrType)
            if ((isinstance(value_type, RefType) or is_own_from_name or is_compound_ref)
                    and stmt.loc is not None
                    and not target_is_ptr
                    and not self.compat.is_copy_call(stmt.value)):
                inner = unwrap_qualifiers(value_type)
                dest = "field" if isinstance(stmt.target, TpyFieldAccess) else "container"
                if self.ctx.is_type_non_copyable(target_type):
                    verb = "may copy" if isinstance(inner, TypeParamRef) else "cannot copy"
                    if inner == target_type:
                        msg = (f"{verb} non-copyable type '{target_type}' into "
                               f"{dest}{NOCOPY_REMEDIATION_HINT}")
                    else:
                        msg = (f"{verb} {inner} into {dest} of type '{target_type}'; "
                               f"target is non-copyable{NOCOPY_REMEDIATION_HINT}")
                    raise self.ctx.error(msg, stmt)
                if isinstance(inner, TypeParamRef):
                    msg = f"may copy {inner} into {dest} if not a value type; use copy() to make this explicit"
                else:
                    msg = f"copies {inner} into {dest}; use copy() to make this explicit"
                self.ctx.warning(msg, stmt)
                copy_warning_fired = True
            # Own[T] param stored in a field/container — mark as consumed
            if isinstance(stmt.value, TpyName) and stmt.value.name in self.ctx.func.current_param_names:
                self.ctx.mark_own_param_consumed(stmt.value.name)
                # Address-escape tracking: storing a param into a mutable Ptr[T]
                # takes its address. Suppress the perf-default `const T&` param
                # emission so the `&param -> T*` store type-checks.
                tgt_inner = unwrap_qualifiers(target_type)
                if (isinstance(stmt.target, TpyFieldAccess)
                        and isinstance(tgt_inner, PtrType)
                        and not tgt_inner.is_readonly):
                    self.ctx.func.current_addr_escape_param_names.add(stmt.value.name)
        if isinstance(stmt.target, TpyName):
            # Track param rebinding (subsequent mutations target the new local, not the arg)
            if stmt.target.name in self.ctx.func.current_param_names:
                self.ctx.func.current_rebound_params.add(stmt.target.name)
            # Block reassignment of Final globals at module level
            if self.ctx.is_top_level and stmt.target.name in self.ctx.final_globals:
                raise self.ctx.error(
                    f"Cannot reassign Final variable '{stmt.target.name}'",
                    stmt
                )
            # Match var-decl flow: reject forbidden rebinding before any type mutation.
            self._check_nonvalue_rebinding(stmt.target.name, stmt)
            declared_target_type = self.ctx.func.current_scope.lookup(stmt.target.name)
            if declared_target_type is not None:
                target_type = declared_target_type
            # Unwrap Ref, Own, and ReadonlyType for reassignment type resolution --
            # this is a binding, not passing by reference.
            inner_target = unwrap_own(unwrap_ref_type(unwrap_readonly(target_type)))
            inner_value = unwrap_own(unwrap_ref_type(unwrap_readonly(value_type)))
            # PendingListType reassignment: different sizes force list
            if isinstance(inner_target, PendingListType):
                if isinstance(inner_value, PendingListType):
                    if inner_target.size != inner_value.size:
                        self.deduction.mark_list_different_size(inner_target.literal_id)
                        self.deduction.mark_list_different_size(inner_value.literal_id)
                    else:
                        self.deduction.link_list_literals(inner_target.literal_id, inner_value.literal_id)
                # target_type stays PendingListType
            # PendingDictType/PendingSetType reassignment: keep pending
            elif isinstance(inner_target, (PendingDictType, PendingSetType)):
                pass  # target_type stays pending
            # PendingViewType reassignment: track view-compatibility, keep pending
            elif isinstance(inner_target, PendingViewType):
                vf = inner_target.family
                is_any_check = is_any_str_type if vf is STR_FAMILY else is_any_bytes_type
                if is_any_check(inner_value):
                    if not self.deduction.is_view_compatible_source(stmt.value, inner_value):
                        self.deduction.mark_view_reassigned_from_owned(stmt.target.name, vf)
                    else:
                        self.deduction.track_view_reassign_source(stmt.target.name, inner_value, vf)
                # target_type stays pending
            else:
                target_type = self.deduction.resolve_reassignment_target_type(
                    stmt.target.name, inner_target, inner_value, init_expr=stmt.value
                )
                # Readonly status flows from the value expression
                if isinstance(value_type, ReadonlyType) and not target_type.is_value_type():
                    target_type = ReadonlyType(target_type)
            self.ctx.func.current_scope.define(stmt.target.name, target_type)
            # Reassignment revives a consumed variable
            self.ctx.func.consumed_vars.discard(stmt.target.name)
            # Borrow tracking: reassignment breaks aliases in both directions.
            # Note: borrow creation is skipped for reassigned vars (they use T*
            # pointer-locals in codegen); tracking borrows for them would require
            # pointer-alias analysis beyond the current design scope.
            self.ctx.mark_all_view_borrowers_mutated(stmt.target.name)
            bt = self.ctx.func.borrow_tracker
            bt.remove_borrower(stmt.target.name)
            bt.remove_storage_borrows(stmt.target.name)
            # Rebinding a non-value pointer-local generates local = &(source) in C++,
            # requiring source param to be T& (not const T&).
            if not inner_target.is_value_type() and self.compat.is_lvalue(stmt.value):
                for rebind_root in addr_taken_roots(stmt.value):
                    self.ctx.mark_param_mutated(rebind_root)
            if self.ctx.func.current_ns:
                self.ctx.func.current_ns.update_variable_type(stmt.target.name, target_type)
            self.ctx.set_expr_type(stmt.target, target_type)
            self.deduction.record_write(stmt.target.name, stmt.value, inner_value)
            if not isinstance(inner_target, (*PENDING_CONTAINER_TYPES, PendingViewType)):
                resolved = unwrap_readonly(target_type)
                var_decl = self.ctx.func.var_decl_by_name.get(stmt.target.name)
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
            # Update rvalue/ownership status for hoist eligibility and copy detection
            if self.compat.is_lvalue(stmt.value):
                self.ctx.func.rvalue_vars.discard(stmt.target.name)
                self.ctx.func.owned_locals.discard(stmt.target.name)
            else:
                self.ctx.func.rvalue_vars.add(stmt.target.name)
                if not self._is_reference_returning_call(stmt.value):
                    self.ctx.func.owned_locals.add(stmt.target.name)
                    self.ctx.func.ever_owned_locals.add(stmt.target.name)
                else:
                    self.ctx.func.owned_locals.discard(stmt.target.name)

        # Tuples are immutable -- reject element assignment
        if isinstance(stmt.target, TpySubscript):
            obj_type = self.ctx.get_expr_type(stmt.target.obj)
            if isinstance(obj_type, TupleType):
                raise self.ctx.error("Tuples are immutable; cannot assign to tuple elements", stmt)
            # Dict/TypedDict subscript assignment is always allowed
            actual_obj = unwrap_readonly(obj_type)
            is_typed_dict_target = (
                isinstance(actual_obj, NominalType) and actual_obj.is_record
                and stmt.target.typed_dict_field is not None
            )
            if not (is_dict(actual_obj) or isinstance(actual_obj, PendingDictType)) and not is_typed_dict_target:
                elem_type = obj_type.get_element_type()
                if elem_type is not None:
                    # Span[readonly[T]] always rejects element assignment
                    if is_span(obj_type) and is_readonly_span(obj_type):
                        raise self.ctx.error(f"Cannot assign to elements of {obj_type} (read-only)", stmt)
                    # Check if type conforms to MutableSequence[elem_type]
                    mutable_seq = NominalType("MutableSequence", (elem_type,), is_protocol=True)
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

        # Borrow conflict: field write on borrowed storage.
        # Resolves aliases so alias.field = val warns when the underlying storage
        # has field/element/ptr borrows.
        # Subscript assignment (items[i] = val, d[k] = val) is in-place and does
        # NOT invalidate element references: list element replacement doesn't
        # reallocate, and ordered_map is node-based so insertion is stable
        # (confirmed by @native_preserves_refs on dict.__setitem__).
        if isinstance(stmt.target, TpySubscript):
            storage = self._resolve_obj_storage(stmt.target.obj)
            if storage is not None:
                self.ctx.mark_all_view_borrowers_mutated(storage)
        elif isinstance(stmt.target, TpyFieldAccess):
            storage = self._resolve_obj_storage(stmt.target.obj)
            # Also check the field-path key itself (e.g. "self.items" for self.items = [...])
            # since borrows may be registered on the dotted key.
            field_storage = _storage_key(stmt.target)
            _BORROW_KINDS = (BorrowKind.FIELD, BorrowKind.ELEMENT, BorrowKind.PTR, BorrowKind.ITER)
            has_conflict = False
            bt = self.ctx.func.borrow_tracker
            if storage is not None and bt.has_borrow_of_kinds(storage, _BORROW_KINDS):
                has_conflict = True
            if not has_conflict and field_storage is not None and bt.has_borrow_of_kinds(field_storage, _BORROW_KINDS):
                storage = field_storage
                has_conflict = True
            if has_conflict:
                msg = (f"Mutation of '{storage}' while borrowed"
                       " (field assignment may invalidate references)")
                self.ctx.warning(msg, stmt)
            if storage is not None:
                self.ctx.mark_all_view_borrowers_mutated(storage)
            if field_storage is not None and field_storage != storage:
                self.ctx.mark_all_view_borrowers_mutated(field_storage)

        # PendingDictType subscript assignment: d[k] = v -- infer key/value types
        if isinstance(stmt.target, TpySubscript):
            obj_type_for_dict = self.ctx.get_expr_type(stmt.target.obj)
            if isinstance(obj_type_for_dict, PendingDictType):
                index_type = self.ctx.get_expr_type(stmt.target.index)
                self.deduction.infer_dict_key_value_types(
                    stmt.target.obj, index_type, value_type)
                # Update obj_type and target_type if types were inferred
                dict_info = self.ctx.dict_literals.get(obj_type_for_dict.literal_id)
                if dict_info and not isinstance(dict_info.key_type, UnknownElementType):
                    new_pending = PendingDictType(dict_info.key_type, dict_info.value_type, obj_type_for_dict.literal_id)
                    if new_pending.key_type != obj_type_for_dict.key_type or new_pending.value_type != obj_type_for_dict.value_type:
                        self.ctx.set_expr_type(stmt.target.obj, new_pending)
                        if isinstance(stmt.target.obj, TpyName):
                            if self.ctx.func.current_scope:
                                self.ctx.func.current_scope.define(stmt.target.obj.name, new_pending)
                            if self.ctx.func.current_ns:
                                self.ctx.func.current_ns.bind_variable(stmt.target.obj.name, new_pending)
                    target_type = dict_info.value_type
                    self.ctx.set_expr_type(stmt.target, target_type)

        stmt.value = self.compat.coerce_expr(stmt.value, value_type, target_type, "assignment",
                                              coercion_ctx=CoercionContext.ASSIGN)
        # Annotate tuple literal element capture modes
        if isinstance(stmt.value, TpyTupleLiteral) and isinstance(target_type, TupleType):
            is_field = isinstance(stmt.target, TpyFieldAccess)
            self._annotate_tuple_elem_capture(
                stmt.value, target_type, is_field=is_field)
        # Residual copy warning: reassigned vars without OwnType in scope
        if isinstance(stmt.target, (TpyFieldAccess, TpySubscript)):
            if stmt.loc is not None and not copy_warning_fired:
                if self._is_non_owned_var_copy(stmt.value, target_type):
                    dest = "field" if isinstance(stmt.target, TpyFieldAccess) else "container"
                    if self.ctx.is_type_non_copyable(target_type):
                        verb = "may copy" if isinstance(target_type, TypeParamRef) else "cannot copy"
                        if value_type == target_type:
                            msg = (f"{verb} non-copyable type '{target_type}' into "
                                   f"{dest}{NOCOPY_REMEDIATION_HINT}")
                        else:
                            msg = (f"{verb} {value_type} into {dest} of type '{target_type}'; "
                                   f"target is non-copyable{NOCOPY_REMEDIATION_HINT}")
                        raise self.ctx.error(msg, stmt)
                    if isinstance(target_type, TypeParamRef):
                        msg = f"may copy {target_type} into {dest} if not a value type; use copy() to make this explicit"
                    else:
                        msg = f"copies {value_type} into {dest}; use copy() to make this explicit"
                    self.ctx.warning(msg, stmt)

        # Scope escape check for assignments to named variables
        if isinstance(stmt.target, TpyName) and not target_type.is_value_type():
            self.scopes.check_escape(stmt.target.name, stmt.value, stmt)
            # Assigning a loop var to a pointer-local takes &(var) in codegen
            if isinstance(stmt.value, TpyName):
                self.ctx.mark_loop_var_mutated(stmt.value.name)

        if isinstance(stmt.target, TpyName) and _needs_provenance_tracking(target_type):
            self.init.mark_provenance(stmt.target.name, self.compat.is_param_derived_expr(stmt.value))
            self.init.mark_safe_to_return(
                stmt.target.name, self.compat.is_safe_to_return_expr(stmt.value))
        # Track non-null pointer provenance for null-check elision
        if isinstance(stmt.target, TpyName) and isinstance(target_type, PtrType):
            # Unwrap coercion (e.g. Ptr[T] -> Ptr[readonly[T]]) to find the source expression
            val_inner = stmt.value.expr if isinstance(stmt.value, TpyCoerce) else stmt.value
            _fi = val_inner.resolved_function_info if isinstance(val_inner, TpyCall) else None
            is_non_null = (isinstance(val_inner, TpyCall)
                           and len(val_inner.args) > 0
                           and ((val_inner.call_type is not None
                                 and val_inner.call_type.is_pointer())
                                or (_fi is not None and _fi.value_ptr_coercion)))
            if not is_non_null and isinstance(val_inner, TpyName):
                is_non_null = val_inner.name in self.ctx.func.non_null_ptr_vars
            self.init.mark_non_null_ptr(stmt.target.name, is_non_null)

        # Mark as definitely assigned for plain name targets
        if isinstance(stmt.target, TpyName):
            self.init.mark_assigned(stmt.target.name)
            self.narrowing.update_after_write(stmt.target.name, target_type, value_type, stmt.value)
        elif isinstance(stmt.target, TpyFieldAccess):
            self.narrowing.invalidate_for_field_write(stmt.target)

    def _analyze_del_item(self, stmt: TpyDelItem) -> None:
        """Analyze del obj[key] statement."""
        for subscript in stmt.targets:
            # Analyze obj and index separately to avoid triggering __getitem__
            # validation (del doesn't read the element, only deletes it).
            self.expr.analyze_expr(subscript.obj)
            # Track mutation of for-each loop variables and parameters
            del_root = _root_name_of_expr(subscript.obj)
            if del_root is not None:
                self.ctx.mark_loop_var_mutated(del_root)
                self.ctx.mark_param_mutated(del_root)
                # del item removes an element from the container -- structural mutation.
                self.ctx.mark_param_structurally_mutated(del_root)
            # Borrow conflict: del on a container with element-level borrows
            storage = self._resolve_obj_storage(subscript.obj)
            if storage is not None:
                bt = self.ctx.func.borrow_tracker
                if bt.has_element_borrow(storage):
                    if bt.has_iter_borrow(storage):
                        msg = (f"Mutation of '{storage}' while iterating over it"
                               " ('del' invalidates the iterator)")
                    else:
                        msg = (f"Mutation of '{storage}' while borrowed"
                               " ('del' may invalidate references)")
                    self.ctx.warning(msg, stmt)
                self.ctx.mark_all_view_borrowers_mutated(storage)
            self._enforce_readonly_assignment_target(subscript)
            obj_type = self.ctx.get_expr_type(subscript.obj)
            actual = unwrap_readonly(obj_type)
            # Reject known-immutable/fixed-size types before analyzing the index
            if isinstance(actual, TupleType):
                raise self.ctx.error(
                    "Tuples are immutable; cannot delete tuple elements", stmt)
            if is_array(actual):
                raise self.ctx.error(
                    "Arrays are fixed-size; cannot delete array elements", stmt)
            if is_span(actual):
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

    def _analyze_del_var(self, stmt: TpyDelVar) -> None:
        """Analyze a variable deletion statement (del x)."""
        for name in stmt.names:
            # Global-declared and nonlocal vars are always reachable;
            # locals/params must be definitely assigned.
            is_external = (name in self.ctx.func.global_declarations
                           or name in self.ctx.func.current_nonlocal_names)
            if not is_external and name not in self.ctx.func.definitely_assigned:
                raise self.ctx.error(
                    f"variable '{name}' may not be assigned at this point", stmt)
            # Remove from definitely_assigned so use-after-del is caught
            self.ctx.func.definitely_assigned.discard(name)
            # Clear narrowing facts
            self.ctx.func.narrowed_types.pop(name, None)
            self.ctx.func.non_null_ptr_vars.discard(name)

    def _apply_aug_assign_writeback(
        self,
        target: TpyExpr,
        target_type: TpyType,
        result_type: TpyType,
        op: str,
        stmt: TpyAugAssign,
    ) -> None:
        """Check and apply the write-back step of an augmented assignment.

        After the binop is resolved with result_type, verifies result_type is
        compatible with the target and updates variable caches when widening applies.
        Uses the same type rules as regular assignment (resolve_reassignment_target_type
        + check_type_compatible), so annotated variables and non-wideneable pairs
        produce a standard type mismatch error.
        """
        if result_type == target_type:
            return
        if isinstance(target, TpyName):
            name = target.name
            effective_type = self.deduction.resolve_reassignment_target_type(
                name, target_type, result_type,
            )
            # check_type_compatible errors when effective_type refused widening
            # (e.g. annotated variable, or mixed-sign fixed-int pair).
            self.compat.check_type_compatible(
                result_type, effective_type, f"'{op}=' to '{name}'", loc=stmt.loc,
            )
            if effective_type != target_type:
                if self.ctx.func.current_scope:
                    self.ctx.func.current_scope.define(name, effective_type)
                var_decl = self.ctx.func.var_decl_by_name.get(name)
                if var_decl:
                    self.ctx.var_types[id(var_decl)] = effective_type
                for key in self.ctx.declared_var_types:
                    if key[1] == name:
                        self.ctx.declared_var_types[key] = effective_type
        else:
            # Subscript/field target: element type is fixed, cannot widen.
            self.compat.check_type_compatible(
                result_type, target_type,
                f"'{op}=' to {_format_aug_target(target)}", loc=stmt.loc,
            )

    def _is_non_owned_var_copy(self, expr: TpyExpr, target_type: TpyType) -> bool:
        """Check if storing a non-OwnType variable copies into storage.

        Covers locals excluded from OwnType wrapping: reassigned vars,
        union types, Optional with pointer repr.  These are not caught by
        the unified Ref/Own check.
        """
        if target_type.is_value_type():
            return False
        if self.compat._is_value_type_param(target_type):
            return False
        if self.compat.is_copy_call(expr):
            return False
        if not isinstance(expr, TpyName):
            return False
        scope_type = self.ctx.func.current_scope.lookup(expr.name) if self.ctx.func.current_scope else None
        if scope_type is not None and isinstance(scope_type, (RefType, OwnType)):
            return False  # already caught by the Ref/Own check
        if scope_type is not None and scope_type.is_value_type():
            return False
        # Skip at last-use of movable var
        if id(expr) in self.ctx.all_last_uses and self.compat._is_owned_var(expr.name):
            return False
        if scope_type is None:
            return False
        return True

    def _analyze_aug_assign(self, stmt: TpyAugAssign) -> None:
        """Analyze an augmented assignment (+=, -=, etc.)."""
        # In nested defs, aug-assign to an outer variable requires nonlocal
        if (self.ctx.func.in_nested_def
                and isinstance(stmt.target, TpyName)
                and stmt.target.name in self.ctx.func.outer_scope_locals
                and stmt.target.name not in self.ctx.func.current_nonlocal_names):
            raise self.ctx.error(
                f"Cannot modify '{stmt.target.name}' in nested function"
                f" without 'nonlocal' declaration",
                stmt)
        # Block augmented assignment of Final globals at module level
        if isinstance(stmt.target, TpyName) and self.ctx.is_top_level and stmt.target.name in self.ctx.final_globals:
            raise self.ctx.error(
                f"Cannot reassign Final variable '{stmt.target.name}'",
                stmt
            )
        target_type = unwrap_own(unwrap_ref_type(self.expr.analyze_expr(stmt.target)))
        # Augmented assignment on properties not yet supported
        if isinstance(stmt.target, TpyFieldAccess) and stmt.target.is_property_access:
            raise self.ctx.error(
                f"Augmented assignment on property '{stmt.target.field}' is not yet supported",
                stmt,
            )
        # Aug-assign replaces the target's value with a freshly computed one
        # (owned str/bytes concat, reallocated list, etc.), so any prior
        # param-derived / safe-to-return provenance is now stale and must be
        # cleared -- otherwise a later `return` as a view would pass the
        # dangling check despite pointing into local storage.
        if isinstance(stmt.target, TpyName) and _needs_provenance_tracking(target_type):
            self.init.mark_provenance(stmt.target.name, False)
            self.init.mark_safe_to_return(stmt.target.name, False)
        value_type = self.expr.analyze_expr_with_hint(stmt.value, target_type)
        # Track mutation of for-each loop variables and parameters
        aug_root = _root_name_of_expr(stmt.target)
        if aug_root is not None:
            self.ctx.mark_loop_var_mutated(aug_root)
            self.ctx.mark_param_mutated(aug_root)
            # items += other_list extends the container in-place (structural mutation).
            # items[i] += x and obj.field += x are in-place element/field writes -- not structural.
            if isinstance(stmt.target, TpyName):
                self.ctx.mark_param_structurally_mutated(aug_root)
        self._enforce_readonly_assignment_target(stmt.target)
        # Borrow conflict: augmented assignment may mutate borrowed storage.
        # Subscript aug-assign (items[i] += x) modifies element in-place -- same as
        # subscript assign, no reallocation, element/ptr borrows remain valid.
        if isinstance(stmt.target, TpySubscript):
            storage = self._resolve_obj_storage(stmt.target.obj)
            if storage is not None:
                self.ctx.mark_all_view_borrowers_mutated(storage)
        elif isinstance(stmt.target, TpyFieldAccess):
            storage = self._resolve_obj_storage(stmt.target.obj)
            field_storage = _storage_key(stmt.target)
            _BORROW_KINDS = (BorrowKind.FIELD, BorrowKind.ELEMENT, BorrowKind.PTR, BorrowKind.ITER)
            has_conflict = False
            bt = self.ctx.func.borrow_tracker
            if storage is not None and bt.has_borrow_of_kinds(storage, _BORROW_KINDS):
                has_conflict = True
            if not has_conflict and field_storage is not None and bt.has_borrow_of_kinds(field_storage, _BORROW_KINDS):
                storage = field_storage
                has_conflict = True
            if has_conflict:
                self.ctx.warning(
                    f"Mutation of '{storage}' while borrowed"
                    " (field assignment may invalidate references)",
                    stmt,
                )
            if storage is not None:
                self.ctx.mark_all_view_borrowers_mutated(storage)
            if field_storage is not None and field_storage != storage:
                self.ctx.mark_all_view_borrowers_mutated(field_storage)
        # Borrow conflict: aug-assign on a name target that has element borrows.
        # Any structural aug-assign (list +=, set |=, user-defined __iadd__ that
        # reallocates) is a mutation -- check the borrow state, not the container type.
        elif isinstance(stmt.target, TpyName):
            bt = self.ctx.func.borrow_tracker
            storage = bt.effective_storage(stmt.target.name)
            if bt.has_element_borrow(storage):
                if bt.has_iter_borrow(storage):
                    self.ctx.warning(
                        f"Mutation of '{storage}' while iterating over it"
                        f" ('{stmt.op}=' invalidates the iterator)",
                        stmt,
                    )
                else:
                    self.ctx.warning(
                        f"Mutation of '{storage}' while borrowed"
                        f" ('{stmt.op}=' may invalidate references)",
                        stmt,
                    )
            self.ctx.mark_all_view_borrowers_mutated(storage)
        if (
            isinstance(stmt.target, TpyName)
            and is_big_int_type(target_type)
            and is_fixed_int_type(value_type)
            and stmt.target.name in self.ctx.func.literal_default_vars
        ):
            type_name = str(value_type)
            self.ctx.warning(
                f"Augmented assignment does not narrow '{stmt.target.name}' from int to {type_name}; "
                f"variable remains int (BigInt). Annotate or initialize '{stmt.target.name}' as {type_name} "
                f"to keep {type_name} arithmetic.",
                stmt,
            )
        # Target must be numeric, owned string, or a type with registered operators.
        # StrView is excluded -- it's non-owning, so += would dangle.
        is_numeric_target = is_any_int_type(target_type) or is_float_type(target_type)
        is_str_target = (is_str_type(target_type) or is_string_type(target_type)
                         or isinstance(target_type, PendingStrType))
        is_bytes_target = (is_bytes_type(target_type) or is_bytearray_type(target_type)
                           or isinstance(target_type, PendingBytesType))
        # PendingViewType += promotes to owned
        if isinstance(target_type, PendingViewType) and isinstance(stmt.target, TpyName):
            self.deduction.mark_view_augassign(stmt.target.name, target_type.family)
        if not is_numeric_target and not is_str_target and not is_bytes_target:
            # StrView/BytesView += would dangle (result is a temporary assigned to a view)
            if is_str_view_type(target_type):
                raise self.ctx.error(
                    f"Augmented assignment is not supported for StrView (result would dangle)",
                    stmt,
                )
            if is_bytes_view_type(target_type):
                raise self.ctx.error(
                    f"Augmented assignment is not supported for BytesView (result would dangle)",
                    stmt,
                )
            # Try in-place method first (e.g. __iadd__, __ior__), then binary operator
            operators = OperatorResolver(self.ctx)
            protocol_checker = self.protocols.type_conforms_to_protocol if self.protocols else None
            if result := operators.resolve_aug_inplace(
                target_type, stmt.op, value_type, protocol_checker=protocol_checker,
            ):
                stmt.resolved_inplace = result
                if result.method.params:
                    _, param_type = result.method.params[0]
                    self.compat.check_type_compatible(
                        value_type, param_type, f"'{stmt.op}=' operand", source_expr=stmt.value,
                    )
                return
            # Method exists but arg type mismatches -- produce a specific type error.
            expected_param = operators.get_aug_inplace_param_type(target_type, stmt.op)
            if expected_param is not None:
                self.compat.check_type_compatible(
                    value_type, expected_param, f"'{stmt.op}=' operand",
                    loc=stmt.loc, source_expr=stmt.value,
                )
            if result := operators.resolve_binop(target_type, stmt.op, value_type):
                stmt.resolved_binop = result
                return
            raise self.ctx.error(
                f"Operator '{stmt.op}=' is not supported for {target_type}",
                stmt,
            )
        check_value_type = value_type.wrapped if isinstance(value_type, OwnType) else value_type
        if is_numeric_target and not (is_any_int_type(check_value_type) or is_any_float_type(check_value_type)):
            raise self.ctx.error(
                f"Augmented assignment value must be a numeric type, got {check_value_type}",
                stmt,
            )
        if is_str_target and not is_any_str_type(check_value_type):
            raise self.ctx.error(
                f"Augmented assignment value must be a string type, got {check_value_type}",
                stmt,
            )
        # Special case: FixedInt += BigInt should use the target's ops (value gets converted)
        # This preserves checked arithmetic and avoids unnecessary promotion to BigInt
        resolve_value_type = check_value_type
        if is_fixed_int_type(target_type) and is_big_int_type(check_value_type):
            resolve_value_type = target_type
        # Invalidate range facts for the target (value has changed)
        if isinstance(stmt.target, TpyName):
            self.ctx.func.value_ranges.pop(stmt.target.name, None)
        # Resolve the binary operation for codegen
        operators = OperatorResolver(self.ctx)
        if result := operators.resolve_binop(target_type, stmt.op, resolve_value_type):
            stmt.resolved_binop = result
            if is_numeric_target:
                self._apply_aug_assign_writeback(stmt.target, target_type, result.method.return_type, stmt.op, stmt)
        elif is_numeric_target:
            raise self.ctx.error(
                f"Operator '{stmt.op}=' is not supported between {target_type} and {value_type}",
                stmt,
            )
