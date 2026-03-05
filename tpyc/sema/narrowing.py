"""
TurboPython Narrowing Tracker

Centralizes type narrowing (Optional + Union) and fact invalidation on writes.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, OptionalType, NoneType, VoidType, PtrType, OwnType, NamedType,
    TypeParamRef,
    ReadonlyType, UnionType, unwrap_readonly, make_union, union_none_narrow,
    is_protocol_type,
)
from ..parse import (
    TpyExpr, TpyName, TpyBinOp, TpyUnaryOp, TpyFieldAccess,
    TpySubscript, TpyNoneLiteral, TpyCall, TpyMethodCall,
)
from ..prescan import match_is_none
from ..namespace import BindingKind
from .diagnostics import OPTIONAL_VALUE_TRUTHINESS_WARNING

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker


class NarrowingTracker:
    """Centralized type narrowing flow analysis.

    Operates on ctx.narrowed_types without owning it.
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

    def _resolve_field_path_type(self, key: str) -> TpyType | None:
        """Resolve the declared type for a dotted field path like 'obj.field'."""
        parts = key.split(".", 1)
        if len(parts) != 2:
            return None
        obj_name, field_name = parts
        obj_type = self.declared_type_for_name(obj_name)
        if obj_type is None:
            return None
        return self.declared_type_for_expr(TpyFieldAccess(TpyName(obj_name), field_name))

    def effective_union_type(self, name: str) -> TpyType | None:
        """Get effective type for isinstance/narrowing, falling through assignment narrowing.

        If the variable is narrowed to a concrete (non-union) type by assignment
        narrowing, returns the declared union type instead, since isinstance and
        condition_type_facts need the full union to compute branch facts.
        """
        effective = self.ctx.narrowed_types.get(name)
        if effective is None:
            effective = self.declared_type_for_name(name)
        if not isinstance(effective, UnionType):
            declared = self.declared_type_for_name(name)
            if declared is not None:
                inner = unwrap_readonly(declared)
                if isinstance(inner, UnionType):
                    return inner
        return effective

    # -- Declared type resolution for expressions ------------------------

    def declared_type_for_expr(self, expr: TpyExpr) -> TpyType | None:
        """Get declared type for identity-capable expressions without flow narrowing."""
        if isinstance(expr, TpyName):
            return self.declared_type_for_name(expr.name)
        if isinstance(expr, TpyFieldAccess):
            obj_type = self.declared_type_for_expr(expr.obj)
            if obj_type is None:
                return None
            actual_type = unwrap_readonly(obj_type)
            if isinstance(actual_type, PtrType):
                actual_type = actual_type.pointee
            elif isinstance(actual_type, OwnType):
                actual_type = actual_type.wrapped
            elif isinstance(actual_type, OptionalType):
                if actual_type.inner.is_value_type():
                    return None
                actual_type = actual_type.inner

            if isinstance(actual_type, NamedType) and actual_type.is_record:
                record = self.ctx.registry.get_record_for_type(actual_type)
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
        record = self.ctx.registry.get_record_for_type(record_type)
        if record is None:
            return None
        getitem = self.protocols.lookup_record_method(record, "__getitem__")
        if getitem is None:
            return None
        type_subst = self.type_ops.build_type_substitution(record_type)
        if type_subst:
            return self.type_ops.substitute_type_params(getitem.return_type, type_subst)
        return getitem.return_type

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
            effective = self.effective_union_type(name)
            if isinstance(effective, UnionType):
                remaining = [m for m in effective.members if m != check_type]
                if remaining:
                    false_type = make_union(*remaining)
                else:
                    false_type = check_type
                return {name: check_type}, {name: false_type}
            # Optional[Protocol] isinstance narrows to the inner protocol type
            if self._is_optional_type(effective):
                inner = self._optional_inner_type(effective)
                if is_protocol_type(inner):
                    return {name: inner}, {}

        # is None / is not None on union or optional types
        match = match_is_none(expr)
        if match is not None:
            key, is_not_none = match
            effective = self.ctx.narrowed_types.get(key)
            if effective is None:
                if "." in key:
                    effective = self._resolve_field_path_type(key)
                else:
                    effective = self.declared_type_for_name(key)
            if isinstance(effective, UnionType) and effective.has_none_member():
                non_none_type, none_type = union_none_narrow(effective)
                if is_not_none:
                    return {key: non_none_type}, {key: none_type}
                else:
                    return {key: none_type}, {key: non_none_type}
            if self._is_optional_type(effective):
                inner_type = self._optional_inner_type(effective)
                if is_not_none:
                    return {key: inner_type}, {}
                else:
                    return {}, {key: inner_type}

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
        # Invalidate field narrowing facts rooted at this variable
        self._invalidate_field_facts(name)
        # For Optional targets, re-narrow if RHS is provably non-None
        inner_target = unwrap_readonly(target_type)
        if not isinstance(inner_target, OptionalType):
            return
        if isinstance(rhs_type, (NoneType, OptionalType)) or isinstance(rhs_expr, TpyNoneLiteral):
            return
        if rhs_type is None:
            return
        self.ctx.narrowed_types[name] = self._optional_inner_type(target_type)

    def _invalidate_field_facts(self, name: str) -> None:
        """Remove all field narrowing facts rooted at the given variable name."""
        prefix = name + "."
        stale = [k for k in self.ctx.narrowed_types if k.startswith(prefix)]
        for k in stale:
            del self.ctx.narrowed_types[k]

    def invalidate_field_facts_for_call(self, call: TpyCall) -> None:
        """Invalidate field narrowing facts for name arguments passed by mutable reference.

        When a non-value-type object is passed to a function, the callee receives
        a mutable reference and may modify any field, so field narrowing facts
        for that object are no longer reliable.
        """
        for arg in call.args:
            if not isinstance(arg, TpyName):
                continue
            arg_type = self.ctx.get_expr_type(arg)
            if arg_type is None:
                continue
            inner = unwrap_readonly(arg_type)
            if inner.is_value_type():
                continue
            self._invalidate_field_facts(arg.name)

    def invalidate_field_facts_for_method_call(self, call: TpyMethodCall) -> None:
        """Invalidate field narrowing facts after a method call.

        The receiver object is passed as mutable self, so any field could be mutated.
        Also invalidates for any non-value-type arguments.
        """
        if isinstance(call.obj, TpyName) and not call.is_static_call:
            self._invalidate_field_facts(call.obj.name)
        for arg in call.args:
            if not isinstance(arg, TpyName):
                continue
            arg_type = self.ctx.get_expr_type(arg)
            if arg_type is None:
                continue
            inner = unwrap_readonly(arg_type)
            if inner.is_value_type():
                continue
            self._invalidate_field_facts(arg.name)

