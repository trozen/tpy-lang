"""The dyn-own FORWARD verdict at a SUBSTITUTED `Own[@dynamic P]` slot.

When the source call's declared return IS the erased protocol, the result is
already `unique_ptr<P>`-shaped and renders bare -- no adapter, no
make_unique, and nothing the substitution changes. The concrete free-call
ladder carried the row; the generic one did not. Corpus witness: asyncio's
`_accept_loop` (`create_task(cb(StreamReader(..), StreamWriter(..)))`)."""

from .testutil import (
    _reject_tally, _assert_byte_identical, _assert_rejects_at,
                       _assert_routes_byte_identical, _compile, _entry)
from ..codegen_cpp import CodeGenOptions


_HEAD = ("from typing import Callable\n"
         "from tpy import Int32, Own, nocopy\n"
         "from tpy.coro import Cancellable, Waker, Poll, poll_ready\n"
         "def spawn[T](c: Own[Cancellable[T]]) -> None:\n"
         "    c.cancel()\n")


def _reject_tags(src: str):
    return _reject_tally(src)


class TestGenericDynOwnForward:
    FORWARD = (_HEAD
               + "def go(cb: Callable[[Int32], Own[Cancellable[None]]]"
               + ") -> None:\n"
               + "    spawn(cb(1))\n"
               + "def main() -> None:\n"
               + "    print(1)\n"
               + "main()\n")

    def test_forward_at_substituted_slot_routes(self):
        _assert_routes_byte_identical(self.FORWARD)

    def test_concrete_callee_sibling_still_routes(self):
        src = (_HEAD
               + "def spawn0(c: Own[Cancellable[None]]) -> None:\n"
               + "    c.cancel()\n"
               + "def go(cb: Callable[[Int32], Own[Cancellable[None]]]"
               + ") -> None:\n"
               + "    spawn0(cb(1))\n"
               + "def main() -> None:\n"
               + "    print(1)\n"
               + "main()\n")
        _assert_routes_byte_identical(src)


class TestGenericDynOwnForwardBoundary:
    def test_conformer_rvalue_keeps_rejecting(self):
        # A CONFORMER source is a different verdict: its vtable rides an
        # adapter wrap, so passing it bare would be silent wrong code.
        src = (_HEAD
               + "@nocopy\n"
               + "class Job:\n"
               + "    n: Int32\n"
               + "    def __init__(self, n: Int32) -> None:\n"
               + "        self.n = n\n"
               + "    def __poll__(self, waker: Waker) -> Own[Poll[None]]:\n"
               + "        return poll_ready(None)\n"
               + "    def cancel(self) -> None:\n"
               + "        self.n = 0\n"
               + "def mk(n: Int32) -> Own[Job]:\n"
               + "    return Job(n)\n"
               + "def go() -> None:\n"
               + "    spawn(mk(1))\n"
               + "def main() -> None:\n"
               + "    print(1)\n"
               + "main()\n")
        _assert_rejects_at(_reject_tally(src),
                           "body:expr.call:call.generic_arg_shape")
