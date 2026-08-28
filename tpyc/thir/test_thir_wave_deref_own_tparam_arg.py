"""An `Own[T]`-returning call rvalue at an open `Own[T]` slot reached through
a `__deref__()` receiver. The prvalue binds the `T&&` slot bare, so the
qualified marker family renders it exactly as the record-method family does at
the same slot -- but a BORROW-returning callee stays out: its `val_or_ref_t<T>`
result is not an rvalue the slot may bind, and the callee's DECLARED return is
the only thing that separates the two."""

from __future__ import annotations

from .testutil import (
    _assert_byte_identical, _assert_rejects_at, _assert_routes_byte_identical,
    _lower_ctx_witnessed, _thir_ctx,
)

_PRELUDE = (
    "from tpy import Own, Int32, nocopy\n"
    "from tpy.mem import UninitStorage\n"
    "from tplib.rc import Rc\n"
    "@nocopy\n"
    "class Cell[T]:\n"
    "    _slot: UninitStorage[T]\n"
    "    def __init__(self) -> None:\n"
    "        self._slot = UninitStorage[T]()\n"
    "    def put(self, value: Own[T]) -> None:\n"
    "        self._slot.construct(value)\n"
    "    def drop(self) -> None:\n"
    "        if self._slot.has():\n"
    "            self._slot.reset()\n"
    "@nocopy\n"
    "class Holder[T]:\n"
    "    _v: T\n"
    "    def __init__(self, v: Own[T]) -> None:\n"
    "        self._v = v\n"
    "    def peek(self) -> T:\n"
    "        return self._v\n"
    "def dup[T](v: Own[T]) -> Own[T]:\n"
    "    return v\n"
    "@nocopy\n"
    "class Payload:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "@nocopy\n"
    "class Mover[T]:\n"
    "    _cell: Rc[Cell[T]]\n"
    "    _src: UninitStorage[T]\n"
    "    def __init__(self, cell: Own[Rc[Cell[T]]]) -> None:\n"
    "        self._cell = cell\n"
    "        self._src = UninitStorage[T]()\n"
    "    def drop_all(self) -> None:\n"
    "        self._cell.drop()\n")

_MAIN = ("def main() -> None:\n"
         "    c = Rc.new(Cell[Payload]())\n"
         "    m = Mover[Payload](c.clone())\n"
         "    m.drop_all()\n"
         "main()\n")


def _src(body: str) -> str:
    return _PRELUDE + body + _MAIN


class TestDerefOwnTparamArg:

    def test_own_returning_method_call_routes(self):
        # `self._state._push(self._value.take())` -- the channel send shape.
        src = _src("    def go(self) -> None:\n"
                   "        self._cell.put(self._src.take())\n")
        hpp = _assert_routes_byte_identical(src)[0]
        assert "__deref__().put(this->_src.take())" in hpp

    def test_the_row_is_the_one_that_admits_it(self):
        src = _src("    def go(self) -> None:\n"
                   "        self._cell.put(self._src.take())\n")
        _, faces = _lower_ctx_witnessed(src)
        assert faces.get("arg.own_tparam_call_rvalue", 0) >= 1

    def test_own_returning_free_call_routes(self):
        # Same slot, free callee: an open T fixes the render at
        # instantiation, so the callee kind cannot change it.
        src = _src("    def go(self) -> None:\n"
                   "        self._cell.put(dup(self._src.take()))\n")
        _assert_routes_byte_identical(src)

    def test_borrow_returning_call_stays_ast(self):
        # `peek()` returns a borrowed `val_or_ref_t<T>`, which the AST binds
        # straight into the `T&&` slot -- ill-formed at any reference-type
        # instantiation, so nothing may route it. `is_rvalue_source` answers
        # True for both callees; only the DECLARED return separates them.
        src = _src("    def go(self, h: Holder[T]) -> None:\n"
                   "        self._cell.put(h.peek())\n")
        _, fallback = _thir_ctx(src)
        _assert_rejects_at(fallback, "body:expr.method_call",
                           "method.qualcall.arg.own")
        _assert_byte_identical(src)
