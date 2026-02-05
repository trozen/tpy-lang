"""
TurboPython Type Compatibility

Type compatibility checking, coercions, and lvalue analysis.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional

from ..typesys import (
    TpyType, IntLiteralType, BigIntType, Int32Type, ArrayType, ListType,
    PendingListType, SpanType, StrType, OwnType, VoidType, PtrType, ConstPtrType,
    NamedType
)
from ..parse import (
    TpyExpr, TpyName, TpyFieldAccess, TpySubscript, TpyArrayLiteral,
    TpyListRepeat, TpyCall, TpyMethodCall, TpyUnaryOp, TpyBinOp, TpyCoerce,
    TpyIntLiteral, SourceLocation
)
from ..coercions import resolve_coercion, Coercion, CoercionContext
from .diagnostics import SemanticError

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker


class TypeCompatibility:
    """Type compatibility checking, coercions, and lvalue analysis."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx
        # These are set later to break circular dependencies
        self.type_ops: TypeOperations | None = None
        self.protocols: ProtocolChecker | None = None

    def check_type_compatible(
        self, actual: TpyType, expected: TpyType, context: str,
        loc: SourceLocation | None = None,
        source_expr: TpyExpr | None = None,
        is_return: bool = False,
        coercion_ctx: str | None = None
    ) -> Optional[Coercion]:
        """Check if actual type is compatible with expected type.

        Returns a Coercion if a conversion should be applied at codegen time.

        Args:
            source_expr: The expression being converted (for lvalue checks)
            is_return: True if this is a return statement (affects lifetime checks)
        """
        if actual == expected:
            return None

        # Protocol matching (structural subtyping)
        if isinstance(expected, NamedType) and expected.is_protocol:
            if self.protocols and self.protocols.type_conforms_to_protocol(actual, expected):
                return None  # No coercion needed, structural match
            raise SemanticError(
                f"Type {actual} does not conform to protocol {expected} in {context}",
                loc
            )

        # Allow T -> Own[T] coercion (ownership transfer for return values)
        if isinstance(expected, OwnType):
            return self.check_type_compatible(actual, expected.wrapped, context, loc, source_expr, is_return, coercion_ctx)

        # Allow Own[T] -> T coercion (receiving an owned value)
        if isinstance(actual, OwnType):
            return self.check_type_compatible(actual.wrapped, expected, context, loc, source_expr, is_return, coercion_ctx)

        # IntLiteral can coerce to BigInt or stay unresolved
        if isinstance(actual, IntLiteralType):
            if isinstance(expected, (BigIntType, IntLiteralType)):
                return None

        # Allow Array element type coercion if sizes match
        if isinstance(actual, ArrayType) and isinstance(expected, ArrayType):
            if actual.size == expected.size:
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return None

        # Allow list element type coercion
        if isinstance(actual, ListType) and isinstance(expected, ListType):
            if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                return None

        # Allow ListType -> ArrayType only for literal expressions
        # (global array literals and list repeats become ListType but can be assigned to Array variables)
        # List *variables* cannot be coerced to Array - codegen can't handle std::vector -> std::array
        if isinstance(actual, ListType) and isinstance(expected, ArrayType):
            if isinstance(source_expr, (TpyArrayLiteral, TpyListRepeat)):
                if actual.element_type == expected.element_type:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return None

        # Allow PendingListType compatibility during first phase (before resolution)
        if isinstance(actual, PendingListType):
            # Compatible with list[T] if element types match
            if isinstance(expected, ListType):
                if actual.element_type == expected.element_type:
                    return None
                if isinstance(actual.element_type, IntLiteralType) and isinstance(expected.element_type, (Int32Type, BigIntType)):
                    return None
            # Compatible with Array[T, N] if element types and sizes match
            if isinstance(expected, ArrayType):
                if self.type_ops and self.type_ops.pending_list_matches_array(actual, expected):
                    return None

        ctx = coercion_ctx or context
        coercion = resolve_coercion(actual, expected, ctx)
        if coercion is None:
            raise SemanticError(f"Type mismatch in {context}: expected {expected}, got {actual}", loc)

        if isinstance(actual, PendingListType) and isinstance(expected, SpanType):
            info = self.ctx.list_literals.get(actual.literal_id)
            if info:
                info.coerced_element_type = expected.element_type
                info.passed_to_span_param = True

        if coercion.check_range and not coercion.check_range(actual, expected):
            INT32_MIN = -(2**31)
            INT32_MAX = 2**31 - 1
            raise SemanticError(
                f"Integer literal {actual.value} is outside Int32 range "
                f"[{INT32_MIN}, {INT32_MAX}] in {context}",
                loc
            )

        if coercion.requires_mutable:
            if source_expr is None or not self.is_mutable_lvalue(source_expr):
                raise SemanticError(
                    f"Cannot take mutable pointer to read-only or temporary value in {context}; "
                    f"use ConstPtr for read-only access, or assign to a variable first",
                    loc
                )
        elif coercion.requires_lvalue:
            if source_expr is None or not self.is_lvalue(source_expr):
                raise SemanticError(
                    f"Cannot take address of temporary or rvalue in {context}; "
                    f"assign to a variable first",
                    loc
                )

        if coercion.forbid_return_local and is_return:
            if source_expr is not None and self.is_dangling_return(source_expr):
                raise SemanticError(
                    f"Cannot return local or temporary value; "
                    f"the returned pointer/reference would dangle",
                    loc
                )

        return coercion

    def coerce_expr(
        self, expr: TpyExpr, actual: TpyType, expected: TpyType, context: str,
        coercion_ctx: str, is_return: bool = False
    ) -> TpyExpr:
        """Wrap expr in a coercion node if a conversion is needed."""
        coercion = self.check_type_compatible(
            actual, expected, context,
            getattr(expr, "loc", None),
            source_expr=expr,
            is_return=is_return,
            coercion_ctx=coercion_ctx
        )
        if coercion is None:
            return expr
        runtime_bigint = False
        if coercion.name == "int_literal_to_int32":
            runtime_bigint = self.is_runtime_bigint_expr(expr)
        coerced = TpyCoerce(
            expr=expr,
            actual_type=actual,
            expected_type=expected,
            coercion=coercion,
            context_kind=coercion_ctx,
            context_msg=context,
            runtime_bigint=runtime_bigint,
            loc=expr.loc
        )
        self.ctx.set_expr_type(coerced, expected)
        return coerced

    def is_runtime_bigint_expr(self, expr: TpyExpr) -> bool:
        """Check if an IntLiteralType expression could be BigInt at runtime."""
        if isinstance(expr, TpyCoerce):
            return self.is_runtime_bigint_expr(expr.expr)
        if isinstance(expr, TpyName):
            return True
        if isinstance(expr, TpyIntLiteral):
            return False
        if isinstance(expr, TpyBinOp):
            return self.is_runtime_bigint_expr(expr.left) or self.is_runtime_bigint_expr(expr.right)
        if isinstance(expr, TpyUnaryOp):
            return self.is_runtime_bigint_expr(expr.operand)
        if isinstance(expr, (TpyCall, TpyMethodCall)):
            return True
        return True

    def is_lvalue(self, expr: TpyExpr) -> bool:
        """Check if an expression is an lvalue (can have its address taken)."""
        if isinstance(expr, TpyCoerce):
            return self.is_lvalue(expr.expr)
        # Named variables are lvalues
        if isinstance(expr, TpyName):
            return True
        # Field access on an lvalue is also an lvalue (e.g., obj.field)
        if isinstance(expr, TpyFieldAccess):
            return self.is_lvalue(expr.obj)
        # Subscript on an lvalue is also an lvalue (e.g., arr[i])
        if isinstance(expr, TpySubscript):
            return self.is_lvalue(expr.obj)
        # Everything else (calls, literals, operators) are rvalues
        return False

    def is_copy_call(self, expr: TpyExpr) -> bool:
        """Check if expression is a copy() call from the tpy module."""
        if isinstance(expr, TpyCoerce):
            return self.is_copy_call(expr.expr)
        if not isinstance(expr, TpyCall):
            return False
        # Check if this function name maps to tpy.copy (handles aliases like "from tpy import copy as c")
        if expr.func in self.ctx.imported_names:
            module_name, func_name = self.ctx.imported_names[expr.func]
            return module_name == "tpy" and func_name == "copy"
        return False

    def is_mutable_lvalue(self, expr: TpyExpr) -> bool:
        """Check if an expression is a mutable lvalue (can get a mutable Ptr).

        This is like is_lvalue but also rejects read-only sources like Span elements.
        """
        if isinstance(expr, TpyCoerce):
            return self.is_mutable_lvalue(expr.expr)
        # Named variables are mutable lvalues
        if isinstance(expr, TpyName):
            return True
        # Field access on a mutable lvalue is also mutable
        if isinstance(expr, TpyFieldAccess):
            return self.is_mutable_lvalue(expr.obj)
        # Subscript: check if the base is a read-only type (Span, str)
        if isinstance(expr, TpySubscript):
            obj_type = self.ctx.get_expr_type(expr.obj)
            if isinstance(obj_type, (SpanType, StrType)):
                return False  # Span and str elements are read-only
            return self.is_mutable_lvalue(expr.obj)
        return False

    def is_dangling_return(self, expr: TpyExpr) -> bool:
        """Check if returning this expression would create a dangling reference."""
        if isinstance(expr, TpyCoerce):
            return self.is_dangling_return(expr.expr)
        # Array literal - creates temporary
        if isinstance(expr, TpyArrayLiteral):
            return True

        # List repeat - creates temporary
        if isinstance(expr, TpyListRepeat):
            return True

        # Constructor call - creates temporary
        if isinstance(expr, TpyCall):
            # Generic type constructor creates a temporary
            if expr.call_type is not None:
                return True

            # Record constructor
            if expr.func in self.ctx.registry.records:
                return True

            # Function returning Own[T] creates a temporary (by-value return)
            func = self.ctx.registry.get_function(expr.func)
            if func and isinstance(func.return_type, OwnType):
                return True

            # Regular function call - assume it returns something safe
            # (the callee is responsible for not returning dangling refs)
            return False

        # Local variable (not a parameter or global) - would dangle after function returns
        if isinstance(expr, TpyName):
            # 'self' in a method is safe - refers to the receiver object
            # (its lifetime is managed by the caller)
            if expr.name == "self":
                return False

            # Check if it's a parameter (safe)
            if self.ctx.current_function:
                for pname, ptype in self.ctx.current_function.params:
                    if pname == expr.name:
                        return False  # Parameter - safe to return reference

            # Check if it's a global (safe - lives forever)
            if expr.name in self.ctx.global_scope.bindings:
                return False

            # Local variable - dangling
            return True

        # Field access - safe only if the object itself is safe
        if isinstance(expr, TpyFieldAccess):
            return self.is_dangling_return(expr.obj)

        # Subscript - safe only if the container itself is safe
        if isinstance(expr, TpySubscript):
            return self.is_dangling_return(expr.obj)

        # Method call - assume safe (callee's responsibility)
        if isinstance(expr, TpyMethodCall):
            return False

        # Unary/Binary ops - might create temporaries, be conservative
        if isinstance(expr, (TpyUnaryOp, TpyBinOp)):
            return True

        # Default: assume safe
        return False

    def check_dangling_reference(self, expr: TpyExpr, return_type: TpyType, loc: SourceLocation | None) -> None:
        """Check if returning expr as a reference would be a dangling reference.

        Object types are returned by reference. Returning a local variable or
        newly constructed object would create a dangling reference.
        """
        # Only check object types (value types are returned by value)
        # Pointers are also value types (the pointer itself is copied)
        # OwnType returns by value (ownership transfer), so no dangling risk
        if return_type.is_value_type() or isinstance(return_type, (VoidType, PtrType, ConstPtrType, OwnType)):
            return

        # Check if the expression is safe to return as a reference
        if self.is_dangling_return(expr):
            raise self.ctx.error(
                f"Cannot return local or temporary as reference. "
                f"Object type '{return_type}' is returned by reference. "
                f"Use Own[{return_type}] to return by value.",
                expr
            )
