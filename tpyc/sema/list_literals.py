"""
TurboPython Iterable Type Helpers

General-purpose iterable-type queries (is_type_iterable, get_iterable_element_type).
List literal deduction logic has moved to local_deduction.LocalTypeDeduction.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, CopyIterType, OwnIterType, GenExprType, SpanIterType,
    is_protocol_type,
)
from .diagnostics import SemanticError

if TYPE_CHECKING:
    from ..parse import SourceLocation
    from .context import SemanticContext

from tpyc import modules as builtin_modules


class IterableHelper:
    """General-purpose iterable type queries."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def is_type_iterable(self, typ: TpyType) -> bool:
        """Check if a type is iterable.

        A type is iterable if it:
        - Is a NativeIterable[T], Iterator[T], Iterable[T], or
          Spannable[T] protocol type (used as parameter type)
        - Is a compiler-internal iterator adapter (CopyIter[T], OwnIter[T],
          generator-expression type) or SpanIter[T] (stub-declared but its
          __iter__(self) -> Self currently bypasses the unified lookup --
          the parser doesn't resolve Self to SpanIterType, so the adapter
          allowlist covers it; low-priority follow-up)
        - Has __next__() (Iterator conformance)
        - Has __iter__() returning an iterator type
        """
        if is_protocol_type(typ) and typ.qualified_name() in builtin_modules.ITERABLE_PROTOCOL_QNAMES:
            return True
        if isinstance(typ, (CopyIterType, OwnIterType, GenExprType, SpanIterType)):
            return True
        if builtin_modules.get_iter_element_type(typ, registry=self.ctx.registry) is not None:
            return True
        if builtin_modules.get_error_return_next_element_type(typ, registry=self.ctx.registry) is not None:
            return True
        return False

    def get_iterable_element_type_or_none(self, iterable_type: TpyType) -> TpyType | None:
        """Get the element type of an iterable, or None if not iterable."""
        return builtin_modules.get_iterable_element_type(iterable_type, registry=self.ctx.registry)

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
