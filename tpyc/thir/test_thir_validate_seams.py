"""The structural validator over the SEAM body shape -- the resumable
frame, whose leaves the skeleton holds in seam tables rather than one linear
function body.

Pins that lowering actually runs the gate (a body reaching the seam
unvalidated is the hole these units exist for), that a planted form lie in a
seam position is caught, and that the two rules narrowed for shapes only
these bodies carry still reject their adjacent form."""

from __future__ import annotations

import dataclasses

import pytest

from ..codegen_cpp.context import CodeGenOptions
from ..compilation_context import activate_compiler
from ..typesys import INT32, VoidType
from .nodes import (
    Form, THIRArgTemp, THIRBinOp, THIRCall, THIRCoerce, THIRExprStmt,
    THIRFieldAccess, THIRFormConvert, THIRFunction, THIRFunctionLayout,
    THIRLiteral,
    THIRMethodCall, THIRRaise, THIRResumableBody, THIRReturn, THIRSelf,
    THIRUnionArgLift,
)
from . import validate as _validate
from .lower import iter_module_constructors, lower_constructor
from .lower import resumable as _lower_resumable_mod
from .testutil import _compile, _entry, _lower_fn
from .validate import (
    THIRValidationError, validate_constructor, validate_function,
    validate_resumable_body,
)

# The whole-function / constructor validator rules: no compiling program can
# reach a _fail arm (reaching one would mean lowering emitted malformed THIR),
# so the rules have no case-shaped pin and live here over hand-built THIR.
_PTR_RECORDS = (
    "from tpy import int32, Own, readonly\n"
    "class A:\n    x: int32\n    def __init__(self, x: int32):\n        self.x = x\n"
    "class B:\n    y: int32\n    def __init__(self, y: int32):\n        self.y = y\n"
    "class H:\n"
    "    u: A | B\n"
    "    n: int32\n"
    "    def __init__(self, v: Own[A | B]):\n"
    "        self.u = v\n        self.n = 0\n"
)


_PRE = "from tpy import int32\nfrom typing import Iterator\n\n"

_ASYNC_SRC = (_PRE
              + "async def step(n: int32) -> int32:\n"
              + "    return n + 1\n\n"
              + "async def runner(a: int32) -> int32:\n"
              + "    n = a\n"
              + "    while n < 3:\n"
              + "        n = await step(n)\n"
              + "    return n\n\n"
              + "def main() -> None:\n    pass\nmain()\n")

