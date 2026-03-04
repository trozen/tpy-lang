"""
TurboPython Iterable Type Helpers

General-purpose iterable-type queries (is_type_iterable, get_iterable_element_type).
List literal deduction logic has moved to local_deduction.LocalTypeDeduction.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, CHAR, is_protocol_type, is_any_str_type,
)
from .diagnostics import SemanticError
from .protocols import record_extends_any

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
        - Is a NativeIterable[T], OptIterator[T], Iterator[T], or Iterable[T] protocol type
        - Declares extends=["NativeIterable[T]"] or extends=["OptIterator[T]"]
        - Has __next_opt__() or __next__() (OptIterator conformance)
        - Has __iter__() returning an iterator type
        """
        # NativeIterable[T], OptIterator[T], Iterator[T], or Iterable[T] protocol type
        if is_protocol_type(typ) and typ.name in ("NativeIterable", "OptIterator", "Iterator", "Iterable"):
            return True
        # Check if type extends NativeIterable or OptIterator
        if record_extends_any(typ, "NativeIterable", self.ctx.registry):
            return True
        if builtin_modules.get_native_iterator_element_type(typ, registry=self.ctx.registry) is not None:
            return True
        # Check __span__() method (zero-cost span-based iteration)
        if builtin_modules.get_span_element_type(typ, registry=self.ctx.registry) is not None:
            return True
        # Check __iter__() method
        if builtin_modules.get_iter_element_type(typ, registry=self.ctx.registry) is not None:
            return True
        return False

    def get_iterable_element_type_or_none(self, iterable_type: TpyType) -> TpyType | None:
        """Get the element type of an iterable, or None if not iterable.

        For types extending NativeIterable[T] or OptIterator[T], returns T.
        """
        # Handle NativeIterable[T] protocol type
        if is_protocol_type(iterable_type) and iterable_type.name == "NativeIterable":
            if iterable_type.type_args:
                return iterable_type.type_args[0]
            return None

        # Handle OptIterator[T] protocol type
        if is_protocol_type(iterable_type) and iterable_type.name == "OptIterator":
            if iterable_type.type_args:
                return iterable_type.type_args[0]
            return None

        # Handle Iterator[T] and Iterable[T] protocol types
        if is_protocol_type(iterable_type) and iterable_type.name in ("Iterator", "Iterable"):
            if iterable_type.type_args:
                return iterable_type.type_args[0]
            return None

        # Handle str/String/StrView -> Char
        if is_any_str_type(iterable_type):
            return CHAR

        # Check OptIterator extends (e.g., Range extends OptIterator[Int32])
        iter_elem = builtin_modules.get_native_iterator_element_type(iterable_type, registry=self.ctx.registry)
        if iter_elem is not None:
            return iter_elem

        # Check __span__() method (zero-cost span-based iteration)
        span_elem = builtin_modules.get_span_element_type(iterable_type, registry=self.ctx.registry)
        if span_elem is not None:
            return span_elem

        # Check __iter__() method (container -> separate iterator)
        iter_elem = builtin_modules.get_iter_element_type(iterable_type, registry=self.ctx.registry)
        if iter_elem is not None:
            return iter_elem

        # Use get_iteration_element_type() for container types (list, Array, Span, dict, etc.)
        # Dict overrides this to return K (key type) instead of V (value type).
        return iterable_type.get_iteration_element_type()

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
