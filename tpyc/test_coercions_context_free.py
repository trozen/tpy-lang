"""`context_free_wrap` is a hand-set flag; this is what enforces it.

A flagged row promises that its C++ render is a single wrap around the operand
that reads neither the coercion context nor the operand's text. THIR carries no
`CoercionContext`, so the lowering pre-renders the row ONCE as a `{0}` template
and `emit` fills the operand in with `.format()`. A row that quietly starts
reading either would render correctly at the position it was tested at and
wrongly everywhere else, with nothing to catch it.
"""
import pytest

from tpyc.coercions import (
    COERCIONS, Coercion, CoercionContext, context_free_wrap_template,
)
from tpyc.typesys import INT32

_FLAGGED: list[Coercion] = [c for c in COERCIONS if c.context_free_wrap]
_OPERANDS: tuple[str, ...] = ("x", "f(a, b)", "(*p)", "")


def test_rows_are_flagged() -> None:
    assert _FLAGGED, "no context_free_wrap rows -- the check below is vacuous"


@pytest.mark.parametrize("coercion", _FLAGGED, ids=[c.name for c in _FLAGGED])
def test_flagged_row_renders_as_one_operand_template(
        coercion: Coercion) -> None:
    # Only `expected.to_cpp()` is ever read off the types, so one concrete pair
    # exercises every flagged lambda.
    template = context_free_wrap_template(coercion, INT32, INT32)
    assert template is not None
    assert template.count("{0}") == 1, template
    for ctx in CoercionContext:
        for operand in _OPERANDS:
            assert (coercion.codegen(operand, INT32, INT32, ctx)
                    == template.format(operand))
