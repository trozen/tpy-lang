"""A `T&` lvalue read returned at a VALUE record's by-value return slot.

`-> D` on a value record is spelled like a borrow return but returns BY
VALUE, so every borrow source at it (an lvalue ternary, a container element,
a `T&`-returning call passthrough) reads to the return rule as a form lie
even though the C++ return object simply copy-constructs from the reference.
The rule keys on the return TYPE, which cannot see that; the escape keys on
the VALUE's own type instead.

The escape is deliberately narrow. A borrow at any OTHER value-typed return
still raises -- an owned `str` slot cannot be initialized from a
`std::string_view` without a spelled convert, which is exactly the class the
rule exists to catch."""

from __future__ import annotations

import pytest

from ..compilation_context import activate_compiler
from ..compiler import CompiledModule, Compiler
from ..typesys import NominalType, TpyType
from .lower import lower_module
from .nodes import (
    Form, THIRExpr, THIRFunction, THIRFunctionLayout, THIRName, THIRReturn,
)
from .testutil import (
    _assert_rejects_at,
    _reject_tally,
    _assert_routes_byte_identical, _compile, _entry, _fn, _thir_ctx,
)
from .validate import THIRValidationError, validate_function

# A value record plus the sources that hand its `T&` lvalue to a by-value
# return slot.
_D = (
    "from tpy import Int32, ValueType\n"
    "class D(ValueType):\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
)


def _use(call: str) -> str:
    return ("def use() -> None:\n"
            f"    {call}\n"
            "use()\n")


class TestValueRecordBorrowReturnRoutes:
    def test_lvalue_ternary_dunder_routes(self):
        # A value record's dunder returning a ternary over two lvalues.
        src = _D + (
            "    def __add__(self, o: D) -> D:\n"
            "        return self if self.n >= o.n else o\n"
        ) + (
            "def use() -> None:\n"
            "    a = D(1)\n"
            "    b = D(2)\n"
            "    print((a + b).n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return (((this->n >= o.n)) ? ((*this)) : (o));" in hpp + cpp

    def test_ternary_over_params_routes(self):
        # The same ternary in a FREE function -- no dunder, no receiver.
        src = _D + (
            "def pick(a: D, b: D) -> D:\n"
            "    return a if a.n >= b.n else b\n"
        ) + (
            "def use() -> None:\n"
            "    a = D(1)\n"
            "    b = D(2)\n"
            "    print(pick(a, b).n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return (((a.n >= b.n)) ? (a) : (b));" in hpp + cpp

    def test_container_element_return_routes(self):
        # The element read is a `T&` lvalue off `operator[]`; the by-value
        # return copies out of it.
        src = _D + (
            "def first(xs: list[D]) -> D:\n"
            "    return xs[0]\n"
        ) + (
            "def use() -> None:\n"
            "    xs = [D(7), D(8)]\n"
            "    print(first(xs).n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return ::tpy::__getitem__(xs, 0);" in hpp + cpp

    def test_by_value_call_passthrough_routes(self):
        src = _D + (
            "def first(xs: list[D]) -> D:\n"
            "    return xs[0]\n"
            "def again(xs: list[D]) -> D:\n"
            "    return first(xs)\n"
        ) + (
            "def use() -> None:\n"
            "    xs = [D(7), D(8)]\n"
            "    print(again(xs).n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return first(xs);" in hpp + cpp

    def test_ternary_at_value_variant_return_routes(self):
        # The value-VARIANT slot (`std::variant<D, E>`) takes the same lvalue
        # ternary through the variant's converting ctor.
        src = _D + (
            "class E(ValueType):\n"
            "    m: Int32\n"
            "    def __init__(self, m: Int32) -> None:\n"
            "        self.m = m\n"
            "def widen(a: D, b: D) -> D | E:\n"
            "    return a if a.n >= b.n else b\n"
        ) + (
            "def use() -> None:\n"
            "    a = D(1)\n"
            "    b = D(2)\n"
            "    w = widen(a, b)\n"
            "    if isinstance(w, D):\n"
            "        print(w.n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        assert "return (((a.n >= b.n)) ? (a) : (b));" in hpp + cpp


class TestAdjacentShapesKeepTheirBehaviour:
    def test_reference_record_ternary_return_still_borrows(self):
        # BOUNDARY: a NON-value record's `-> Acc` return IS the borrow slot
        # (`Acc&`), which the rule already allowed -- the escape must not
        # change how that renders.
        src = (
            "from tpy import Int32\n"
            "class Acc:\n"
            "    n: Int32\n"
            "    def __init__(self, n: Int32) -> None:\n"
            "        self.n = n\n"
            "def pick(a: Acc, b: Acc) -> Acc:\n"
            "    return a if a.n >= b.n else b\n"
        ) + (
            "def use() -> None:\n"
            "    a = Acc(1)\n"
            "    b = Acc(2)\n"
            "    print(pick(a, b).n)\n"
            "use()\n"
        )
        hpp, cpp = _assert_routes_byte_identical(src)
        both = hpp + cpp
        assert "Acc& pick(Acc& a, Acc& b);" in both
        assert "return (((a.n >= b.n)) ? (a) : (b));" in both

    def test_prvalue_ternary_arm_keeps_rejecting(self):
        # BOUNDARY: a ctor arm makes the C++ ternary a PRVALUE, which
        # `ifexpr.record` rejects -- the escape is about the return rule, so
        # it must not admit an arm shape the ternary gate turns away.
        src = _D + (
            "def pick(a: D) -> D:\n"
            "    return a if a.n >= 0 else D(0)\n"
        ) + (
            "def use() -> None:\n"
            "    a = D(1)\n"
            "    print(pick(a).n)\n"
            "use()\n"
        )
        _assert_rejects_at(_reject_tally(src), "body:expr.ifexpr")


class TestBorrowReturnRuleStillCatchesFormLies:
    """The escape reads the VALUE's type, so it must not fire for a
    value-typed return that is not a record. Planted nodes -- lowering has no
    arm that builds these, which is what the rule guards against."""

    def _lowered(self, src: str) -> tuple[Compiler, CompiledModule]:
        compiler, modules = _compile(src)
        return compiler, _entry(modules)

    def _wrap(self, value: THIRExpr, return_type: TpyType) -> THIRFunction:
        return THIRFunction(name="w", params=(), return_type=return_type,
                            body=(THIRReturn(value=value),),
                            layout=THIRFunctionLayout())

    def test_value_record_borrow_passes(self):
        src = _D + (
            "def pick(a: D) -> D:\n"
            "    return a\n"
        ) + _use("print(pick(D(1)).n)")
        compiler, entry = self._lowered(src)
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
            d = _fn(thir, "pick").return_type
            assert isinstance(d, NominalType) and d.is_value_type()
            validate_function(self._wrap(
                THIRName(result_type=d, name="a", form=Form.BORROW), d))

    def test_owned_str_borrow_still_raises(self):
        # A `std::string_view` at a `std::string` return needs the spelled
        # copy; `str` is value-typed but not a record, so the escape must
        # leave this on the rule.
        src = ("def greet(s: str) -> str:\n"
               "    return s\n") + _use('print(greet("hi"))')
        compiler, entry = self._lowered(src)
        with activate_compiler(compiler):
            thir = lower_module(entry.ast, entry.analyzer)
            st = _fn(thir, "greet").return_type
            with pytest.raises(THIRValidationError,
                               match="BORROW return value"):
                validate_function(self._wrap(
                    THIRName(result_type=st, name="s", form=Form.BORROW), st))
