"""
TurboPython Operator Resolution

Binary and unary operator resolution using the type registry.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Callable

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, LiteralType, TypeParamRef,
    ResolvedBinop, ResolvedUnaryop, FunctionInfo, TypeParamKind,
    INT32, FLOAT, PendingListType, make_list, OwnType, unwrap_ref_type,
    unwrap_readonly, resolve_int_literals,
)
from .overloads import type_matches_numeric, type_matches_strict
from tpyc import modules as builtin_modules

if TYPE_CHECKING:
    from .context import SemanticContext
    from .protocols import ProtocolChecker as ProtocolCheckerObj
    from .type_ops import TypeOperations
    ProtocolChecker = Callable[[TpyType, TpyType], bool]

from .bound_check import raise_if_class_param_bound_violated


# C++ templates for user-defined dunder methods (used for unified operator resolution)
# These use C++ operator syntax which works for user types with operator overloads
DUNDER_CPP_TEMPLATES: dict[str, str] = {
    # Binary operators
    "__add__": "({self}) + ({0})",
    "__sub__": "({self}) - ({0})",
    "__mul__": "({self}) * ({0})",
    "__truediv__": "({self}) / ({0})",
    # C++ has no `//`; lower to a direct method call so `//` stays distinct
    # from `/` (a type may define both __truediv__ and __floordiv__ with the
    # same operand type -- e.g. timedelta -- which cannot share operator/).
    "__floordiv__": "({self}).__floordiv__({0})",
    "__mod__": "({self}) % ({0})",
    "__pow__": "std::pow({self}, {0})",
    "__lshift__": "({self}) << ({0})",
    "__rshift__": "({self}) >> ({0})",
    "__and__": "({self}) & ({0})",
    "__or__": "({self}) | ({0})",
    "__xor__": "({self}) ^ ({0})",
    # Comparison operators
    "__eq__": "({self}) == ({0})",
    "__ne__": "({self}) != ({0})",
    "__lt__": "({self}) < ({0})",
    "__le__": "({self}) <= ({0})",
    "__gt__": "({self}) > ({0})",
    "__ge__": "({self}) >= ({0})",
    # Reverse operators
    "__radd__": "({0}) + ({self})",
    "__rsub__": "({0}) - ({self})",
    "__rmul__": "({0}) * ({self})",
    "__rtruediv__": "({0}) / ({self})",
    "__rfloordiv__": "({self}).__rfloordiv__({0})",
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

    def __init__(
        self, ctx: SemanticContext,
        type_ops: 'TypeOperations', protocols: 'ProtocolCheckerObj',
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        # Used to enforce class-shadowed method type-param bounds at dispatch.
        self.protocols = protocols

    def _check_class_bounds(
        self, method: FunctionInfo, receiver_type: TpyType,
        class_subst: dict[str, 'TpyType | int'], loc_node,
    ) -> None:
        """Raise if the receiver doesn't satisfy a class-shadowed method bound.

        Operator dispatch bypasses `_analyze_generic_method_call` (which runs
        the same check), so every resolve_* path must invoke this so the
        sema-level error matches what codegen's `requires` clause catches at
        C++ instantiation time.
        """
        # Cheap method-level guard first: short-circuits the common case
        # (every operator dispatch where the matched method has no bounds).
        if not method.type_param_bounds:
            return
        # readonly[Box[T]] conforms to any protocol Box[T] does -- mirror that
        # unwrap here so the bound check operates on the inner record.
        record = self.ctx.registry.get_record_for_type(unwrap_readonly(receiver_type))
        if record is None or not record.type_params:
            return
        raise_if_class_param_bound_violated(
            method, record.type_params, class_subst,
            self.protocols.type_conforms_to_protocol,
            self.ctx.error, loc_node,
        )

    def _resolve_pending_types(self, tpy_type: TpyType) -> TpyType:
        """Resolve pending/structural types (PendingListType, TypeParamRef, LiteralType).

        Preserves IntLiteralType for flexible overload matching.
        """
        if isinstance(tpy_type, TypeParamRef) and tpy_type.kind == TypeParamKind.INT:
            return INT32
        if isinstance(tpy_type, PendingListType):
            return make_list(tpy_type.element_type)
        if isinstance(tpy_type, LiteralType):
            return tpy_type.base_type
        return tpy_type

    def get_effective_type_for_binop(self, tpy_type: TpyType) -> TpyType:
        """Fully resolve type for registry lookup (also resolves IntLiteralType/FloatLiteralType)."""
        tpy_type = unwrap_ref_type(tpy_type)
        if isinstance(tpy_type, OwnType):
            tpy_type = tpy_type.wrapped
        if isinstance(tpy_type, IntLiteralType):
            return self.ctx.default_int_for_literal(tpy_type)
        if isinstance(tpy_type, FloatLiteralType):
            return FLOAT
        if isinstance(tpy_type, LiteralType):
            return tpy_type.base_type
        return self._resolve_pending_types(tpy_type)

    def _build_type_subst(
        self, receiver_type: TpyType, arg_effective: TpyType,
    ) -> dict[str, TpyType]:
        """Build type param substitution from a concrete receiver type.

        When the receiver has IntLiteralType element(s), adapts to the arg's
        concrete element type if available, otherwise resolves to default int.
        """
        # Drop int-kind type args (e.g. N in Array[T, N]) --
        # _substitute_type_params only acts on TypeParamRef -> TpyType.
        subst: dict[str, TpyType] = {
            k: v for k, v in self.type_ops.build_type_substitution(receiver_type).items()
            if isinstance(v, TpyType)
        }
        if not subst:
            return {}
        arg_params = builtin_modules.extract_type_params(arg_effective)
        for name, typ in list(subst.items()):
            if isinstance(typ, (IntLiteralType, FloatLiteralType)):
                arg_val = arg_params.get(name)
                if arg_val is not None and not isinstance(arg_val, (IntLiteralType, FloatLiteralType)):
                    subst[name] = arg_val
                else:
                    subst[name] = resolve_int_literals(typ, self.ctx.default_int_for_literal)
            else:
                subst[name] = resolve_int_literals(typ, self.ctx.default_int_for_literal)
        return subst

    def _find_matching_overload(
        self, overloads: list[FunctionInfo], arg_type: TpyType,
        type_subst: dict[str, TpyType],
        protocol_checker: ProtocolChecker | None = None,
    ) -> FunctionInfo | None:
        """Find an overload matching arg_type, substituting type params first."""
        for method in overloads:
            if len(method.params) == 1:
                _, param_type = method.params[0]
                param_type = unwrap_ref_type(param_type)
                if type_subst:
                    param_type = _substitute_type_params(param_type, type_subst)
                if (type_matches_strict(arg_type, param_type, protocol_checker)
                        or type_matches_numeric(arg_type, param_type)):
                    return method
        return None

    def _make_resolved(
        self, method: FunctionInfo, type_subst: dict[str, TpyType],
        receiver_type: TpyType, loc_node, left_wrapper: str = "{expr}",
        right_wrapper: str = "{expr}", is_reverse: bool = False,
        promotion: FunctionInfo | None = None,
    ) -> ResolvedBinop:
        """Create ResolvedBinop with type params substituted in method signature."""
        # Bound check before substitution rewrites `method` (bounds are on the
        # original FunctionInfo and indexed by class-shadow names). Raises on
        # violation -- the resolver never returns a result carrying one.
        self._check_class_bounds(method, receiver_type, type_subst, loc_node)
        if type_subst:
            new_params = [
                dc_replace(p, type=_substitute_type_params(p.type, type_subst))
                for p in method.params
            ]
            new_return = _substitute_type_params(method.return_type, type_subst)
            method = dc_replace(method, params=new_params, return_type=new_return,
                                canonical_fi=method.root)
        # Unwrap Ref from return type -- Ref is a codegen-level concern,
        # sema expression types should not carry it.
        ret = unwrap_ref_type(method.return_type)
        if ret is not method.return_type:
            method = dc_replace(method, return_type=ret, canonical_fi=method.root)
        if type_subst:
            # Rebuild receiver_type from subst to resolve IntLiteralType elements
            # (a literal receiver like [10, 20] carries IntLiteralType inner types
            # that must not leak into codegen's receiver render).
            # Use inner_types() as the source of truth for the reconstruction -- it
            # defines exactly how many (and which) inner types the type has. params_map
            # may have more entries (e.g. Tspan for PtrType), but with_inner_types only
            # accepts len(inner_types()) values.
            inner = receiver_type.inner_types()
            if inner:
                params_map = builtin_modules.extract_type_params(receiver_type)
                param_names = list(params_map.keys())
                new_inner = tuple(
                    type_subst.get(param_names[i], inner[i]) if i < len(param_names) else inner[i]
                    for i in range(len(inner))
                )
                receiver_type = receiver_type.with_inner_types(new_inner)
        return ResolvedBinop(
            method=method,
            left_wrapper=left_wrapper,
            right_wrapper=right_wrapper,
            is_reverse=is_reverse,
            receiver_type=receiver_type,
            promotion=promotion,
        )

    def resolve_binop(
        self, left_type: TpyType, op: str, right_type: TpyType,
        loc_node=None,
    ) -> ResolvedBinop | None:
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

        # Build type substitution maps for generic types (e.g. list[T] -> list[int32])
        left_subst = self._build_type_subst(left_effective, right_effective)
        right_subst = self._build_type_subst(right_effective, left_effective)

        # For overload matching, resolve structural types but preserve
        # IntLiteralType flexibility (matches any numeric type)
        left_arg = unwrap_ref_type(self._resolve_pending_types(left_type))
        right_arg = unwrap_ref_type(self._resolve_pending_types(right_type))

        # Lets builtin dunders take marker-protocol params (e.g. float.__add__
        # over AnyFixedInt covers every fixed-width int in one overload).
        pc = self.protocols.type_conforms_to_protocol

        # 1. Try direct: left.__add__(right)
        if left_record:
            overloads = left_record.get_method_overloads(method_name)
            if method := self._find_matching_overload(overloads, right_arg, left_subst, pc):
                return self._make_resolved(method, left_subst, left_effective, loc_node)

        # 2. Try promoting left to right's type via __int__
        if left_record and right_record:
            int_overloads = left_record.get_method_overloads("__int__")
            if int_overloads:
                int_method = int_overloads[0]
                promoted_type = int_method.return_type
                # Check if promoted type matches right's type
                if self.ctx.registry.get_record_for_type(promoted_type) == right_record:
                    right_overloads = right_record.get_method_overloads(method_name)
                    if method := self._find_matching_overload(right_overloads, right_arg, right_subst, pc):
                        return self._make_resolved(
                            method, right_subst, right_effective, loc_node,
                            left_wrapper=int_method.cpp_template or "{expr}",
                            promotion=int_method,
                        )

        # 3. Try reverse: right.__radd__(left)
        if right_record and rmethod_name:
            overloads = right_record.get_method_overloads(rmethod_name)
            if method := self._find_matching_overload(overloads, left_arg, right_subst, pc):
                return self._make_resolved(
                    method, right_subst, right_effective, loc_node, is_reverse=True,
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
                    if method := self._find_matching_overload(left_overloads, promoted_type, left_subst, pc):
                        return self._make_resolved(
                            method, left_subst, left_effective, loc_node,
                            right_wrapper=int_method.cpp_template or "{expr}",
                            promotion=int_method,
                        )

        return None

    def resolve_aug_inplace(
        self, target_type: TpyType, op: str, value_type: TpyType,
        loc_node=None,
    ) -> ResolvedBinop | None:
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
        value_arg = unwrap_ref_type(self._resolve_pending_types(value_type))
        pc = self.protocols.type_conforms_to_protocol

        overloads = record.get_method_overloads(method_name)
        # Try builtin methods with cpp_template first
        builtin_overloads = [m for m in overloads if m.cpp_template]
        if method := self._find_matching_overload(builtin_overloads, value_arg, type_subst, pc):
            return self._make_resolved(method, type_subst, target_effective, loc_node)

        # Then try user-defined methods (no cpp_template)
        user_overloads = [m for m in overloads if not m.cpp_template]
        if method := self._find_matching_overload(user_overloads, value_arg, type_subst, pc):
            return self._make_resolved(method, type_subst, target_effective, loc_node)

        return None

    def get_aug_inplace_param_type(
        self, target_type: TpyType, op: str,
    ) -> TpyType | None:
        """Return the expected param type for an in-place operator, or None if not defined.

        Used to produce precise type mismatch errors when resolve_aug_inplace fails:
        the caller can pass the returned type to check_type_compatible to get a
        specific "expected X, got Y" error instead of a generic "not supported" error.
        """
        method_name = builtin_modules.AUGOP_TO_IMETHOD.get(op)
        if not method_name:
            return None

        target_effective = self.get_effective_type_for_binop(target_type)
        record = self.ctx.registry.get_record_for_type(target_effective)
        if not record:
            return None

        overloads = record.get_method_overloads(method_name)
        candidates = [m for m in overloads if m.params]
        if not candidates:
            return None

        type_subst = self._build_type_subst(target_effective, target_effective)
        _, param_type = candidates[0].params[0]
        if type_subst:
            param_type = _substitute_type_params(param_type, type_subst)
        return param_type

    def resolve_unaryop(
        self, operand_type: TpyType, op: str, loc_node=None,
    ) -> ResolvedUnaryop | None:
        """Resolve unary operator using registry."""
        method_name = builtin_modules.UNARYOP_TO_METHOD.get(op)
        if not method_name:
            return None

        effective_type = self.get_effective_type_for_binop(operand_type)
        record = self.ctx.registry.get_record_for_type(effective_type)
        if record:
            overloads = record.get_method_overloads(method_name)
            if overloads and len(overloads[0].params) == 0:
                method = overloads[0]
                class_subst = self._build_type_subst(effective_type, effective_type)
                self._check_class_bounds(method, effective_type, class_subst, loc_node)
                return ResolvedUnaryop(method=method)

        return None
