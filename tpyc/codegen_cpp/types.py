"""
TurboPython Code Generation Type Helpers

Type resolution and C++ type mapping utilities.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, BigIntType, IntLiteralType, FloatType,
    PendingListType, ListType, ArrayType, TypeParamRef, NamedType,
    INT32, BIGINT, FLOAT
)
from ..parse import TpyExpr, TpyName, TpyBinOp, TpyUnaryOp, TpyCoerce, TpyCall, TpyMethodCall, TpyIntLiteral

if TYPE_CHECKING:
    from .context import CodeGenContext


class TypeResolver:
    """Type resolution and C++ type mapping utilities."""

    def __init__(self, ctx: CodeGenContext):
        self.ctx = ctx

    def get_resolved_type(self, expr: TpyExpr, target_type: TpyType | None = None) -> TpyType:
        """Get the resolved type of an expression, handling PendingListType.

        PendingListType is used during semantic analysis but should be resolved
        to concrete Array or list types before codegen. This method looks up
        the resolved type if needed.

        Args:
            expr: The expression to get the type of.
            target_type: Optional hint for what type the expression will be coerced to.
                         Used to determine if literal+literal should be Int32 or BigInt.
        """
        # Check for codegen-overridden types (e.g., loop variables)
        if isinstance(expr, TpyName) and expr.name in self.ctx.var_types:
            return self.ctx.var_types[expr.name]
        if isinstance(expr, TpyCoerce):
            return expr.expected_type

        # For binary operations, compute type using resolved operand types
        if isinstance(expr, TpyBinOp):
            # First pass without context to detect Int32 operands
            left_raw = self.get_resolved_type(expr.left)
            right_raw = self.get_resolved_type(expr.right)

            # If either operand is float, result is float (float takes precedence)
            if isinstance(left_raw, FloatType) or isinstance(right_raw, FloatType):
                # True division always returns float
                if expr.op == "div":
                    return FLOAT
                # Most arithmetic ops with float return float
                if expr.op in ("+", "-", "*", "//", "%", "**"):
                    return FLOAT

            # True division always returns float
            if expr.op == "div":
                return FLOAT

            # Determine Int32 context: explicit target or operand is Int32
            int32_ctx = target_type if isinstance(target_type, Int32Type) else None
            if isinstance(left_raw, Int32Type) or isinstance(right_raw, Int32Type):
                int32_ctx = INT32
            # Second pass with context for proper literal resolution
            left_type = self.get_resolved_type(expr.left, int32_ctx)
            right_type = self.get_resolved_type(expr.right, int32_ctx)
            # Use analyzer types for literal check - analyzer returns IntLiteralType for
            # all-literal expressions (including nested binops like 2+3)
            # NOTE: Variables (TpyName) may have IntLiteralType but aren't actual literals
            left_analyzer_type = self.ctx.analyzer.get_expr_type(expr.left)
            right_analyzer_type = self.ctx.analyzer.get_expr_type(expr.right)
            left_is_literal = isinstance(left_analyzer_type, IntLiteralType) and not isinstance(expr.left, TpyName)
            right_is_literal = isinstance(right_analyzer_type, IntLiteralType) and not isinstance(expr.right, TpyName)
            # If target is Int32 and both operands are literals, result is Int32
            if isinstance(int32_ctx, Int32Type) and left_is_literal and right_is_literal:
                return INT32
            # If either operand is Int32 (and other is compatible), result is Int32
            if isinstance(left_type, Int32Type) and isinstance(right_type, (Int32Type, IntLiteralType)):
                return INT32
            if isinstance(right_type, Int32Type) and isinstance(left_type, (Int32Type, IntLiteralType)):
                return INT32
            # Otherwise, result is BigInt if either operand is BigInt, or if both are IntLiteral
            is_bigint_op = (
                isinstance(left_type, BigIntType) or
                isinstance(right_type, BigIntType) or
                (isinstance(left_type, IntLiteralType) and isinstance(right_type, IntLiteralType))
            )
            if is_bigint_op and expr.op in ("+", "-", "*", "//", "%", "**", "&", "|", "^", "<<", ">>"):
                return BIGINT

        typ = self.ctx.analyzer.get_expr_type(expr)
        if isinstance(typ, PendingListType):
            # Look up the resolved type from the literal info
            literal_id = typ.literal_id
            if literal_id in self.ctx.analyzer.list_literals:
                info = self.ctx.analyzer.list_literals[literal_id]
                if info.resolved_type:
                    return info.resolved_type
            # Fallback: treat as ListType with resolved element type
            elem_type = typ.element_type
            if isinstance(elem_type, IntLiteralType):
                elem_type = INT32
            return ListType(elem_type)
        # Resolve IntLiteralType based on context (Int32 if target, else BigInt)
        if isinstance(typ, IntLiteralType):
            if isinstance(target_type, Int32Type):
                return INT32
            return BIGINT
        # Resolve IntLiteralType in container element types
        if isinstance(typ, ListType) and isinstance(typ.element_type, IntLiteralType):
            return ListType(BIGINT)
        return typ

    def substitute_type_params(self, typ: TpyType, subst: dict[str, TpyType]) -> TpyType:
        """Substitute type parameters with concrete types for codegen.

        This is simpler than the sema version - just applies the substitution.
        """
        if isinstance(typ, TypeParamRef):
            return subst.get(typ.name, typ)
        return typ.map_inner_types(lambda t: self.substitute_type_params(t, subst))

    def involves_variables(self, expr: TpyExpr) -> bool:
        """Check if an expression involves any variable references."""
        if isinstance(expr, TpyCoerce):
            return self.involves_variables(expr.expr)
        if isinstance(expr, TpyName):
            return True
        if isinstance(expr, TpyIntLiteral):
            return False
        if isinstance(expr, TpyBinOp):
            return self.involves_variables(expr.left) or self.involves_variables(expr.right)
        if isinstance(expr, TpyUnaryOp):
            return self.involves_variables(expr.operand)
        if isinstance(expr, TpyCall):
            return True  # Function calls may return BigInt
        if isinstance(expr, TpyMethodCall):
            return True
        # Default to True for safety
        return True

    def is_int32_arithmetic(self, left_type: TpyType, right_type: TpyType, op: str) -> bool:
        """Check if binary op produces Int32 result (needs checked arithmetic).

        Only applies when at least one operand is explicitly Int32Type.
        IntLiteralType alone defaults to BigInt (Python semantics).
        """
        if op not in ("+", "-", "*", "//", "%", "**"):
            return False
        # Need at least one explicit Int32 operand
        has_int32 = isinstance(left_type, Int32Type) or isinstance(right_type, Int32Type)
        if not has_int32:
            return False
        # The other operand must be Int32 or IntLiteral (coerces to Int32)
        def is_int32_compatible(t: TpyType) -> bool:
            return isinstance(t, (Int32Type, IntLiteralType))
        return is_int32_compatible(left_type) and is_int32_compatible(right_type)

    def is_runtime_bigint(self, expr: TpyExpr, expr_type: TpyType) -> bool:
        """Check if expression is stored as BigInt at runtime."""
        if isinstance(expr_type, BigIntType):
            return True
        if isinstance(expr_type, IntLiteralType):
            return self.involves_variables(expr)
        return False

    def type_to_cpp(self, typ: TpyType) -> str:
        """Convert a type to its C++ representation, qualifying imported types.

        For imported record types from user modules, generates fully qualified names
        like tpy_user::utils::Point or tpy_user::pkg::mod::Point for packages.
        """
        if isinstance(typ, NamedType) and typ.is_record:
            # Check if this record is imported from a user module
            if typ.name in self.ctx.user_imported_records:
                source_module, original_name = self.ctx.user_imported_records[typ.name]
                cpp_ns = source_module.replace('.', '::')
                if typ.type_args:
                    args = ", ".join(
                        self.type_to_cpp(t) if isinstance(t, TpyType) else str(t)
                        for t in typ.type_args
                    )
                    return f"tpy_user::{cpp_ns}::{original_name}<{args}>"
                return f"tpy_user::{cpp_ns}::{original_name}"
        # Default: use the type's built-in to_cpp() method
        return typ.to_cpp()