def _emit_thir(src: str) -> None:
    compiler, modules = _compile(src)
    compiler.generate_code_to_strings(
        _entry(modules),
        options=CodeGenOptions(emit_source_comments=True))


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

    def test_frame_nested_def_body_reaches_the_validator(self, monkeypatch):
        seen = []
        real = _validate.validate_stmts

        def spy(owner, stmts, return_type=None):
            seen.append((owner, stmts))
            return real(owner, stmts, return_type)

        monkeypatch.setattr(_lower_resumable_mod, "validate_stmts", spy)
        _emit_thir(_PRE
                   + "async def outer(a: int32) -> int32:\n"
                   + "    def helper(v: int32) -> int32:\n"
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


class TestArgListFlushRight:
    """The one arg-list walk carries a per-position flush right. The raise arm
    grants it (its ctor arg temps, union lift included, hoist before the throw
    line); sharing the walk must not have loosened what a NON-flush position
    rejects."""

    @staticmethod
    def _lift(value=None, temp=True):
        return THIRUnionArgLift(
            result_type=INT32, variant_cpp="std::variant<int32_t*>",
            value=value if value is not None
            else THIRLiteral(result_type=INT32, value=1),
            temp_cpp="std::variant<int32_t*>" if temp else None)

    def test_temp_bearing_lift_at_a_cond_seam_raises(self):
        # A resumable CONDITION has no flush point in the skeleton.
        body = THIRResumableBody(
            leaves={}, conds={1: self._lift()}, await_args={},
            return_values={})
        with pytest.raises(THIRValidationError,
                           match="THIRUnionArgLift outside a call arg"):
            validate_resumable_body("f", body)

    def test_nested_temp_under_a_lift_raises_even_where_temps_flush(self):
        # A temp is admitted only as a DIRECT arg: nesting one inside a lift's
        # value is rejected in an arg list that DOES grant the flush right,
        # so the raise arm's widened right cannot reach it either.
        nested = THIRArgTemp(result_type=INT32,
                             init=THIRLiteral(result_type=INT32, value=1),
                             cpp_type="int32_t")
        body = THIRResumableBody(
            leaves={}, conds={}, return_values={},
            await_args={1: (self._lift(value=nested),)})
        with pytest.raises(THIRValidationError,
                           match="THIRArgTemp outside a call arg"):
            validate_resumable_body("f", body)

    def test_raise_args_grant_the_flush_right(self):
        # A raise's ctor args ARE a flush position: its temps hoist before the
        # throw line, so a temp-bearing lift is admitted there and the same
        # node at a cond seam, which has no flush point, is not.
        body = THIRResumableBody(
            leaves={1: THIRRaise(cpp_type="::tpy::ValueError",
                                 args=(self._lift(),))},
            conds={}, await_args={}, return_values={})
        validate_resumable_body("f", body)

    def test_raise_args_still_reject_a_nested_temp(self):
        nested = THIRArgTemp(result_type=INT32,
                             init=THIRLiteral(result_type=INT32, value=1),
                             cpp_type="int32_t")
        body = THIRResumableBody(
            leaves={1: THIRRaise(cpp_type="::tpy::ValueError",
                                 args=(self._lift(value=nested),))},
            conds={}, await_args={}, return_values={})
        with pytest.raises(THIRValidationError,
                           match="THIRArgTemp outside a call arg"):
            validate_resumable_body("f", body)

    def test_plain_temp_bearing_lift_in_an_arg_list_passes(self):
        # The emplace arg list IS a flush position, like a call's or a raise's.
        body = THIRResumableBody(
            leaves={}, conds={}, return_values={},
            await_args={1: (self._lift(),)})
        validate_resumable_body("f", body)


class TestTransparentWrapperTemps:
    """`_TRANSPARENT_WRAPPERS`: a temp reached through a wrapper that only
    re-renders it in place keeps the flush right of the position the wrapper
    sits in -- the frame-view backing arg (`::tpy::as_mut_span(__tmp_N)`) is
    the shape that needs it. The right comes from the POSITION, not from the
    wrapper, and an opaque parent grants nothing."""

    @staticmethod
    def _temp():
        return THIRArgTemp(result_type=INT32,
                           init=THIRLiteral(result_type=INT32, value=1),
                           cpp_type="int32_t", movable=True)

    def _coerced_temp(self):
        return THIRCoerce(result_type=INT32, expr=self._temp(),
                          coercion_name="int_literal")

    def test_temp_under_a_coerce_in_an_arg_list_passes(self):
        body = THIRResumableBody(
            leaves={}, conds={}, return_values={},
            await_args={1: (self._coerced_temp(),)})
        validate_resumable_body("f", body)

    def test_temp_under_an_opaque_parent_in_an_arg_list_raises(self):
        # A binop is not a pure re-render of its operand: the operand's decl
        # would have to hoist out of an expression the emit composes itself.
        opaque = THIRBinOp(result_type=INT32, left=self._temp(), op="<=",
                           right=THIRLiteral(result_type=INT32, value=1),
                           resolved=None)
        body = THIRResumableBody(
            leaves={}, conds={}, return_values={},
            await_args={1: (opaque,)})
        with pytest.raises(THIRValidationError,
                           match="THIRArgTemp outside a call arg"):
            validate_resumable_body("f", body)

    def test_coerced_temp_outside_a_flush_position_raises(self):
        # A resumable RETURN VALUE has no flush point (the scaffolding binds
        # it into its own slot), and transparency does not manufacture one.
        body = THIRResumableBody(
            leaves={}, conds={}, await_args={},
            return_values={1: self._coerced_temp()})
        with pytest.raises(THIRValidationError,
                           match="THIRArgTemp outside a call arg"):
            validate_resumable_body("f", body)


class TestValidator:
    def _valid_fn(self, body) -> THIRFunction:
        return THIRFunction(name="t", params=(), return_type=VoidType(),
                            body=tuple(body), layout=THIRFunctionLayout())

    def test_noop_form_convert_raises(self):
        inner = THIRLiteral(result_type=INT32, value=1, form=Form.STORAGE)
        bad = THIRFormConvert(result_type=INT32, value=inner, form=Form.STORAGE)
        fn = self._valid_fn([THIRReturn(value=bad)])
        with pytest.raises(THIRValidationError, match="no-op form convert"):
            validate_function(fn)

    def test_form_changing_convert_passes(self):
        inner = THIRLiteral(result_type=INT32, value=1, form=Form.BORROW)
        ok = THIRFormConvert(result_type=INT32, value=inner, form=Form.STORAGE)
        validate_function(self._valid_fn([THIRReturn(value=ok)]))

    def test_coerce_form_lie_raises(self):
        # A non-view-target coerce must carry its inner form.
        inner = THIRLiteral(result_type=INT32, value=1, form=Form.STORAGE)
        bad = THIRCoerce(result_type=INT32, expr=inner,
                         coercion_name="int_literal", form=Form.VALUE)
        fn = self._valid_fn([THIRReturn(value=bad)])
        with pytest.raises(THIRValidationError, match="coerce form"):
            validate_function(fn)

    def test_corpus_units_still_validate(self):
        # The validator runs inside lower_function; any unit in this file
        # lowering successfully already exercises it. Sanity-check one shape
        # with a genuine THIRFormConvert (str view->owned).
        fn = _lower_fn('def s(v: str) -> str:\n    t: str = v\n    return t\n',
                       "s")
        assert fn is not None

    # --- sink-position raise paths (U2): strip the convert off a GOOD
    # --- lowering and assert the validator screams. Built inside a compiler
    # --- context so the pointer-repr predicates resolve.

    def test_borrow_at_pointer_lifted_field_write_raises(self):
        fn = _lower_fn(
            _PTR_RECORDS + "def wf(h: H, v: A | B) -> None:\n    h.u = v\n",
            "wf")
        good = fn.body[0]
        bad = dataclasses.replace(good, value=good.value.value)
        with pytest.raises(THIRValidationError,
                           match="pointer-lifted field-write"):
            validate_function(dataclasses.replace(fn, body=(bad,)))

    def test_borrow_at_mil_cell_raises(self):
        compiler, modules = _compile(_PTR_RECORDS)
        entry = _entry(modules)
        with activate_compiler(compiler):
            for rec, init, self_type in iter_module_constructors(
                    entry.ast, entry.analyzer):
                if rec.name != "H":
                    continue
                ctor = lower_constructor(rec, init, entry.analyzer,
                                         self_type=self_type)
                mil = ctor.mil_inits[0]
                bad = dataclasses.replace(
                    mil, value=dataclasses.replace(mil.value,
                                                   form=Form.BORROW))
                with pytest.raises(THIRValidationError, match="MIL cell"):
                    validate_constructor(
                        dataclasses.replace(ctor, mil_inits=(bad,)))
                return
        raise AssertionError("H ctor not lowered")

    def test_borrow_return_of_value_type_raises(self):
        bad = THIRReturn(value=THIRLiteral(result_type=INT32, value=1,
                                           form=Form.BORROW))
        fn = THIRFunction(name="t", params=(), return_type=INT32,
                          body=(bad,), layout=THIRFunctionLayout())
        with pytest.raises(THIRValidationError, match="BORROW return"):
            validate_function(fn)


class TestNodeStructuralRules:
    """Per-node invariants the validator holds over hand-built THIR: no
    lowering builds these shapes, so a case cannot reach them."""

    def _fn(self, node) -> THIRFunction:
        return THIRFunction(
            name="w", params=(), return_type=VoidType(),
            body=(THIRReturn(value=node),), layout=THIRFunctionLayout())

    def _self(self, deref: bool) -> THIRSelf:
        return THIRSelf(result_type=INT32, form=Form.BORROW, deref=deref)

    def test_arrow_field_over_bare_receiver_passes(self):
        validate_function(self._fn(THIRFieldAccess(
            result_type=INT32, receiver=self._self(False),
            field_cpp="n", is_arrow=True)))

    def test_arrow_field_over_dereferenced_receiver_fails(self):
        # `(*this)->x` is not valid C++: the member reached through the
        # pointer must take the raw receiver.
        with pytest.raises(THIRValidationError, match="arrow member access"):
            validate_function(self._fn(THIRFieldAccess(
                result_type=INT32, receiver=self._self(True),
                field_cpp="n", is_arrow=True)))

    def test_arrow_method_over_dereferenced_receiver_fails(self):
        with pytest.raises(THIRValidationError, match="arrow member access"):
            validate_function(self._fn(THIRMethodCall(
                result_type=INT32, receiver=self._self(True),
                method_cpp="m", args=(), is_arrow=True)))

    def test_template_args_with_native_callee_fails(self):
        # A native or cpp_template callee spells its own template arguments.
        bad = THIRCall(result_type=VoidType(), callee="f", args=(),
                       native_name="tpy::f", template_args_cpp=("int32_t",))
        fn = THIRFunction(name="f", params=(), return_type=VoidType(),
                          body=(THIRExprStmt(expr=bad),),
                          layout=THIRFunctionLayout())
        with pytest.raises(THIRValidationError, match="template_args_cpp"):
            validate_function(fn)
