"""
TurboPython Narrowing Tracker

Centralizes type narrowing (Optional + Union) and fact invalidation on writes.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, OptionalType, NoneType, VoidType, PtrType, OwnType, NamedType,
    TypeParamRef, FixedIntType, BigIntType, IntLiteralType,
    ReadonlyType, UnionType, unwrap_readonly, unwrap_ref_type, make_union, union_none_narrow,
    is_protocol_type, LiteralType, LiteralValue,
)
from ..parse import (
    TpyExpr, TpyName, TpyBinOp, TpyUnaryOp, TpyFieldAccess,
    TpySubscript, TpyNoneLiteral, TpyCall, TpyMethodCall,
    TpyIntLiteral, TpyStrLiteral, TpyBoolLiteral, TpyCoerce, TpyNamedExpr,
)
from .value_range import ValueRange
from ..prescan import match_is_none, _expr_to_narrowing_key
from ..namespace import BindingKind
from .diagnostics import OPTIONAL_VALUE_TRUTHINESS_WARNING

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker


class NarrowingTracker:
    """Centralized type narrowing flow analysis.

    Operates on ctx.func.narrowed_types without owning it.
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
        return self.ctx.func.narrowed_types.get(name, typ)

    def declared_type_for_name(self, name: str) -> TpyType | None:
        """Get a variable's declared type (without applying flow narrowing).

        Strips Ref -- declared types reflect the user's annotation, not
        the internal reference-provenance wrapper.
        """
        if self.ctx.func.current_ns:
            binding = self.ctx.func.current_ns.lookup(name)
            if binding and binding.kind == BindingKind.VARIABLE:
                return unwrap_ref_type(binding.type)
        typ = self.ctx.func.current_scope.lookup(name)
        return unwrap_ref_type(typ) if typ is not None else None

    def _resolve_field_path_type(self, key: str) -> TpyType | None:
        """Resolve the declared type for a dotted field path like 'obj.field' or 'obj.a.b'."""
        parts = key.split(".")
        if len(parts) < 2:
            return None
        expr: TpyExpr = TpyName(parts[0])
        for field in parts[1:]:
            expr = TpyFieldAccess(expr, field)
        return self.declared_type_for_expr(expr)

    def effective_union_type(self, name: str) -> TpyType | None:
        """Get effective type for isinstance/narrowing, falling through assignment narrowing.

        If the variable is narrowed to a concrete (non-union) type by assignment
        narrowing, returns the declared union type instead, since isinstance and
        condition_type_facts need the full union to compute branch facts.
        """
        effective = self.ctx.func.narrowed_types.get(name)
        if effective is None:
            effective = self.declared_type_for_name(name)
        # Strip Own[T] -- narrowing operates on the underlying type
        if isinstance(effective, OwnType):
            effective = effective.wrapped
        if not isinstance(effective, UnionType):
            declared = self.declared_type_for_name(name)
            if declared is not None:
                inner = unwrap_readonly(declared)
                if isinstance(inner, OwnType):
                    inner = inner.wrapped
                if isinstance(inner, UnionType):
                    return inner
        # Expand recursive union alias NamedType to underlying UnionType
        # (e.g. after Optional narrowing: Tree | None -> Tree -> int | list[Tree])
        if (isinstance(effective, NamedType)
                and not effective.is_protocol and not effective.is_module_type
                and effective.name in self.ctx.recursive_union_names):
            alias = self.ctx.registry.get_type_alias(effective.name)
            if alias is not None:
                return alias
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
            actual_type = unwrap_readonly(unwrap_ref_type(obj_type))
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
        """Extract inner type from Optional, preserving ReadonlyType/OwnType wrappers."""
        if isinstance(typ, ReadonlyType):
            return ReadonlyType(typ.wrapped.inner)
        if isinstance(typ, OwnType):
            inner = typ.wrapped
            if isinstance(inner, OptionalType):
                return inner.inner
            return inner
        return typ.inner

    def _effective_type_for_key(self, key: str) -> TpyType | None:
        """Get the effective type for a narrowing key (name or dotted path)."""
        effective = self.ctx.func.narrowed_types.get(key)
        if effective is not None:
            return effective
        if "." in key:
            return self._resolve_field_path_type(key)
        result = self.declared_type_for_name(key)
        # Strip Own[T] -- narrowing operates on the underlying type
        if isinstance(result, OwnType):
            result = result.wrapped
        return result

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
            effective = self._effective_type_for_key(key)
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

        # Truthiness on Optional: `if x:` / `if obj.field:` / `if (x := get()):` narrows
        if isinstance(expr, (TpyName, TpyFieldAccess, TpyNamedExpr)):
            key = _expr_to_narrowing_key(expr)
            if key is not None:
                effective = self._effective_type_for_key(key)
                if self._is_optional_type(effective):
                    inner_type = self._optional_inner_type(effective)
                    return {key: inner_type}, {}

        # Equality narrowing on Literal types: `if mode == "rb":`
        if isinstance(expr, TpyBinOp) and expr.op in ("==", "!="):
            facts = self._literal_equality_facts(expr)
            if facts is not None:
                return facts

        if isinstance(expr, TpyUnaryOp) and expr.op == "!":
            true_facts, false_facts = self._isinstance_facts(expr.operand)
            return false_facts, true_facts

        if isinstance(expr, TpyBinOp):
            if expr.op == "&&":
                left_true, left_false = self._isinstance_facts(expr.left)
                right_true, right_false = self._isinstance_facts(expr.right)
                merged_true = self._merge_facts_both_hold(left_true, right_true)
                merged_false = {k: v for k, v in left_false.items()
                                if k in right_false and right_false[k] == v}
                return merged_true, merged_false
            if expr.op == "||":
                left_true, left_false = self._isinstance_facts(expr.left)
                right_true, right_false = self._isinstance_facts(expr.right)
                merged_true = self._merge_facts_either_holds(left_true, right_true)
                merged_false = self._merge_facts_both_hold(left_false, right_false)
                return merged_true, merged_false

        return {}, {}

    @staticmethod
    def _merge_facts_either_holds(
        left: dict[str, TpyType], right: dict[str, TpyType],
    ) -> dict[str, TpyType]:
        """Merge facts where at least one holds (||-true, &&-false).

        For same key with LiteralType on both sides, union the value sets.
        For same key with non-Literal types, keep only if they agree exactly.
        Keys present in only one side are dropped (can't assume which held).
        """
        merged = {}
        for k, lv in left.items():
            if k not in right:
                continue
            rv = right[k]
            if (isinstance(lv, LiteralType) and isinstance(rv, LiteralType)
                    and lv.base_type == rv.base_type):
                seen = set(lv.values)
                combined = list(lv.values) + [v for v in rv.values if v not in seen]
                merged[k] = LiteralType(base_type=lv.base_type, values=tuple(combined))
            elif lv == rv:
                merged[k] = lv
        return merged

    @staticmethod
    def _merge_facts_both_hold(
        left: dict[str, TpyType], right: dict[str, TpyType],
    ) -> dict[str, TpyType]:
        """Merge facts where both must hold (&&-true, ||-false).

        For distinct keys, include both. For same key with LiteralType on
        both sides, intersect the value sets. For non-Literal same-key
        conflicts, right overrides left (preserves pre-existing behavior
        where isinstance narrows further after is-not-None).
        """
        merged = dict(left)
        for k, rv in right.items():
            if k not in merged:
                merged[k] = rv
                continue
            lv = merged[k]
            if (isinstance(lv, LiteralType) and isinstance(rv, LiteralType)
                    and lv.base_type == rv.base_type):
                common = tuple(v for v in lv.values if v in rv.values)
                if common:
                    merged[k] = LiteralType(base_type=lv.base_type, values=common)
                else:
                    merged[k] = lv.base_type
            else:
                merged[k] = rv
        return merged

    def _literal_equality_facts(
        self, expr: TpyBinOp,
    ) -> tuple[dict[str, TpyType], dict[str, TpyType]] | None:
        """Extract narrowing facts for `x == lit` / `lit == x` on LiteralType vars."""
        key: str | None = None
        lit_val: LiteralValue | None = None
        # Try both directions: `x == "rb"` and `"rb" == x`
        for var_side, lit_side in [(expr.left, expr.right), (expr.right, expr.left)]:
            k = _expr_to_narrowing_key(var_side)
            if k is None:
                continue
            lv = self._extract_literal_value(lit_side)
            if lv is not None:
                key, lit_val = k, lv
                break
        if key is None or lit_val is None:
            return None
        effective = self._effective_type_for_key(key)
        if not isinstance(effective, LiteralType):
            return None
        if lit_val not in effective.values:
            return None
        true_type = LiteralType(base_type=effective.base_type, values=(lit_val,))
        remaining = tuple(v for v in effective.values if v != lit_val)
        false_type = (LiteralType(base_type=effective.base_type, values=remaining)
                      if remaining else effective.base_type)
        if expr.op == "==":
            return {key: true_type}, {key: false_type}
        else:
            return {key: false_type}, {key: true_type}

    @staticmethod
    def _extract_literal_value(expr: TpyExpr) -> LiteralValue | None:
        """Extract a LiteralValue from a literal expression node."""
        if isinstance(expr, TpyStrLiteral):
            return LiteralValue("str", expr.value)
        if isinstance(expr, TpyBoolLiteral):
            return LiteralValue("bool", expr.value)
        if isinstance(expr, TpyIntLiteral):
            return LiteralValue("int", expr.value)
        # Negative int: -1 is TpyUnaryOp("-", TpyIntLiteral(1))
        if (isinstance(expr, TpyUnaryOp) and expr.op == "-"
                and isinstance(expr.operand, TpyIntLiteral)):
            return LiteralValue("int", -expr.operand.value)
        return None

    def condition_type_facts(
        self, condition: TpyExpr,
    ) -> tuple[dict[str, TpyType], dict[str, TpyType]]:
        """Get (true_facts, false_facts) for type narrowing (isinstance, is None, truthiness)."""
        return self._isinstance_facts(condition)

    def condition_ptr_null_facts(
        self, condition: TpyExpr,
    ) -> tuple[set[str], set[str]]:
        """Get (true_nonnull, false_nonnull) for Ptr is/is not None checks.

        Returns names proven non-null in the true branch and names proven
        non-null in the false branch, respectively. When `p is not None`
        and p is a Ptr[T], returns ({p}, {}) -- p is non-null when the
        condition is true. When `p is None`, returns ({}, {p}) -- p is
        non-null when the condition is false.
        """
        return self._ptr_null_facts(condition)

    def _ptr_null_facts(
        self, expr: TpyExpr,
    ) -> tuple[set[str], set[str]]:
        match = match_is_none(expr)
        if match is not None:
            key, is_not_none = match
            if "." in key:
                declared = self._resolve_field_path_type(key)
            else:
                declared = self.declared_type_for_name(key)
            if isinstance(declared, PtrType):
                if is_not_none:
                    return {key}, set()
                else:
                    return set(), {key}

        if isinstance(expr, TpyUnaryOp) and expr.op == "!":
            non_null, null = self._ptr_null_facts(expr.operand)
            return null, non_null

        if isinstance(expr, TpyBinOp):
            if expr.op == "&&":
                l_nn, l_n = self._ptr_null_facts(expr.left)
                r_nn, r_n = self._ptr_null_facts(expr.right)
                return l_nn | r_nn, l_n & r_n
            if expr.op == "||":
                l_nn, l_n = self._ptr_null_facts(expr.left)
                r_nn, r_n = self._ptr_null_facts(expr.right)
                return l_nn & r_nn, l_n | r_n

        return set(), set()

    # -- Integer range facts from conditions ----------------------------

    def condition_range_facts(
        self, condition: TpyExpr,
    ) -> tuple[dict[str, ValueRange], dict[str, ValueRange]]:
        """Get (true_facts, false_facts) for integer range narrowing.

        Handles comparisons (!=, ==, <, <=, >, >=), including symbolic
        len() comparisons, and logical composition (and/or/not).
        """
        return self._range_facts(condition)

    def _range_facts(
        self, expr: TpyExpr,
    ) -> tuple[dict[str, ValueRange], dict[str, ValueRange]]:
        if isinstance(expr, TpyUnaryOp) and expr.op == "!":
            true_facts, false_facts = self._range_facts(expr.operand)
            return false_facts, true_facts

        if isinstance(expr, TpyBinOp):
            if expr.op == "&&":
                l_true, l_false = self._range_facts(expr.left)
                r_true, r_false = self._range_facts(expr.right)
                merged_true = _intersect_range_dicts(l_true, r_true)
                merged_false = _merge_range_dicts(l_false, r_false)
                return merged_true, merged_false
            if expr.op == "||":
                l_true, l_false = self._range_facts(expr.left)
                r_true, r_false = self._range_facts(expr.right)
                merged_true = _merge_range_dicts(l_true, r_true)
                merged_false = _intersect_range_dicts(l_false, r_false)
                return merged_true, merged_false

            return self._comparison_range_facts(expr)

        return {}, {}

    def _is_integer_typed(self, expr: TpyExpr) -> bool:
        """Check if an expression has an integer type (fixed-width or BigInt)."""
        typ = self.ctx.get_expr_type(expr)
        if typ is None:
            return False
        return isinstance(typ, (FixedIntType, BigIntType, IntLiteralType))

    def _comparison_range_facts(
        self, expr: TpyBinOp,
    ) -> tuple[dict[str, ValueRange], dict[str, ValueRange]]:
        """Extract range facts from a single comparison (==, !=, <, <=, >, >=)."""
        op = expr.op
        if op not in ("==", "!=", "<", "<=", ">", ">="):
            return {}, {}

        left, right = expr.left, expr.right

        # Normalize: put the variable name on the left side
        name: str | None = None
        other: TpyExpr | None = None

        if isinstance(left, TpyName) and self._is_integer_typed(left):
            name = left.name
            other = right
        elif isinstance(right, TpyName) and self._is_integer_typed(right):
            name = right.name
            other = left
            # Flip the operator: x < 5 becomes 5 > x
            op = {"<": ">", "<=": ">=", ">": "<", ">=": "<=",
                  "==": "==", "!=": "!="}[op]

        if name is None or other is None:
            return {}, {}

        # Determine what 'other' is: literal, len() call, or unknown
        literal_val = self._extract_int_literal(other)
        len_of = self._extract_len_of(other)

        existing = self.ctx.func.value_ranges.get(name, ValueRange())

        if op == "!=":
            if literal_val == 0:
                return {name: existing.with_non_zero()}, {}
            return {}, {}

        if op == "==":
            if literal_val is not None:
                false_facts: dict[str, ValueRange] = {}
                if literal_val == 0:
                    false_facts = {name: existing.with_non_zero()}
                return {name: ValueRange.from_literal(literal_val)}, false_facts
            return {}, {}

        if op == ">":
            if literal_val is not None:
                nz = literal_val >= 0
                r = existing.with_lo(literal_val + 1)
                if nz:
                    r = r.with_non_zero()
                return {name: r}, {name: existing.with_hi(literal_val)}
            return {}, {}

        if op == ">=":
            if literal_val is not None:
                nz = literal_val > 0
                r = existing.with_lo(literal_val)
                if nz:
                    r = r.with_non_zero()
                return {name: r}, {name: existing.with_hi(literal_val - 1)}
            return {}, {}

        if op == "<":
            if literal_val is not None:
                return (
                    {name: existing.with_hi(literal_val - 1)},
                    {name: existing.with_lo(literal_val)},
                )
            if len_of is not None:
                return (
                    {name: existing.with_hi_len_of(len_of)},
                    {},
                )
            return {}, {}

        if op == "<=":
            if literal_val is not None:
                return (
                    {name: existing.with_hi(literal_val)},
                    {name: existing.with_lo(literal_val + 1)},
                )
            return {}, {}

        return {}, {}

    def _extract_int_literal(self, expr: TpyExpr) -> int | None:
        """Extract a concrete integer value from an expression."""
        if isinstance(expr, TpyIntLiteral):
            return expr.value
        return None

    def _extract_len_of(self, expr: TpyExpr) -> str | None:
        """Extract container name from len(container) call."""
        if (isinstance(expr, TpyCall) and expr.func_name == "len"
                and len(expr.args) == 1 and isinstance(expr.args[0], TpyName)):
            return expr.args[0].name
        return None

    # -- Truthiness warnings -------------------------------------------

    def _truthy_names(self, expr: TpyExpr) -> set[str]:
        """Collect Optional variable/field keys used in truthiness contexts."""
        if isinstance(expr, TpyName):
            declared = self.declared_type_for_name(expr.name)
            if self._is_optional_type(declared):
                return {expr.name}
            return set()
        if isinstance(expr, TpyFieldAccess):
            key = _expr_to_narrowing_key(expr)
            if key is not None:
                declared = self._resolve_field_path_type(key)
                if self._is_optional_type(declared):
                    return {key}
            return set()
        if isinstance(expr, TpyUnaryOp) and expr.op == "!":
            return self._truthy_names(expr.operand)
        if isinstance(expr, TpyBinOp) and expr.op in ("&&", "||"):
            return self._truthy_names(expr.left) | self._truthy_names(expr.right)
        return set()

    def condition_truthy_value_optional_names(self, condition: TpyExpr) -> set[str]:
        """Get value-optionals used via truthiness in a condition."""
        keys = self._truthy_names(condition)
        result: set[str] = set()
        for key in keys:
            if "." in key:
                declared = self._resolve_field_path_type(key)
            else:
                declared = self.declared_type_for_name(key)
            inner = unwrap_readonly(declared) if declared else None
            if isinstance(inner, OptionalType) and inner.inner.is_value_type():
                result.add(key)
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
        self.ctx.func.narrowed_types.pop(name, None)
        # Invalidate field narrowing facts rooted at this variable
        self._invalidate_field_facts(name)
        # Invalidate integer range facts for this variable
        self.ctx.func.value_ranges.pop(name, None)
        # Invalidate symbolic bounds referencing this variable's length
        self._invalidate_len_ranges(name)
        # Set range fact for integer literal assignments (e.g. i = 0, i: Int32 = 0).
        # Unwrap one level of TpyCoerce (typed annotations wrap the literal in a coerce node).
        rhs_inner = rhs_expr.expr if isinstance(rhs_expr, TpyCoerce) else rhs_expr
        if isinstance(rhs_inner, TpyIntLiteral):
            self.ctx.func.value_ranges[name] = ValueRange.from_literal(rhs_inner.value)
        # For Optional targets, re-narrow if RHS is provably non-None
        inner_target = unwrap_readonly(target_type)
        if isinstance(inner_target, OwnType):
            inner_target = inner_target.wrapped
        if not isinstance(inner_target, OptionalType):
            return
        if isinstance(rhs_type, (NoneType, OptionalType)) or isinstance(rhs_expr, TpyNoneLiteral):
            return
        if rhs_type is None:
            return
        self.ctx.func.narrowed_types[name] = self._optional_inner_type(target_type)

    def _invalidate_field_facts(self, name: str) -> None:
        """Remove all field narrowing facts rooted at the given variable name."""
        prefix = name + "."
        stale = [k for k in self.ctx.func.narrowed_types if k.startswith(prefix)]
        for k in stale:
            del self.ctx.func.narrowed_types[k]
        stale_ptr = [k for k in self.ctx.func.non_null_ptr_vars if k.startswith(prefix)]
        for k in stale_ptr:
            self.ctx.func.non_null_ptr_vars.discard(k)

    def invalidate_for_field_write(self, target: TpyExpr) -> None:
        """Invalidate narrowing facts for sub-paths when a field is written.

        When `obj.inner = ...` is written, clears Optional narrowing facts for
        deeper paths like `obj.inner.value` (the new object may have different
        field values). Does NOT clear the Optional fact for the written field
        itself -- that is managed by the enclosing `is not None` guard.
        Ptr non-null facts ARE cleared for the written key itself, since a
        field write always introduces a potentially-null pointer value.
        """
        key = _expr_to_narrowing_key(target)
        if key is None or "." not in key:
            return
        prefix = key + "."
        stale = [k for k in self.ctx.func.narrowed_types if k.startswith(prefix)]
        for k in stale:
            del self.ctx.func.narrowed_types[k]
        self.ctx.func.non_null_ptr_vars.discard(key)
        stale_ptr = [k for k in self.ctx.func.non_null_ptr_vars if k.startswith(prefix)]
        for k in stale_ptr:
            self.ctx.func.non_null_ptr_vars.discard(k)

    def _invalidate_len_ranges(self, name: str) -> None:
        """Remove range facts whose symbolic bound references len(name)."""
        stale = [k for k, v in self.ctx.func.value_ranges.items()
                 if v.hi_len_of == name]
        for k in stale:
            del self.ctx.func.value_ranges[k]

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
            self._invalidate_len_ranges(arg.name)

    def invalidate_field_facts_for_method_call(self, call: TpyMethodCall) -> None:
        """Invalidate field narrowing facts after a method call.

        The receiver object is passed as mutable self, so any field could be mutated.
        Also invalidates symbolic len-based range facts for the receiver (the method
        may change the container's length, e.g. pop/clear/insert).
        Also invalidates for any non-value-type arguments.
        """
        if not call.is_static_call:
            if isinstance(call.obj, TpyName):
                self._invalidate_field_facts(call.obj.name)
                self._invalidate_len_ranges(call.obj.name)
            else:
                obj_key = _expr_to_narrowing_key(call.obj)
                if obj_key is not None:
                    self._invalidate_field_facts(obj_key)
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


# -- Module-level helpers for range fact dict operations ------------------

def _intersect_range_dicts(
    a: dict[str, ValueRange], b: dict[str, ValueRange],
) -> dict[str, ValueRange]:
    """Combine two range fact dicts (and-composition): tighten overlapping keys."""
    if not a:
        return b
    if not b:
        return a
    result = dict(a)
    for k, v in b.items():
        if k in result:
            result[k] = ValueRange.intersect(result[k], v)
        else:
            result[k] = v
    return result


def _merge_range_dicts(
    a: dict[str, ValueRange], b: dict[str, ValueRange],
) -> dict[str, ValueRange]:
    """Combine two range fact dicts (or-composition): widen overlapping keys, drop unique."""
    if not a or not b:
        return {}
    result = {}
    for k, v in a.items():
        if k in b:
            result[k] = ValueRange.merge(v, b[k])
    return result

