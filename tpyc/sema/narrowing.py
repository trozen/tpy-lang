"""
TurboPython Narrowing Tracker

Centralizes type narrowing (Optional + Union), expression-identity narrowing,
and fact invalidation on writes/calls.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Callable

from ..typesys import (
    TpyType, OptionalType, NoneType, VoidType, PtrType, ConstPtrType, OwnType, NamedType,
    TypeParamRef, ListType, ArrayType, SpanType, PendingListType, StrType, ModuleType,
    ReadonlyType, UnionType, unwrap_readonly, make_union, union_none_narrow,
    is_protocol_type,
)
from ..parse import (
    TpyExpr, TpyName, TpyIntLiteral, TpyBinOp, TpyUnaryOp, TpyFieldAccess,
    TpySubscript, TpyNoneLiteral, TpyCall, TpyMethodCall, TpyArrayLiteral,
    TpyListRepeat, TpyCoerce,
)
from ..prescan import match_is_none
from ..namespace import BindingKind
from .diagnostics import OPTIONAL_VALUE_TRUTHINESS_WARNING

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker

ExprIdentity = tuple[str, ...]


class NarrowingTracker:
    """Centralized type narrowing and expression-identity flow analysis.

    Operates on ctx.narrowed_types and ctx.non_none_exprs without owning them.
    """

    def __init__(
        self,
        ctx: SemanticContext,
        type_ops: TypeOperations,
        protocols: ProtocolChecker,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.protocols = protocols
        self._is_readonly_call: Callable[[TpyExpr], bool] | None = None

    def set_readonly_check(self, fn: Callable[[TpyExpr], bool]) -> None:
        """Inject the readonly-call predicate (avoids circular dependency)."""
        self._is_readonly_call = fn

    # -- Name-based narrowing -------------------------------------------

    def narrow_name_type(self, name: str, typ: TpyType) -> TpyType:
        """Narrow Optional/Union name type using flow facts."""
        return self.ctx.narrowed_types.get(name, typ)

    def declared_type_for_name(self, name: str) -> TpyType | None:
        """Get a variable's declared type (without applying flow narrowing)."""
        if self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(name)
            if binding and binding.kind == BindingKind.VARIABLE:
                return binding.type
        return self.ctx.current_scope.lookup(name)

    # -- Expression-identity narrowing ----------------------------------

    def _is_builtin_stable_subscript_type(self, typ: TpyType) -> bool:
        """Whether subscript identities for this type are safe to track."""
        typ = unwrap_readonly(typ)
        if isinstance(typ, (ListType, ArrayType, SpanType, PendingListType, StrType)):
            return True
        if isinstance(typ, ModuleType):
            qname = typ.qualified_name()
            return qname in {"builtins.list", "builtins.str", "tpy.Array", "tpy.Span", "tpy.StaticList"}
        return False

    def _simple_index_token(self, expr: TpyExpr) -> str | None:
        if isinstance(expr, TpyIntLiteral):
            return f"int:{expr.value}"
        if isinstance(expr, TpyName):
            return f"name:{expr.name}"
        if isinstance(expr, TpyUnaryOp) and expr.op == "-" and isinstance(expr.operand, TpyIntLiteral):
            return f"int:{-expr.operand.value}"
        return None

    def declared_type_for_expr(self, expr: TpyExpr) -> TpyType | None:
        """Get declared type for identity-capable expressions without flow narrowing."""
        if isinstance(expr, TpyName):
            return self.declared_type_for_name(expr.name)
        if isinstance(expr, TpyFieldAccess):
            obj_type = self.declared_type_for_expr(expr.obj)
            if obj_type is None:
                return None
            actual_type = unwrap_readonly(obj_type)
            if isinstance(actual_type, (PtrType, ConstPtrType)):
                actual_type = actual_type.pointee
            elif isinstance(actual_type, OwnType):
                actual_type = actual_type.wrapped
            elif isinstance(actual_type, OptionalType):
                if actual_type.inner.is_value_type():
                    return None
                actual_type = actual_type.inner

            if isinstance(actual_type, NamedType) and actual_type.is_record:
                record = self.ctx.registry.get_record(actual_type.name)
                if not record:
                    return None
                type_subst = self.type_ops.build_type_substitution(actual_type)
                field_info = self.protocols.lookup_record_field(record, expr.field)
                if field_info is None:
                    return None
                field_type = field_info.type
                if type_subst:
                    field_type = self.type_ops.substitute_type_params(field_type, type_subst)
                return field_type

            if isinstance(actual_type, TypeParamRef):
                bound = self.type_ops.get_type_param_bound(actual_type.name)
                if bound is not None and is_protocol_type(bound):
                    protocol_info = self.ctx.registry.get_protocol(bound.name)
                    if protocol_info:
                        for field_name, field_type in protocol_info.fields or []:
                            if field_name == expr.field:
                                type_subst: dict[str, TpyType] = {"Self": actual_type}
                                if protocol_info.type_params and bound.type_args:
                                    type_subst.update(dict(zip(protocol_info.type_params, bound.type_args)))
                                return self.type_ops.substitute_types(field_type, type_subst)
            return None
        if isinstance(expr, TpySubscript):
            obj_type = self.declared_type_for_expr(expr.obj)
            if obj_type is None:
                return None
            actual_type = unwrap_readonly(obj_type)
            if isinstance(actual_type, OptionalType):
                if actual_type.inner.is_value_type():
                    return None
                actual_type = actual_type.inner
            elem_type = actual_type.get_element_type()
            if elem_type is not None:
                return elem_type
            if is_protocol_type(actual_type):
                return self._get_protocol_getitem_type(actual_type)
            if isinstance(actual_type, NamedType) and actual_type.is_record:
                return self._get_record_getitem_type(actual_type)
        return None

    def _get_protocol_getitem_type(self, protocol: NamedType) -> TpyType | None:
        """Get __getitem__ return type for a protocol (returns None on failure)."""
        protocol_info = self.ctx.registry.get_protocol(protocol.name)
        if protocol_info is None:
            return None
        type_subst: dict[str, TpyType] = {}
        if protocol_info.type_params and protocol.type_args:
            type_subst = dict(zip(protocol_info.type_params, protocol.type_args))
        for method_sig in protocol_info.methods:
            if method_sig.name == "__getitem__":
                if type_subst:
                    return self.type_ops.substitute_types(method_sig.return_type, type_subst)
                return method_sig.return_type
        return None

    def _get_record_getitem_type(self, record_type: NamedType) -> TpyType | None:
        """Get __getitem__ return type for a record (returns None on failure)."""
        record = self.ctx.registry.get_record(record_type.name)
        if record is None:
            return None
        getitem = self.protocols.lookup_record_method(record, "__getitem__")
        if getitem is None:
            return None
        type_subst = self.type_ops.build_type_substitution(record_type)
        if type_subst:
            return self.type_ops.substitute_type_params(getitem.return_type, type_subst)
        return getitem.return_type

    def expr_identity(self, expr: TpyExpr) -> ExprIdentity | None:
        """Compute a stable identity tuple for an expression (or None if not trackable)."""
        if isinstance(expr, TpyName):
            return (expr.name,)
        if isinstance(expr, TpyFieldAccess):
            base = self.expr_identity(expr.obj)
            if base is None:
                return None
            return base + (f".{expr.field}",)
        if isinstance(expr, TpySubscript):
            base = self.expr_identity(expr.obj)
            if base is None:
                return None
            base_type = self.declared_type_for_expr(expr.obj)
            if base_type is None:
                return None
            if isinstance(base_type, OptionalType):
                base_type = base_type.inner
            if not self._is_builtin_stable_subscript_type(base_type):
                return None
            token = self._simple_index_token(expr.index)
            if token is None:
                return None
            return base + (f"[{token}]",)
        return None

    def narrow_optional_expr_type(self, expr: TpyExpr, typ: TpyType) -> TpyType:
        """Narrow Optional type using expression-identity flow facts."""
        if isinstance(typ, OptionalType):
            identity = self.expr_identity(expr)
            if identity is not None and identity in self.ctx.non_none_exprs:
                if isinstance(expr, (TpyFieldAccess, TpySubscript)):
                    expr.narrowed_optional_proven = True
                return typ.inner
        return typ

    # -- Condition fact extraction --------------------------------------

    @staticmethod
    def _is_optional_type(typ: TpyType | None) -> bool:
        """Check if type is Optional (possibly wrapped in ReadonlyType)."""
        if typ is None:
            return False
        if isinstance(typ, ReadonlyType):
            typ = typ.wrapped
        return isinstance(typ, OptionalType)

    @staticmethod
    def _optional_inner_type(typ: TpyType) -> TpyType:
        """Extract inner type from Optional, preserving ReadonlyType wrapper."""
        if isinstance(typ, ReadonlyType):
            return ReadonlyType(typ.wrapped.inner)
        return typ.inner

    def _expr_none_facts(self, expr: TpyExpr) -> tuple[set[ExprIdentity], set[ExprIdentity]]:
        """Return (facts_if_true, facts_if_false) for Optional expression identities."""
        if isinstance(expr, (TpyName, TpyFieldAccess, TpySubscript)):
            identity = self.expr_identity(expr)
            declared = self.declared_type_for_expr(expr)
            if identity is not None and self._is_optional_type(declared):
                return {identity}, set()

        if isinstance(expr, TpyUnaryOp) and expr.op == "!":
            true_facts, false_facts = self._expr_none_facts(expr.operand)
            return false_facts, true_facts

        if isinstance(expr, TpyBinOp):
            if expr.op in ("is", "is not"):
                identity_expr: TpyExpr | None = None
                if isinstance(expr.right, TpyNoneLiteral):
                    identity_expr = expr.left
                elif isinstance(expr.left, TpyNoneLiteral):
                    identity_expr = expr.right
                if identity_expr is not None:
                    identity = self.expr_identity(identity_expr)
                    declared = self.declared_type_for_expr(identity_expr)
                    if identity is not None and self._is_optional_type(declared):
                        if expr.op == "is not":
                            return {identity}, set()
                        return set(), {identity}
            if expr.op == "&&":
                left_true, left_false = self._expr_none_facts(expr.left)
                right_true, right_false = self._expr_none_facts(expr.right)
                return left_true | right_true, left_false & right_false
            if expr.op == "||":
                left_true, left_false = self._expr_none_facts(expr.left)
                right_true, right_false = self._expr_none_facts(expr.right)
                return left_true & right_true, left_false | right_false
        return set(), set()

    def condition_expr_facts(
        self, condition: TpyExpr
    ) -> tuple[set[ExprIdentity], set[ExprIdentity]]:
        """Get (true_facts, false_facts) for Optional expression-identity narrowing."""
        return self._expr_none_facts(condition)

    # -- Type narrowing (isinstance, is None, truthiness) ---------------

    def _isinstance_facts(
        self, expr: TpyExpr,
    ) -> tuple[dict[str, TpyType], dict[str, TpyType]]:
        """Extract (true_facts, false_facts) for type narrowing.

        Handles isinstance checks (union), is/is not None (union + optional),
        and truthiness (optional).
        """
        if isinstance(expr, TpyCall) and expr.isinstance_var is not None and expr.isinstance_type is not None:
            name = expr.isinstance_var
            check_type = expr.isinstance_type
            # Get the effective union type (may already be narrowed)
            effective = self.ctx.narrowed_types.get(name)
            if effective is None:
                effective = self.declared_type_for_name(name)
            # NOTE: doesn't unwrap ReadonlyType -- readonly unions can't
            # reach here today, but add unwrap_readonly if that changes.
            if isinstance(effective, UnionType):
                remaining = [m for m in effective.members if m != check_type]
                if remaining:
                    false_type = make_union(*remaining)
                else:
                    false_type = check_type
                return {name: check_type}, {name: false_type}

        # is None / is not None on union or optional types
        match = match_is_none(expr)
        if match is not None:
            name, is_not_none = match
            effective = self.ctx.narrowed_types.get(name)
            if effective is None:
                effective = self.declared_type_for_name(name)
            if isinstance(effective, UnionType) and effective.has_none_member():
                non_none_type, none_type = union_none_narrow(effective)
                if is_not_none:
                    return {name: non_none_type}, {name: none_type}
                else:
                    return {name: none_type}, {name: non_none_type}
            if self._is_optional_type(effective):
                inner_type = self._optional_inner_type(effective)
                if is_not_none:
                    return {name: inner_type}, {}
                else:
                    return {}, {name: inner_type}

        # Truthiness on Optional: `if x:` narrows to inner type in true branch
        if isinstance(expr, TpyName):
            effective = self.ctx.narrowed_types.get(expr.name)
            if effective is None:
                effective = self.declared_type_for_name(expr.name)
            if self._is_optional_type(effective):
                inner_type = self._optional_inner_type(effective)
                return {expr.name: inner_type}, {}

        if isinstance(expr, TpyUnaryOp) and expr.op == "!":
            true_facts, false_facts = self._isinstance_facts(expr.operand)
            return false_facts, true_facts

        if isinstance(expr, TpyBinOp):
            if expr.op == "&&":
                left_true, left_false = self._isinstance_facts(expr.left)
                right_true, right_false = self._isinstance_facts(expr.right)
                merged_true = {**left_true, **right_true}
                merged_false = {k: v for k, v in left_false.items()
                                if k in right_false and right_false[k] == v}
                return merged_true, merged_false
            if expr.op == "||":
                left_true, left_false = self._isinstance_facts(expr.left)
                right_true, right_false = self._isinstance_facts(expr.right)
                merged_true = {k: v for k, v in left_true.items()
                               if k in right_true and right_true[k] == v}
                merged_false = {**left_false, **right_false}
                return merged_true, merged_false

        return {}, {}

    def condition_type_facts(
        self, condition: TpyExpr,
    ) -> tuple[dict[str, TpyType], dict[str, TpyType]]:
        """Get (true_facts, false_facts) for type narrowing (isinstance, is None, truthiness)."""
        return self._isinstance_facts(condition)

    # -- Truthiness warnings -------------------------------------------

    def _truthy_names(self, expr: TpyExpr) -> set[str]:
        """Collect Optional variable names used in truthiness contexts."""
        if isinstance(expr, TpyName):
            declared = self.declared_type_for_name(expr.name)
            if self._is_optional_type(declared):
                return {expr.name}
            return set()
        if isinstance(expr, TpyUnaryOp) and expr.op == "!":
            return self._truthy_names(expr.operand)
        if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
            return self._truthy_names(expr.left) | self._truthy_names(expr.right)
        return set()

    def condition_truthy_value_optional_names(self, condition: TpyExpr) -> set[str]:
        """Get value-optionals used via truthiness in a condition."""
        names = self._truthy_names(condition)
        result: set[str] = set()
        for name in names:
            declared = self.declared_type_for_name(name)
            inner = unwrap_readonly(declared) if declared else None
            if isinstance(inner, OptionalType) and inner.inner.is_value_type():
                result.add(name)
        return result

    def warn_truthy_value_optionals(self, condition: TpyExpr) -> None:
        """Warn when truthiness narrows optional value-typed variables."""
        names = self.condition_truthy_value_optional_names(condition)
        for name in sorted(names):
            self.ctx.warning(
                f"{OPTIONAL_VALUE_TRUTHINESS_WARNING} (variable '{name}')",
                condition,
            )

    # -- Fact invalidation on writes/calls -----------------------------

    def update_after_write(
        self,
        name: str,
        target_type: TpyType,
        rhs_type: TpyType | None = None,
        rhs_expr: TpyExpr | None = None,
    ) -> None:
        """Update flow facts after assigning/writing a variable."""
        self.ctx.narrowed_types.pop(name, None)
        # For Optional targets, re-narrow if RHS is provably non-None
        inner_target = unwrap_readonly(target_type)
        if not isinstance(inner_target, OptionalType):
            return
        if isinstance(rhs_type, (NoneType, OptionalType)) or isinstance(rhs_expr, TpyNoneLiteral):
            return
        if rhs_type is None:
            return
        self.ctx.narrowed_types[name] = self._optional_inner_type(target_type)

    def _identity_root_name(self, expr: TpyExpr) -> str | None:
        if isinstance(expr, TpyName):
            return expr.name
        if isinstance(expr, (TpyFieldAccess, TpySubscript)):
            return self._identity_root_name(expr.obj)
        return None

    def kill_facts_rooted_at(self, root: str) -> None:
        """Remove all expression-identity facts rooted at a given variable."""
        if not self.ctx.non_none_exprs:
            return
        self.ctx.non_none_exprs = {k for k in self.ctx.non_none_exprs if not k or k[0] != root}

    def kill_facts_for_target(self, target: TpyExpr) -> None:
        """Remove expression-identity facts touching an assignment target."""
        root = self._identity_root_name(target)
        if root is not None:
            self.kill_facts_rooted_at(root)

    def expr_has_unknown_call(self, expr: TpyExpr) -> bool:
        """Check if an expression contains a non-readonly call."""
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            if self._is_readonly_call is None or not self._is_readonly_call(expr):
                return True
            if isinstance(expr, TpyMethodCall) and self.expr_has_unknown_call(expr.obj):
                return True
            if any(self.expr_has_unknown_call(arg) for arg in expr.args):
                return True
            if isinstance(expr, TpyCall):
                return any(self.expr_has_unknown_call(v) for v in expr.kwargs.values())
            return False
        if isinstance(expr, TpyBinOp):
            return self.expr_has_unknown_call(expr.left) or self.expr_has_unknown_call(expr.right)
        if isinstance(expr, TpyUnaryOp):
            return self.expr_has_unknown_call(expr.operand)
        if isinstance(expr, TpyFieldAccess):
            return self.expr_has_unknown_call(expr.obj)
        if isinstance(expr, TpySubscript):
            return self.expr_has_unknown_call(expr.obj) or self.expr_has_unknown_call(expr.index)
        if isinstance(expr, TpyArrayLiteral):
            return any(self.expr_has_unknown_call(e) for e in expr.elements)
        if isinstance(expr, TpyListRepeat):
            return any(self.expr_has_unknown_call(e) for e in expr.elements) or self.expr_has_unknown_call(expr.count)
        if isinstance(expr, TpyCoerce):
            return self.expr_has_unknown_call(expr.expr)
        return False
