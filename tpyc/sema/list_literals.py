"""
TurboPython List Literal Tracking

Tracks list literals and resolves their types (Array vs list) based on usage.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, ListType, ArrayType, PendingListType, SpanType, IntLiteralType,
    StrType, NamedType, BIGINT, CHAR, is_protocol_type,
)
from ..parse import TpyExpr, TpyName, TpyCoerce
from .diagnostics import SemanticError

if TYPE_CHECKING:
    from ..parse import SourceLocation
    from .context import SemanticContext

from tpyc import modules as builtin_modules


class ListLiteralTracker:
    """Tracks list literals and resolves their types based on usage."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def mark_list_mutated(self, obj_expr: TpyExpr) -> None:
        """Mark a list literal as mutated if it can be traced to one."""
        if isinstance(obj_expr, TpyName):
            var_name = obj_expr.name
            if var_name in self.ctx.variable_to_literal:
                literal_id = self.ctx.variable_to_literal[var_name]
                if literal_id in self.ctx.list_literals:
                    self.ctx.list_literals[literal_id].is_mutated = True

    def mark_list_param_context(self, arg_expr: TpyExpr, param_type: TpyType) -> None:
        """Track parameter context for list literal inference."""
        literal_id = None

        if isinstance(arg_expr, TpyCoerce):
            arg_expr = arg_expr.expr

        # Direct variable reference
        if isinstance(arg_expr, TpyName):
            var_name = arg_expr.name
            if var_name in self.ctx.variable_to_literal:
                literal_id = self.ctx.variable_to_literal[var_name]

        if literal_id is not None and literal_id in self.ctx.list_literals:
            info = self.ctx.list_literals[literal_id]
            if isinstance(param_type, ListType):
                info.passed_to_list_param = True
                info.coerced_element_type = param_type.element_type
            elif isinstance(param_type, SpanType):
                info.passed_to_span_param = True
                info.coerced_element_type = param_type.element_type

    def resolve_pending_list_types(self) -> None:
        """Resolve all pending list types after function analysis.

        Resolution rules (in priority order):
        1. Explicit annotation → use it
        2. is_mutated → ListType
        3. passed_to_list_param → ListType
        4. Otherwise → ArrayType

        Element type resolution:
        - If passed to typed param (list[T] or Span[T]), use T
        - IntLiteralType defaults to Int32 for containers
        """
        for literal_id in self.ctx.pending_resolutions:
            if literal_id not in self.ctx.list_literals:
                continue

            info = self.ctx.list_literals[literal_id]

            # Resolve element type
            # Priority: coerced type from param > resolved inner PendingListType > default
            elem_type = info.element_type

            # If element type is a PendingListType, look up its resolved type
            if isinstance(elem_type, PendingListType):
                inner_info = self.ctx.list_literals.get(elem_type.literal_id)
                if inner_info and inner_info.resolved_type:
                    elem_type = inner_info.resolved_type

            if isinstance(elem_type, IntLiteralType):
                if info.coerced_element_type is not None:
                    # Use element type from typed parameter (list[T] or Span[T])
                    elem_type = info.coerced_element_type
                else:
                    # Default to BigInt (Python semantics)
                    elem_type = BIGINT

            # Determine resolved type
            if info.has_explicit_annotation and info.explicit_type:
                resolved = info.explicit_type
            elif info.is_mutated:
                resolved = ListType(elem_type)
            elif info.passed_to_list_param:
                resolved = ListType(elem_type)
            elif info.is_global:
                # Globals can be imported and mutated by other modules
                resolved = ListType(elem_type)
            else:
                # Default: Array (stack-allocated, no mutation detected)
                resolved = ArrayType(elem_type, info.size)

            info.resolved_type = resolved

            # Update expr_types for the literal expression
            self.ctx.set_expr_type(info.expr, resolved)

            # Update scope binding if this literal was assigned to a variable
            if info.variable_name and self.ctx.current_scope:
                current_type = self.ctx.current_scope.lookup(info.variable_name)
                if isinstance(current_type, PendingListType):
                    self.ctx.current_scope.define(info.variable_name, resolved)

    def is_type_iterable(self, typ: TpyType) -> bool:
        """Check if a type is iterable (extends NativeIterable, NativeIterator, or is a protocol type).

        A type is iterable if it declares extends=["NativeIterable[T]"] or
        extends=["NativeIterator[T]"], or is itself a NativeIterable[T] or
        NativeIterator[T] protocol type.
        """
        # NativeIterable[T] or NativeIterator[T] protocol type
        if is_protocol_type(typ) and typ.name in ("NativeIterable", "NativeIterator"):
            return True
        # Check if type extends NativeIterable or NativeIterator
        if builtin_modules.type_extends_any(typ, "NativeIterable"):
            return True
        if builtin_modules.get_native_iterator_element_type(typ, registry=self.ctx.registry) is not None:
            return True
        return False

    def get_iterable_element_type_or_none(self, iterable_type: TpyType) -> TpyType | None:
        """Get the element type of an iterable, or None if not iterable.

        For types extending NativeIterable[T] or NativeIterator[T], returns T.
        """
        # Handle NativeIterable[T] protocol type
        if is_protocol_type(iterable_type) and iterable_type.name == "NativeIterable":
            if iterable_type.type_args:
                return iterable_type.type_args[0]
            return None

        # Handle NativeIterator[T] protocol type
        if is_protocol_type(iterable_type) and iterable_type.name == "NativeIterator":
            if iterable_type.type_args:
                return iterable_type.type_args[0]
            return None

        # Handle str -> Char
        if isinstance(iterable_type, StrType):
            return CHAR

        # Check NativeIterator extends (e.g., Range extends NativeIterator[Int32])
        iter_elem = builtin_modules.get_native_iterator_element_type(iterable_type, registry=self.ctx.registry)
        if iter_elem is not None:
            return iter_elem

        # Use get_element_type() for container types (list, Array, Span, etc.)
        return iterable_type.get_element_type()

    def get_iterable_element_type(
        self, iterable_type: TpyType, loc: SourceLocation | None = None,
    ) -> TpyType:
        """Get the element type of an iterable for for-each loops.

        Raises SemanticError if type is not iterable.
        """
        elem_type = self.get_iterable_element_type_or_none(iterable_type)
        if elem_type is not None:
            return elem_type
        raise SemanticError(f"Cannot iterate over type {iterable_type}", loc)
