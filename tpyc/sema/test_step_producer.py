"""`step_producer` answers from declarations alone, so a context with no
protocol checker installed still answers the gate-only path."""

from __future__ import annotations

from ..diagnostics import Scope
from ..typesys import TypeRegistry
from .context import SemanticContext
from .iter_loans import step_producer


def test_no_iterable_and_no_type_needs_no_protocol_checker() -> None:
    ctx = SemanticContext(TypeRegistry(), Scope())
    assert ctx.protocols is None
    assert step_producer(ctx, None, None) is None
