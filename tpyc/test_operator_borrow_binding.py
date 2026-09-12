"""Sema borrow/copy diagnostics that no snapshot case can express.

Two families, both here because the CASE harness cannot see the diagnostic:

- Borrow-returning operator dunders over TEMPORARY operands warn (the borrow
  dangles at end-of-statement), exactly like the method-call spelling, and
  the Own-returning inverse stays silent. The aliasing itself is exercised by
  tests/cases/operators/ (op_borrow_return_alias etc.); a case cannot pin the
  WARNING because the warned binding genuinely dangles, so the exec phase's
  -Werror=dangling-reference rejects the generated C++.
- INTERIM: the Own-slot copy warning at the slots that REJECT right after it
  (an `Own[T]` parameter of a free function / method / constructor, a `yield`
  at `Iterator[Own[T]]`, a list-literal element -- the last has no case
  twin at all, since a record list literal with a borrowed element rejects
  at expr.container_literal). `compile_with_diagnostics`
  replaces every accumulated warning with the error text when a CompileError
  follows, so `# tpyc: warning(...)` on those lines can never be satisfied
  (TODO.md, "a warning alongside a compile error never reaches diag.txt").
  When that harness fix lands, move these to annotations on
  tests/cases/calls/error_own_param_borrow_call_copy and
  tests/cases/generators/error_yield_own_borrow_call_copy and delete the
  class below.
"""

from . import get_lib_dir
from .compiler import Compiler
from .diagnostics import DiagnosticLevel

_STDLIB_DIRS = [get_lib_dir() / "tpy"]

_ACC_BORROW = (
    "from tpy import int32\n"
    "class Acc:\n"
    "    n: int32\n"
    "    def __init__(self, n: int32):\n"
    "        self.n = n\n"
    "    def __add__(self, o: 'Acc') -> 'Acc':\n"
    "        return self if self.n >= o.n else o\n"
    "    def __neg__(self) -> 'Acc':\n"
    "        return self\n"
)

_ACC_OWN = (
    "from tpy import int32, Own\n"
    "class Acc:\n"
    "    n: int32\n"
    "    def __init__(self, n: int32):\n"
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


_HOLDER = (
    "from tpy import int32, Own\n"
    "class Payload:\n"
    "    v: int32\n"
    "    def __init__(self, v: int32):\n"
    "        self.v = v\n"
    "class Holder:\n"
    "    p: Payload\n"
    "    def __init__(self):\n"
    "        self.p = Payload(42)\n"
    "    def borrow(self) -> Payload:\n"
    "        return self.p\n"
)

_COPIES = "copies Payload into owned storage"


class TestOwnSlotBorrowCallCopyWarning:
    """INTERIM -- see the module docstring: these five slots warn and then
    reject, and the harness drops the warning, so the cases pin the reject
    and the warning is pinned here."""

    def test_own_param_of_free_function_warns(self):
        warnings = _warnings(
            _HOLDER
            + "def keep(p: Own[Payload]) -> int32:\n"
            + "    return p.v\n"
            + "def use(h: Holder) -> None:\n"
            + "    print(keep(h.borrow()))\n"
        )
        assert any(_COPIES in w for w in warnings)

    def test_own_param_of_method_warns(self):
        warnings = _warnings(
            _HOLDER
            + "class Sink:\n"
            + "    p: Payload\n"
            + "    def __init__(self, p: Own[Payload]):\n"
            + "        self.p = p\n"
            + "    def replace(self, q: Own[Payload]) -> None:\n"
            + "        self.p = q\n"
            + "def use(h: Holder, s: Sink) -> None:\n"
            + "    s.replace(h.borrow())\n"
        )
        assert any(_COPIES in w for w in warnings)

    def test_own_param_of_constructor_warns(self):
        warnings = _warnings(
            _HOLDER
            + "class Sink:\n"
            + "    p: Payload\n"
            + "    def __init__(self, p: Own[Payload]):\n"
            + "        self.p = p\n"
            + "def use(h: Holder) -> None:\n"
            + "    s = Sink(h.borrow())\n"
            + "    print(s.p.v)\n"
        )
        assert any(_COPIES in w for w in warnings)

    def test_yield_at_own_iterator_warns(self):
        warnings = _warnings(
            "from typing import Iterator\n"
            + _HOLDER
            + "def each(h: Holder) -> Iterator[Own[Payload]]:\n"
            + "    yield h.borrow()\n"
        )
        assert any(_COPIES in w for w in warnings)

    def test_list_literal_element_warns(self):
        warnings = _warnings(
            _HOLDER
            + "def use(h: Holder) -> None:\n"
            + "    xs: list[Payload] = [h.borrow()]\n"
            + "    print(len(xs))\n"
        )
        assert any(_COPIES in w for w in warnings)

    def test_owning_call_source_does_not_warn(self):
        # BOUNDARY: an Own-returning call is a fresh value, not a borrow.
        warnings = _warnings(
            _HOLDER
            + "def make() -> Own[Payload]:\n"
            + "    return Payload(1)\n"
            + "def keep(p: Own[Payload]) -> int32:\n"
            + "    return p.v\n"
            + "def use() -> None:\n"
            + "    print(keep(make()))\n"
        )
        assert not any(_COPIES in w for w in warnings)
