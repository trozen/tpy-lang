"""
TurboPython Operator Resolution

Binary and unary operator resolution using the type registry.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, IntLiteralType, TypeParamRef,
    ResolvedBinop, ResolvedUnaryop, FunctionInfo, TypeParamKind,
    INT32, PendingListType, ListType,
)
from .overloads import type_matches_numeric, type_matches_strict
from tpyc import modules as builtin_modules

if TYPE_CHECKING:
    from .context import SemanticContext


# C++ templates for user-defined dunder methods (used for unified operator resolution)
# These use C++ operator syntax which works for user types with operator overloads
DUNDER_CPP_TEMPLATES: dict[str, str] = {
    # Binary operators
    "__add__": "({self}) + ({0})",
    "__sub__": "({self}) - ({0})",
    "__mul__": "({self}) * ({0})",
    "__truediv__": "({self}) / ({0})",
    "__floordiv__": "({self}) / ({0})",  # User types use regular division
    "__mod__": "({self}) % ({0})",
    "__pow__": "std::pow({self}, {0})",
    "__lshift__": "({self}) << ({0})",
    "__rshift__": "({self}) >> ({0})",
    "__and__": "({self}) & ({0})",
    "__or__": "({self}) | ({0})",
    "__xor__": "({self}) ^ ({0})",
    # Reverse operators
    "__radd__": "({0}) + ({self})",
    "__rsub__": "({0}) - ({self})",
    "__rmul__": "({0}) * ({self})",
    "__rtruediv__": "({0}) / ({self})",
    "__rfloordiv__": "({0}) / ({self})",
    "__rmod__": "({0}) % ({self})",
    "__rpow__": "std::pow({0}, {self})",
    "__rlshift__": "({0}) << ({self})",
    "__rrshift__": "({0}) >> ({self})",
    "__rand__": "({0}) & ({self})",
    "__ror__": "({0}) | ({self})",
    "__rxor__": "({0}) ^ ({self})",
    # Unary operators
    "__pos__": "+({self})",
    "__neg__": "-({self})",
    "__invert__": "~({self})",
    # Conversion methods (used for operator promotion)
    "__int__": "({self}).__int__()",
}


def _substitute_type_params(typ: TpyType, subst: dict[str, TpyType]) -> TpyType:
    """Substitute TypeParamRef instances in a type according to subst map."""
    if isinstance(typ, TypeParamRef) and typ.name in subst:
        return subst[typ.name]
    return typ.map_inner_types(lambda t: _substitute_type_params(t, subst))


class OperatorResolver:
    """Resolves binary and unary operators using the type registry."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def _resolve_pending_types(self, tpy_type: TpyType) -> TpyType:
        """Resolve pending/structural types (PendingListType, TypeParamRef).

        Preserves IntLiteralType for flexible overload matching.
        """
        if isinstance(tpy_type, TypeParamRef) and tpy_type.kind == TypeParamKind.INT:
            return INT32
        if isinstance(tpy_type, PendingListType):
            return ListType(tpy_type.element_type)
        return tpy_type

    def get_effective_type_for_binop(self, tpy_type: TpyType) -> TpyType:
        """Fully resolve type for registry lookup (also resolves IntLiteralType)."""
        if isinstance(tpy_type, IntLiteralType):
            return self.ctx.default_int_for_literal(tpy_type)
        return self._resolve_pending_types(tpy_type)

    def _build_type_subst(
        self, receiver_type: TpyType, arg_effective: TpyType,
    ) -> dict[str, TpyType]:
        """Build type param substitution from a concrete receiver type.

        When the receiver has IntLiteralType element(s), adapts to the arg's
        concrete element type if available, otherwise resolves to default int.
        """
        subst = builtin_modules.extract_type_params(receiver_type)
        if not subst:
            return {}
        arg_params = builtin_modules.extract_type_params(arg_effective)
        for name, typ in list(subst.items()):
            if isinstance(typ, IntLiteralType):
                arg_val = arg_params.get(name)
                if arg_val is not None and not isinstance(arg_val, IntLiteralType):
                    subst[name] = arg_val
                else:
                    subst[name] = self.ctx.default_int_for_literal(typ)
        return subst

    def _find_matching_overload(
        self, overloads: list[FunctionInfo], arg_type: TpyType,
        type_subst: dict[str, TpyType],
    ) -> FunctionInfo | None:
        """Find an overload matching arg_type, substituting type params first."""
        for method in overloads:
            if len(method.params) == 1:
                _, param_type = method.params[0]
                if type_subst:
                    param_type = _substitute_type_params(param_type, type_subst)
                if type_matches_strict(arg_type, param_type) or type_matches_numeric(arg_type, param_type):
                    return method
        return None

    def _make_resolved(
        self, method: FunctionInfo, type_subst: dict[str, TpyType],
        receiver_type: TpyType, left_wrapper: str = "{expr}",
        right_wrapper: str = "{expr}", is_reverse: bool = False,
    ) -> ResolvedBinop:
        """Create ResolvedBinop with type params substituted in method signature."""
        if type_subst:
            new_params = [
                dc_replace(p, type=_substitute_type_params(p.type, type_subst))
                for p in method.params
            ]
            new_return = _substitute_type_params(method.return_type, type_subst)
            method = dc_replace(method, params=new_params, return_type=new_return)
            # Rebuild receiver_type from subst to resolve IntLiteralType elements
            params_map = builtin_modules.extract_type_params(receiver_type)
            if params_map and receiver_type.inner_types():
                new_inner = tuple(type_subst.get(name, orig)
                                  for name, orig in params_map.items())
                receiver_type = receiver_type.with_inner_types(new_inner)
        return ResolvedBinop(
            method=method,
            left_wrapper=left_wrapper,
            right_wrapper=right_wrapper,
            is_reverse=is_reverse,
            receiver_type=receiver_type,
        )

    def resolve_binop(self, left_type: TpyType, op: str, right_type: TpyType) -> ResolvedBinop | None:
        """Resolve binary operator using registry.

        Handles both builtin types and user-defined types with dunder methods.
        User-defined methods have cpp_template set to C++ operator syntax.

        Generic methods (e.g. list[T].__add__) have their type params substituted
        based on the receiver's concrete element type.

        Tries in order:
        1. left.__add__(right) - direct match
        2. If left has __int__ returning right's type, promote left and use right's __add__
        3. right.__radd__(left) - reverse operator
        4. If right has __int__ returning left's type, promote right and use left's __add__
        """
        method_name = builtin_modules.BINOP_TO_METHOD.get(op)
        rmethod_name = builtin_modules.BINOP_TO_RMETHOD.get(op)
        if not method_name:
            return None

        # Get effective types (IntLiteralType -> configured default int type,
        # PendingListType -> ListType)
        left_effective = self.get_effective_type_for_binop(left_type)
        right_effective = self.get_effective_type_for_binop(right_type)

        left_record = self.ctx.registry.get_record_for_type(left_effective)
        right_record = self.ctx.registry.get_record_for_type(right_effective)

        # Build type substitution maps for generic types (e.g. list[T] -> list[Int32])
        left_subst = self._build_type_subst(left_effective, right_effective)
        right_subst = self._build_type_subst(right_effective, left_effective)

        # For overload matching, resolve structural types but preserve
        # IntLiteralType flexibility (matches any numeric type)
        left_arg = self._resolve_pending_types(left_type)
        right_arg = self._resolve_pending_types(right_type)

        # 1. Try direct: left.__add__(right)
        if left_record:
            overloads = left_record.get_method_overloads(method_name)
            if method := self._find_matching_overload(overloads, right_arg, left_subst):
                return self._make_resolved(method, left_subst, left_effective)

        # 2. Try promoting left to right's type via __int__
        if left_record and right_record:
            int_overloads = left_record.get_method_overloads("__int__")
            if int_overloads:
                int_method = int_overloads[0]
                promoted_type = int_method.return_type
                # Check if promoted type matches right's type
                if self.ctx.registry.get_record_for_type(promoted_type) == right_record:
                    right_overloads = right_record.get_method_overloads(method_name)
                    if method := self._find_matching_overload(right_overloads, right_arg, right_subst):
                        return self._make_resolved(
                            method, right_subst, right_effective,
                            left_wrapper=int_method.cpp_template or "{expr}",
                        )

        # 3. Try reverse: right.__radd__(left)
        if right_record and rmethod_name:
            overloads = right_record.get_method_overloads(rmethod_name)
            if method := self._find_matching_overload(overloads, left_arg, right_subst):
                return self._make_resolved(
                    method, right_subst, right_effective, is_reverse=True,
                )

        # 4. Try promoting right to left's type via __int__, then use left's operator
        if right_record and left_record:
            int_overloads = right_record.get_method_overloads("__int__")
            if int_overloads:
                int_method = int_overloads[0]
                promoted_type = int_method.return_type
                # Check if promoted type matches left's type
                if self.ctx.registry.get_record_for_type(promoted_type) == left_record:
                    left_overloads = left_record.get_method_overloads(method_name)
                    if method := self._find_matching_overload(left_overloads, promoted_type, left_subst):
                        return self._make_resolved(
                            method, left_subst, left_effective,
                            right_wrapper=int_method.cpp_template or "{expr}",
                        )

        return None

    def resolve_aug_inplace(self, target_type: TpyType, op: str, value_type: TpyType) -> ResolvedBinop | None:
        """Resolve in-place augmented assignment operator (e.g. __iadd__, __ior__).

        Returns a ResolvedBinop whose method has a void return type and a cpp
        template that mutates {self} in place (e.g. tpy::set_update({self}, {0})).
        For user-defined methods, generates {self}.__iadd__({0}) call.
        """
        method_name = builtin_modules.AUGOP_TO_IMETHOD.get(op)
        if not method_name:
            return None

        target_effective = self.get_effective_type_for_binop(target_type)
        record = self.ctx.registry.get_record_for_type(target_effective)
        if not record:
            return None

        type_subst = self._build_type_subst(target_effective, value_type)
        value_arg = self._resolve_pending_types(value_type)

        overloads = record.get_method_overloads(method_name)
        # Try builtin methods with cpp_template first
        builtin_overloads = [m for m in overloads if m.cpp_template]
        if method := self._find_matching_overload(builtin_overloads, value_arg, type_subst):
            return self._make_resolved(method, type_subst, target_effective)

        # Then try user-defined methods (no cpp_template)
        user_overloads = [m for m in overloads if not m.cpp_template]
        if method := self._find_matching_overload(user_overloads, value_arg, type_subst):
            return self._make_resolved(method, type_subst, target_effective)

        return None

    def resolve_unaryop(self, operand_type: TpyType, op: str) -> ResolvedUnaryop | None:
        """Resolve unary operator using registry."""
        method_name = builtin_modules.UNARYOP_TO_METHOD.get(op)
        if not method_name:
            return None

        effective_type = self.get_effective_type_for_binop(operand_type)
        record = self.ctx.registry.get_record_for_type(effective_type)
        if record:
            overloads = record.get_method_overloads(method_name)
            if overloads and len(overloads[0].params) == 0:
                return ResolvedUnaryop(method=overloads[0])

        return None
