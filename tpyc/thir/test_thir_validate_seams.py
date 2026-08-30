"""The structural validator over the two SEAM body shapes -- the resumable
frame and the simple-generator peephole, whose leaves the skeleton holds in
seam tables rather than one linear function body.

Pins that lowering actually runs the gate on both (a body reaching the seam
unvalidated is the hole these units exist for), that a planted form lie in a
seam position is caught, and that the two rules narrowed for shapes only
these bodies carry still reject their adjacent form."""

from __future__ import annotations

import pytest

from ..codegen_cpp.context import CodeGenOptions
from ..typesys import INT32
from .nodes import (
    Form, THIRCoerce, THIRExprStmt, THIRFormConvert, THIRLiteral,
    THIRResumableBody, THIRSimpleGenBody,
)
from . import validate as _validate
from .lower import resumable as _lower_resumable_mod
from .lower import simple_gen as _lower_simple_gen_mod
from .testutil import _compile, _entry
from .validate import (
    THIRValidationError, validate_resumable_body, validate_simple_gen_body,
)

_PRE = "from tpy import Int32\nfrom typing import Iterator\n\n"

_ASYNC_SRC = (_PRE
              + "async def step(n: Int32) -> Int32:\n"
              + "    return n + 1\n\n"
              + "async def runner(a: Int32) -> Int32:\n"
              + "    n = a\n"
              + "    while n < 3:\n"
              + "        n = await step(n)\n"
              + "    return n\n\n"
              + "def main() -> None:\n    pass\nmain()\n")

_SIMPLE_GEN_SRC = (_PRE
                   + "def gen(n: Int32) -> Iterator[Int32]:\n"
                   + "    i = 0\n"
                   + "    while i < n:\n"
                   + "        yield i\n"
                   + "        i = i + 1\n\n"
                   + "def main() -> None:\n"
                   + "    for x in gen(3):\n        print(x)\nmain()\n")


def _emit_thir(src: str) -> None:
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=True, thir_codegen=True))


def _form_lie() -> THIRCoerce:
    """A coerce claiming VALUE over a STORAGE inner -- the shape the
    validator exists to catch."""
    inner = THIRLiteral(result_type=INT32, value=1, form=Form.STORAGE)
    return THIRCoerce(result_type=INT32, expr=inner,
                      coercion_name="int_literal", form=Form.VALUE)


class TestSeamBodiesAreValidated:
    """Mechanical: the lowering entry point CALLS the gate. A render-string
    or byte-identity assertion here would pass even with the call removed."""

    def _record(self, monkeypatch, module, attr, real):
        seen = []

        def spy(owner, body):
            seen.append((owner, body))
            return real(owner, body)

        monkeypatch.setattr(module, attr, spy)
        return seen

    def test_resumable_body_reaches_the_validator(self, monkeypatch):
        seen = self._record(monkeypatch, _lower_resumable_mod,
                            "validate_resumable_body", validate_resumable_body)
        _emit_thir(_ASYNC_SRC)
        assert seen, "no resumable body was validated"
        assert any(b.leaves for _owner, b in seen)

    def test_simple_gen_body_reaches_the_validator(self, monkeypatch):
        seen = self._record(monkeypatch, _lower_simple_gen_mod,
                            "validate_simple_gen_body",
                            validate_simple_gen_body)
        _emit_thir(_SIMPLE_GEN_SRC)
        assert seen, "no simple-generator body was validated"
        assert any(b.pre_yield or b.post_yield or b.init for _o, b in seen)

    def test_frame_nested_def_body_reaches_the_validator(self, monkeypatch):
        seen = []
        real = _validate.validate_stmts

        def spy(owner, stmts, return_type=None):
            seen.append((owner, stmts))
            return real(owner, stmts, return_type)

        monkeypatch.setattr(_lower_resumable_mod, "validate_stmts", spy)
        _emit_thir(_PRE
                   + "async def outer(a: Int32) -> Int32:\n"
                   + "    def helper(v: Int32) -> Int32:\n"
                   + "        return v + 1\n"
                   + "    try:\n"
                   + "        return helper(a)\n"
                   + "    finally:\n"
                   + "        print(a)\n\n"
                   + "def main() -> None:\n    pass\nmain()\n")
        assert seen, "no frame nested-def member body was validated"


class TestSeamPositionsRaise:
    def test_leaf_form_lie_raises(self):
        body = THIRResumableBody(
            leaves={1: THIRExprStmt(expr=_form_lie())},
            conds={}, await_args={}, return_values={})
        with pytest.raises(THIRValidationError, match="coerce form"):
            validate_resumable_body("f", body)

    def test_condition_form_lie_raises(self):
        body = THIRResumableBody(
            leaves={}, conds={1: _form_lie()}, await_args={},
            return_values={})
        with pytest.raises(THIRValidationError, match="coerce form"):
            validate_resumable_body("f", body)

    def test_yield_value_form_lie_raises(self):
        body = THIRResumableBody(
            leaves={}, conds={}, await_args={}, return_values={},
            yield_values={1: _form_lie()})
        with pytest.raises(THIRValidationError, match="coerce form"):
            validate_resumable_body("f", body)

    def test_simple_gen_init_form_lie_raises(self):
        body = THIRSimpleGenBody(
            init=(THIRExprStmt(expr=_form_lie()),), pre_yield=(),
            post_yield=(),
            yield_value=THIRLiteral(result_type=INT32, value=0))
        with pytest.raises(THIRValidationError, match="coerce form"):
            validate_simple_gen_body("g", body)

    def test_simple_gen_yield_value_form_lie_raises(self):
        body = THIRSimpleGenBody(init=(), pre_yield=(), post_yield=(),
                                 yield_value=_form_lie())
        with pytest.raises(THIRValidationError, match="coerce form"):
            validate_simple_gen_body("g", body)


class TestNarrowedRules:
    """The two rules widened for shapes the seam bodies carry, each with the
    adjacent form that must keep failing."""

    def _in_return(self, expr):
        return THIRResumableBody(leaves={}, conds={}, await_args={},
                                 return_values={1: expr})

    def test_value_typed_noop_convert_still_raises(self):
        # The address-of exemption is keyed on the plain-non-value families;
        # a VALUE-typed same-form convert stays dead.
        inner = THIRLiteral(result_type=INT32, value=1, form=Form.BORROW)
        bad = THIRFormConvert(result_type=INT32, value=inner,
                              form=Form.BORROW, move=False)
        with pytest.raises(THIRValidationError, match="no-op form convert"):
            validate_resumable_body("f", self._in_return(bad))

    def test_async_return_addr_of_coerce_passes(self):
        # `&(x)` materializes a pointer prvalue out of a borrow source.
        inner = THIRLiteral(result_type=INT32, value=1, form=Form.BORROW)
        ok = THIRCoerce(result_type=INT32, expr=inner,
                        coercion_name="async_ret_addr_of", wrap="&({0})",
                        form=Form.VALUE)
        validate_resumable_body("f", self._in_return(ok))

    def test_unnamed_wrap_with_the_same_shape_still_raises(self):
        inner = THIRLiteral(result_type=INT32, value=1, form=Form.BORROW)
        bad = THIRCoerce(result_type=INT32, expr=inner,
                         coercion_name="int_literal", wrap="&({0})",
                         form=Form.VALUE)
        with pytest.raises(THIRValidationError, match="coerce form"):
            validate_resumable_body("f", self._in_return(bad))
