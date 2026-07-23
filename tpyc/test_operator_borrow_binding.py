"""Borrow-returning operator dunders: temp-operand diagnostics (sema).

The aliasing itself is exercised end-to-end by tests/cases/operators/
(op_borrow_return_alias etc.). These unit tests pin the borrow-registration
half that has no snapshot witness: binding the result of a borrow-returning
dunder over TEMPORARY operands warns (the borrow dangles at end-of-statement),
exactly like the method-call spelling -- and the Own-returning inverse stays
silent. A snapshot case can't pin this: the warned binding genuinely dangles,
so the exec phase's -Werror=dangling-reference rejects the generated C++.
"""

from . import get_lib_dir
from .compiler import Compiler
from .diagnostics import DiagnosticLevel

_STDLIB_DIRS = [get_lib_dir() / "tpy"]

_ACC_BORROW = (
    "from tpy import Int32\n"
    "class Acc:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n"
    "        self.n = n\n"
    "    def __add__(self, o: 'Acc') -> 'Acc':\n"
    "        return self if self.n >= o.n else o\n"
    "    def __neg__(self) -> 'Acc':\n"
    "        return self\n"
)

_ACC_OWN = (
    "from tpy import Int32, Own\n"
    "class Acc:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32):\n"
    "        self.n = n\n"
    "    def __add__(self, o: 'Acc') -> Own['Acc']:\n"
    "        return Acc(self.n + o.n)\n"
)


def _warnings(source: str) -> list[str]:
    compiler = Compiler.from_source(source, lib_dirs=_STDLIB_DIRS)
    modules = compiler.compile()
    return [d.message
            for m in modules
            if getattr(m, "analyzer", None) is not None
            for d in m.analyzer.diagnostics
            if d.level == DiagnosticLevel.WARNING]


class TestTempOperandBorrowWarnings:
    def test_temp_operands_warn_receiver_and_argument(self):
        warnings = _warnings(
            _ACC_BORROW
            + "def f() -> None:\n"
            + "    t = Acc(4) + Acc(2)\n"
            + "    print('x')\n"
        )
        assert any("temporary receiver" in w for w in warnings)
        assert any("temporary argument" in w for w in warnings)

    def test_unary_temp_receiver_warns(self):
        warnings = _warnings(
            _ACC_BORROW
            + "def f() -> None:\n"
            + "    u = -Acc(3)\n"
            + "    print('x')\n"
        )
        assert any("temporary receiver" in w for w in warnings)

    def test_stable_operands_do_not_warn(self):
        warnings = _warnings(
            _ACC_BORROW
            + "def f() -> None:\n"
            + "    a = Acc(4)\n"
            + "    b = Acc(2)\n"
            + "    c = a + b\n"
            + "    print(c.n)\n"
        )
        assert not any("temporary" in w for w in warnings)

    def test_own_return_temp_operands_do_not_warn(self):
        # Fresh-value result: nothing is borrowed, temp operands are fine.
        warnings = _warnings(
            _ACC_OWN
            + "def f() -> None:\n"
            + "    t = Acc(4) + Acc(2)\n"
            + "    print(t.n)\n"
        )
        assert not any("temporary" in w for w in warnings)
