"""
TurboPython Iterable Type Helpers

General-purpose iterable-type queries (is_type_iterable, get_iterable_element_type).
List literal deduction logic has moved to local_deduction.LocalTypeDeduction.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, CHAR, OwnType, CopyIterType, OwnIterType, GenExprType, SpanIterType,
    is_protocol_type, is_any_str_type, unwrap_ref_type,
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

    # Protocols whose single type argument is the iteration element type.
    # NativeIterable is a marker for C++ begin/end-style iteration; kept
    # alongside Iterator/Iterable/ReadOnlySpanLike for parameter-type use.
    # The concrete implementation of iteration (via __iter__() in codegen)
    # is independent of this sema-level protocol recognition.
    _ITERABLE_PROTOCOLS = frozenset({
        "typing.Iterator", "typing.Iterable",
        "tpy.NativeIterable", "tpy.ReadOnlySpanLike",
    })

    def is_type_iterable(self, typ: TpyType) -> bool:
        """Check if a type is iterable.

        A type is iterable if it:
        - Is a NativeIterable[T], Iterator[T], Iterable[T], or
          ReadOnlySpanLike[T] protocol type (used as parameter type)
        - Is a compiler-internal iterator adapter (CopyIter[T], OwnIter[T],
          generator-expression type) or SpanIter[T] (stub-declared but its
          __iter__(self) -> Self currently bypasses the unified lookup --
          see ITERATOR_OVERHAUL.md follow-up)
        - Has __next__() (Iterator conformance)
        - Has __iter__() returning an iterator type
        """
        if is_protocol_type(typ) and typ.qualified_name() in self._ITERABLE_PROTOCOLS:
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
        # Ref[T] and Own[T] are transparent for iteration resolution
        iterable_type = unwrap_ref_type(iterable_type)
        if isinstance(iterable_type, OwnType):
            iterable_type = iterable_type.wrapped

        # Compiler-internal iterator adapters (no stubs) plus SpanIter
        # (stub-declared; see follow-up note in is_type_iterable above).
        if isinstance(iterable_type, (CopyIterType, OwnIterType, GenExprType, SpanIterType)):
            return iterable_type.element_type

        # Protocol-typed iterables (parameter types): single type_arg is T.
        if is_protocol_type(iterable_type) and iterable_type.qualified_name() in self._ITERABLE_PROTOCOLS:
            if iterable_type.type_args:
                first = iterable_type.type_args[0]
                return first if isinstance(first, TpyType) else None
            return None

        # Handle str/String/StrView -> Char
        if is_any_str_type(iterable_type):
            return CHAR

        # Check error_return __next__ (user-defined iterators)
        er_elem = builtin_modules.get_error_return_next_element_type(iterable_type, registry=self.ctx.registry)
        if er_elem is not None:
            return er_elem

        # Check __iter__() method (container -> separate iterator).
        # Covers built-in containers (list, dict, set, Span, Array, Range,
        # str, bytes, ...) via their declared __iter__ stubs, plus user
        # records and types with __iter__ methods.
        iter_elem = builtin_modules.get_iter_element_type(iterable_type, registry=self.ctx.registry)
        if iter_elem is not None:
            return iter_elem

        return None

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
