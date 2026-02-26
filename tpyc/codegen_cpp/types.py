"""
TurboPython Code Generation Type Helpers

Type resolution and C++ type mapping utilities.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, FixedIntType, BigIntType, IntLiteralType, FloatType,
    PendingListType, PendingStrType, ListType, ArrayType, TypeParamRef, NamedType,
    UnionType, NoneType, VoidType, EnumType,
    unwrap_readonly, is_protocol_type,
    INT32, BIGINT, FLOAT, STR,
    _union_alias_names
)
from ..parse import TpyExpr, TpyName, TpyBinOp, TpyUnaryOp, TpyCoerce, TpyCall, TpyMethodCall, TpyIntLiteral
from .context import qualified_cpp_name

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .protocols import ProtocolGenerator


class TypeResolver:
    """Type resolution and C++ type mapping utilities."""

    def __init__(self, ctx: CodeGenContext):
        self.ctx = ctx
        # Will be set after protocols is created
        self.protocols: ProtocolGenerator | None = None

    def set_protocols(self, protocols: ProtocolGenerator):
        """Set protocols generator (created after TypeResolver)."""
        self.protocols = protocols

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
            # Comparisons always produce bool -- skip arithmetic type logic
            if expr.op in ("==", "!=", "<", ">", "<=", ">="):
                typ = self.ctx.analyzer.get_expr_type(expr)
                return unwrap_readonly(typ) if typ else typ

            # First pass without context to detect Int32 operands
            left_raw = self.get_resolved_type(expr.left)
            right_raw = self.get_resolved_type(expr.right)
            left_analyzer_type = self.ctx.analyzer.get_expr_type(expr.left)
            right_analyzer_type = self.ctx.analyzer.get_expr_type(expr.right)
            left_is_literal = isinstance(left_analyzer_type, IntLiteralType) and not isinstance(expr.left, TpyName)
            right_is_literal = isinstance(right_analyzer_type, IntLiteralType) and not isinstance(expr.right, TpyName)

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

            # Determine fixed-int context: explicit target or operand is a FixedIntType
            fixed_ctx = target_type if isinstance(target_type, FixedIntType) else None
            if isinstance(left_raw, FixedIntType) and not left_is_literal:
                fixed_ctx = left_raw
            elif isinstance(right_raw, FixedIntType) and not right_is_literal:
                fixed_ctx = right_raw
            # Second pass with context for proper literal resolution
            left_type = self.get_resolved_type(expr.left, fixed_ctx)
            right_type = self.get_resolved_type(expr.right, fixed_ctx)
            # If target is fixed-int and both operands are literals, result is that type
            if isinstance(fixed_ctx, FixedIntType) and left_is_literal and right_is_literal:
                return fixed_ctx
            # Pure literal binops without fixed context use configured default-int,
            # with range-safe fallback for out-of-range results.
            if left_is_literal and right_is_literal:
                analyzed = self.ctx.analyzer.get_expr_type(expr)
                if isinstance(analyzed, IntLiteralType):
                    return self.ctx.analyzer.ctx.default_int_for_literal(analyzed)
                return self.ctx.analyzer.ctx.default_int_type
            # If either operand is FixedIntType (and other is compatible), result is that type
            if isinstance(left_type, FixedIntType) and isinstance(right_type, (FixedIntType, IntLiteralType)):
                return left_type
            if isinstance(right_type, FixedIntType) and isinstance(left_type, (FixedIntType, IntLiteralType)):
                return right_type
            # Otherwise, result is BigInt if either operand is BigInt.
            # For literal-literal arithmetic without stronger context, use the
            # configured default integer type.
            is_bigint_op = (
                isinstance(left_type, BigIntType) or
                isinstance(right_type, BigIntType)
            )
            if is_bigint_op and expr.op in ("+", "-", "*", "//", "%", "**", "&", "|", "^", "<<", ">>"):
                return BIGINT
            if (
                isinstance(left_type, IntLiteralType)
                and isinstance(right_type, IntLiteralType)
                and expr.op in ("+", "-", "*", "//", "%", "**", "&", "|", "^", "<<", ">>")
            ):
                analyzed = self.ctx.analyzer.get_expr_type(expr)
                if isinstance(analyzed, IntLiteralType):
                    return self.ctx.analyzer.ctx.default_int_for_literal(analyzed)
                return self.ctx.analyzer.ctx.default_int_type

        typ = self.ctx.analyzer.get_expr_type(expr)
        # Strip ReadonlyType -- C++ doesn't use it
        typ = unwrap_readonly(typ) if typ else typ
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
                elem_type = self.ctx.analyzer.ctx.default_int_for_literal(elem_type)
            return ListType(elem_type)
        if isinstance(typ, PendingStrType):
            return self._resolve_pending_str(typ)
        # Resolve IntLiteralType based on context (FixedInt if target, else
        # configured default int type).
        if isinstance(typ, IntLiteralType):
            if isinstance(target_type, FixedIntType):
                return target_type
            return self.ctx.analyzer.ctx.default_int_for_literal(typ)
        # Resolve IntLiteralType in container element types
        if isinstance(typ, ListType) and isinstance(typ.element_type, IntLiteralType):
            elem = self.ctx.analyzer.ctx.default_int_for_literal(typ.element_type)
            return ListType(elem)
        return typ

    def _resolve_pending_str(self, typ: PendingStrType) -> TpyType:
        """Resolve a PendingStrType to its concrete type (STR or STRVIEW)."""
        sv_info = self.ctx.analyzer.ctx.str_vars.get(typ.str_var_id)
        return sv_info.resolved_type if sv_info and sv_info.resolved_type else STR

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

    def is_fixed_int_arithmetic(self, left_type: TpyType, right_type: TpyType, op: str) -> bool:
        """Check if binary op produces fixed-int result (needs checked arithmetic).

        Only applies when at least one operand is explicitly a FixedIntType.
        IntLiteralType alone uses the configured default integer type.
        """
        if op not in ("+", "-", "*", "//", "%", "**"):
            return False
        has_fixed = isinstance(left_type, FixedIntType) or isinstance(right_type, FixedIntType)
        if not has_fixed:
            return False
        def is_fixed_compatible(t: TpyType) -> bool:
            return isinstance(t, (FixedIntType, IntLiteralType))
        return is_fixed_compatible(left_type) and is_fixed_compatible(right_type)

    def is_runtime_bigint(self, expr: TpyExpr, expr_type: TpyType) -> bool:
        """Check if expression is stored as BigInt at runtime."""
        resolved = self.get_resolved_type(expr)
        if isinstance(resolved, BigIntType):
            return True
        if isinstance(resolved, IntLiteralType):
            resolved_default = self.ctx.analyzer.ctx.default_int_for_literal(resolved)
            return isinstance(resolved_default, BigIntType)
        return False

    def type_to_cpp(self, typ: TpyType) -> str:
        """Convert a type to its C++ representation, qualifying imported types.

        For imported record types from user modules, generates fully qualified names
        like tpy_user::utils::Point or tpy_user::pkg::mod::Point for packages.
        Native records use their native C++ name directly (no namespace qualification).
        @dynamic protocol types map to __tpy_Base_{Name}.
        """
        if is_protocol_type(typ):
            protocol_info = self.ctx.analyzer.registry.get_protocol(typ.name)
            if protocol_info and protocol_info.is_dynamic:
                return self.protocols.get_dynamic_base_name(typ.name)
        if isinstance(typ, NamedType) and typ.is_user_record:
            # Native records use their native C++ name directly (globally visible)
            record_info = self.ctx.analyzer.registry.get_record_for_type(typ)
            if record_info and record_info.is_native:
                return typ.to_cpp()  # to_cpp() already resolves via _native_cpp_names
            # Check if this record is imported from a user module
            if typ.name in self.ctx.user_imported_records:
                source_module, original_name = self.ctx.user_imported_records[typ.name]
                qualified = qualified_cpp_name(source_module, original_name)
                if typ.type_args:
                    args = ", ".join(
                        self.type_to_cpp(t) if isinstance(t, TpyType) else str(t)
                        for t in typ.type_args
                    )
                    return f"{qualified}<{args}>"
                return qualified
        # Imported enum types: qualify with source module namespace
        if isinstance(typ, EnumType):
            if typ.name in self.ctx.user_imported_enums:
                source_module, original_name = self.ctx.user_imported_enums[typ.name]
                return qualified_cpp_name(source_module, original_name)
        # Union types: use alias name if registered, otherwise qualify member names
        if isinstance(typ, UnionType):
            alias = _union_alias_names.get(typ.members)
            if alias is not None:
                return alias
            cpp_members = [
                "std::monostate" if isinstance(m, (NoneType, VoidType)) else self.type_to_cpp(m)
                for m in typ.members
            ]
            return f"std::variant<{', '.join(cpp_members)}>"
        # Resolve PendingStrType to its concrete type before codegen
        if isinstance(typ, PendingStrType):
            return self._resolve_pending_str(typ).to_cpp()
        # Default: use the type's built-in to_cpp() method
        return typ.to_cpp()
