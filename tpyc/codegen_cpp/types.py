"""
TurboPython Code Generation Type Helpers

Type resolution and C++ type mapping utilities.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, Int32Type, FixedIntType, BigIntType, IntLiteralType, FloatType, Float32Type, FloatLiteralType, BoolType,
    PendingListType, PendingDictType, PendingSetType, PendingStrType, ListType, DictType, SetType, ArrayType, TypeParamRef, NamedType,
    UnionType, NoneType, VoidType, EnumType, TupleType,
    unwrap_readonly, is_protocol_type, resolve_int_literals,
    INT32, BIGINT, FLOAT, FLOAT32, STR,
    _union_alias_names
)
from ..parse import TpyExpr, TpyName, TpyBinOp, TpyUnaryOp, TpyCoerce, TpyCall, TpyMethodCall, TpyIntLiteral, TpyIfExpr
from ..sema.context import PENDING_CONTAINER_TYPES
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
            return unwrap_readonly(self.ctx.var_types[expr.name])
        if isinstance(expr, TpyCoerce):
            return expr.expected_type

        # Ternary: if both branches resolve to the same codegen type, use it
        # so deductions like string_view propagate through (e.g. a if c else b).
        if isinstance(expr, TpyIfExpr):
            typ = self.ctx.analyzer.get_expr_type(expr)
            then_resolved = self.get_resolved_type(expr.then_expr)
            else_resolved = self.get_resolved_type(expr.else_expr)
            if then_resolved == else_resolved:
                return then_resolved
            # Branches differ -- resolve PendingStrType so the string_view->string
            # wrapping check in statement codegen sees a concrete type.
            ret = unwrap_readonly(typ) if typ else typ
            if isinstance(ret, PendingStrType):
                ret = self._resolve_pending_str(ret)
            return ret

        # For binary operations, compute type using resolved operand types
        if isinstance(expr, TpyBinOp):
            # Comparisons: skip arithmetic type logic, use sema type directly.
            if expr.op in ("==", "!=", "<", ">", "<=", ">="):
                typ = self.ctx.analyzer.get_expr_type(expr)
                return unwrap_readonly(typ) if typ else typ
            # Logical and/or: for operand-return semantics, resolve via
            # operand types so codegen-level deductions (e.g. string_view)
            # propagate correctly. Bool-result falls through to sema type.
            if expr.op in ("&&", "||"):
                typ = self.ctx.analyzer.get_expr_type(expr)
                if isinstance(typ, BoolType):
                    return unwrap_readonly(typ) if typ else typ
                left_resolved = self.get_resolved_type(expr.left)
                right_resolved = self.get_resolved_type(expr.right)
                if left_resolved == right_resolved:
                    return left_resolved
                # Operands differ (e.g. one is string_view, other is string).
                # Resolve PendingStrType so callers like the string_view->string
                # wrapping check in statement codegen see a concrete type.
                ret = unwrap_readonly(typ) if typ else typ
                if isinstance(ret, PendingStrType):
                    ret = self._resolve_pending_str(ret)
                return ret

            # First pass without context to detect Int32 operands
            left_raw = self.get_resolved_type(expr.left)
            right_raw = self.get_resolved_type(expr.right)
            left_analyzer_type = self.ctx.analyzer.get_expr_type(expr.left)
            right_analyzer_type = self.ctx.analyzer.get_expr_type(expr.right)
            left_is_literal = isinstance(left_analyzer_type, IntLiteralType) and not isinstance(expr.left, TpyName)
            right_is_literal = isinstance(right_analyzer_type, IntLiteralType) and not isinstance(expr.right, TpyName)

            # If either operand is float-family, result is float (float takes precedence)
            if isinstance(left_raw, (FloatType, Float32Type)) or isinstance(right_raw, (FloatType, Float32Type)):
                if expr.op == "div" or expr.op in ("+", "-", "*", "//", "%", "**"):
                    # Float64 wins over Float32
                    if isinstance(left_raw, FloatType) or isinstance(right_raw, FloatType):
                        return FLOAT
                    return left_raw if isinstance(left_raw, Float32Type) else right_raw

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
        resolved = self._resolve_pending_container(typ)
        if resolved is not None:
            return resolved
        if isinstance(typ, PendingStrType):
            return self._resolve_pending_str(typ)
        # Resolve IntLiteralType based on context (FixedInt if target, else
        # configured default int type).
        if isinstance(typ, IntLiteralType):
            if isinstance(target_type, FixedIntType):
                return target_type
            return self.ctx.analyzer.ctx.default_int_for_literal(typ)
        # Resolve FloatLiteralType based on context (Float32 if target, else float64).
        if isinstance(typ, FloatLiteralType):
            if isinstance(target_type, Float32Type):
                return FLOAT32
            return FLOAT
        # Resolve IntLiteralType in container element types
        if isinstance(typ, ListType) and isinstance(typ.element_type, IntLiteralType):
            elem = self.ctx.analyzer.ctx.default_int_for_literal(typ.element_type)
            return ListType(elem)
        # Resolve IntLiteralType in tuple element types (recursively for nesting)
        if isinstance(typ, TupleType):
            resolve_lit = self.ctx.analyzer.ctx.default_int_for_literal
            resolved_elems = []
            changed = False
            for i, et in enumerate(typ.element_types):
                if isinstance(et, IntLiteralType):
                    # Use target_type's element if it's a FixedInt annotation
                    tt_elem = None
                    if isinstance(target_type, TupleType) and i < len(target_type.element_types):
                        tt_elem = target_type.element_types[i]
                    if isinstance(tt_elem, FixedIntType):
                        resolved_elems.append(tt_elem)
                    else:
                        resolved_elems.append(resolve_lit(et))
                    changed = True
                else:
                    resolved = resolve_int_literals(et, resolve_lit)
                    if resolved is not et:
                        changed = True
                    resolved_elems.append(resolved)
            if changed:
                return TupleType(tuple(resolved_elems))
        return typ

    def _resolve_pending_str(self, typ: PendingStrType) -> TpyType:
        """Resolve a PendingStrType to its concrete type (STR or STRVIEW)."""
        sv_info = self.ctx.analyzer.ctx.str_vars.get(typ.str_var_id)
        return sv_info.resolved_type if sv_info and sv_info.resolved_type else STR

    def _resolve_pending_container(self, typ: TpyType) -> TpyType | None:
        """Resolve a pending container type via unified lookup.

        Returns the resolved type, or None if not a pending container.
        Falls back to a best-effort concrete type if resolution hasn't run
        (e.g. list -> ListType with resolved element type).
        """
        if not isinstance(typ, PENDING_CONTAINER_TYPES):
            return None
        info = self.ctx.analyzer.ctx.get_container_info(typ.literal_id)
        if info and info.resolved_type:
            return info.resolved_type
        # Fallback for unresolved containers
        if isinstance(typ, PendingListType):
            elem_type = typ.element_type
            if isinstance(elem_type, IntLiteralType):
                elem_type = self.ctx.analyzer.ctx.default_int_for_literal(elem_type)
            return ListType(elem_type)
        if isinstance(typ, PendingDictType):
            return DictType(typ.key_type, typ.value_type)
        if isinstance(typ, PendingSetType):
            return SetType(typ.element_type)
        return None

    def resolve_type(self, typ: TpyType) -> TpyType:
        """Resolve deferred types (PendingStrType, PendingListType, etc.) to concrete C++ types."""
        if isinstance(typ, PendingStrType):
            return self._resolve_pending_str(typ)
        resolved = self._resolve_pending_container(typ)
        if resolved is not None:
            return resolved
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
        @dynamic protocol types map to the base class name.
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
        # Tuple types: qualify element types for imported members
        if isinstance(typ, TupleType):
            args = ", ".join(self.type_to_cpp(t) for t in typ.element_types)
            return f"std::tuple<{args}>"
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
        # For plain NamedType (not subclasses like ListType/ArrayType) with
        # type_args, recursively resolve args to handle @dynamic protocols
        if type(typ) is NamedType and typ.type_args:
            base = typ.to_cpp_base_name()
            args = ", ".join(
                self.type_to_cpp(t) if isinstance(t, TpyType) else str(t)
                for t in typ.type_args
            )
            return f"{base}<{args}>"
        # Default: use the type's built-in to_cpp() method
        return typ.to_cpp()

