"""
TurboPython Operator Resolution

Binary and unary operator resolution using the type registry.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, TypeParamRef,
    ResolvedBinop, ResolvedUnaryop, FunctionInfo, TypeParamKind,
    INT32, BIGINT
)
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
    "__neg__": "-({self})",
    "__invert__": "~({self})",
    # Conversion methods (used for operator promotion)
    "__int__": "({self}).__int__()",
}


class OperatorResolver:
    """Resolves binary and unary operators using the type registry."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def get_effective_type_for_binop(self, tpy_type: TpyType) -> TpyType:
        """Get the effective type for binop resolution, treating IntLiteralType as BigInt."""
        if isinstance(tpy_type, IntLiteralType):
            return BIGINT
        # INT TypeParamRef (e.g., N: int) treated as Int32 for arithmetic
        if isinstance(tpy_type, TypeParamRef) and tpy_type.kind == TypeParamKind.INT:
            return INT32
        return tpy_type

    def binop_type_matches(self, arg_type: TpyType, param_type: TpyType) -> bool:
        """Check if an argument type matches a parameter type for binop resolution."""
        if arg_type == param_type:
            return True
        # IntLiteralType can match Int32 or BigInt
        if isinstance(arg_type, IntLiteralType):
            return isinstance(param_type, (Int32Type, BigIntType, IntLiteralType))
        # INT TypeParamRef can match Int32 or BigInt
        if isinstance(arg_type, TypeParamRef) and arg_type.kind == TypeParamKind.INT:
            return isinstance(param_type, (Int32Type, BigIntType))
        return False

    def find_binop_overload(self, overloads: list[FunctionInfo], arg_type: TpyType) -> FunctionInfo | None:
        """Find an overload that accepts the given argument type."""
        for method in overloads:
            if len(method.params) == 1:
                _, param_type = method.params[0]
                if self.binop_type_matches(arg_type, param_type):
                    return method
        return None

    def resolve_binop(self, left_type: TpyType, op: str, right_type: TpyType) -> ResolvedBinop | None:
        """Resolve binary operator using registry.

        Handles both builtin types and user-defined types with dunder methods.
        User-defined methods have cpp_template set to C++ operator syntax.

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

        # Get effective types (IntLiteralType -> BigInt)
        left_effective = self.get_effective_type_for_binop(left_type)
        right_effective = self.get_effective_type_for_binop(right_type)

        left_record = self.ctx.registry.get_record_for_type(left_effective)
        right_record = self.ctx.registry.get_record_for_type(right_effective)

        # 1. Try direct: left.__add__(right)
        if left_record:
            overloads = left_record.get_method_overloads(method_name)
            if method := self.find_binop_overload(overloads, right_type):
                return ResolvedBinop(
                    method=method,
                    left_wrapper="{expr}",
                    right_wrapper="{expr}",
                    receiver_type=left_effective
                )

        # 2. Try promoting left to right's type via __int__
        if left_record and right_record:
            int_overloads = left_record.get_method_overloads("__int__")
            if int_overloads:
                int_method = int_overloads[0]
                promoted_type = int_method.return_type
                # Check if promoted type matches right's type
                if self.ctx.registry.get_record_for_type(promoted_type) == right_record:
                    right_overloads = right_record.get_method_overloads(method_name)
                    if method := self.find_binop_overload(right_overloads, right_type):
                        return ResolvedBinop(
                            method=method,
                            left_wrapper=int_method.cpp_template or "{expr}",
                            right_wrapper="{expr}",
                            receiver_type=right_effective
                        )

        # 3. Try reverse: right.__radd__(left)
        if right_record and rmethod_name:
            overloads = right_record.get_method_overloads(rmethod_name)
            if method := self.find_binop_overload(overloads, left_type):
                return ResolvedBinop(
                    method=method,
                    left_wrapper="{expr}",
                    right_wrapper="{expr}",
                    is_reverse=True,
                    receiver_type=right_effective
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
                    if method := self.find_binop_overload(left_overloads, promoted_type):
                        return ResolvedBinop(
                            method=method,
                            left_wrapper="{expr}",
                            right_wrapper=int_method.cpp_template or "{expr}",
                            receiver_type=left_effective
                        )

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
