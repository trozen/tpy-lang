"""A BORROW-returning record call as a USER-RECORD METHOD argument.

The `T&`-returning call binds a plain record ref slot directly -- no temp,
no copy -- exactly as it does at a marker callee's slot, so both families
decide the shape in one cell. An OWN-returning call at the same slot is a
prvalue the AST hoists through a temp and keeps rejecting.
"""

from .testutil import (
    _reject_tally,
    _assert_rejects_at,
    _assert_routes_byte_identical,
    _compile,
    _entry,
    _lower_ctx_witnessed,
)
from ..codegen_cpp.context import CodeGenOptions

_PRE = (
    "from tpy import Int32, Own\n"
    "class Logger:\n"
    "    n: Int32\n"
    "    def __init__(self, n: Int32) -> None:\n"
    "        self.n = n\n"
    "class Holder:\n"
    "    log: Logger\n"
    "    def __init__(self, log: Own[Logger]) -> None:\n"
    "        self.log = log\n"
    "    def borrow(self) -> Logger:\n"
    "        return self.log\n"
    "class Sink:\n"
    "    def absorb(self, lg: Logger) -> Int32:\n"
    "        return lg.n\n"
)


class TestBorrowRetRecordCallAtMethodSlot:
    SRC = (
        _PRE +
        "def main() -> None:\n"
        "    h = Holder(Logger(7))\n"
        "    s = Sink()\n"
        "    print(s.absorb(h.borrow()))\n"
        "main()\n"
    )

    def test_routes_byte_identical(self):
        _assert_routes_byte_identical(self.SRC)

    def test_witnesses_the_borrow_ret_cell(self):
        _thir, witnesses = _lower_ctx_witnessed(self.SRC)
        assert witnesses.get("arg.record_borrow_ret_marker", 0) >= 1, witnesses


class TestOwnReturningCallAtSameSlotKeepsRejecting:
    SRC = (
        _PRE +
        "class Factory:\n"
        "    def make(self) -> Own[Logger]:\n"
        "        return Logger(3)\n"
        "def main() -> None:\n"
        "    f = Factory()\n"
        "    s = Sink()\n"
        "    print(s.absorb(f.make()))\n"
        "main()\n"
    )

    def test_rejects_at_the_arg_shape(self):
        _assert_rejects_at(_reject_tally(self.SRC), 'body:stmt.expr_stmt', shape='method.arg_shape')
