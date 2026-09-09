# A BORROW-returning getter at a generic RECORD METHOD's `T` slot binds BARE --
# no temp, no copy -- so a mutation the method makes through that parameter is
# visible to the caller's original, like CPython. The FREE-function twin of the
# same argument is refused: BUGS.md#generic-free-call-borrow-return-arg-rejects,
# pinned by `generics/error_generic_free_call_borrow_arg`.
# `T` is BOUND here only so the body can call a member and mutate: the alias is
# observed through that mutation, and an unbounded `T` could not touch one. The
# reject the twin pins is bound-independent, which is why it spells a plain
# `f[T]`.
# The method stays `-> None` because the return-through form belongs to two
# other entries: returning the parameter warns about a temporary that is really
# a borrow, BUGS.md#generic-return-through-param-spurious-temporary-warning,
# and binding a generic method's `T` return to a caller local emits an
# ill-formed non-const alias,
# BUGS.md#generic-method-inferred-readonly-return-binding.
from __future__ import annotations
from typing import Protocol
from tpy import Int32


class Bump(Protocol):
    def bump(self) -> None: ...


class Cell:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n = self.n + 1


class Holder:
    c: Cell

    def __init__(self, n: Int32) -> None:
        self.c = Cell(n)

    def borrow(self) -> Cell:
        return self.c


class Bumper[T: Bump]:
    def bump_it(self, v: T) -> None:
        v.bump()


# free function
def in_free_function() -> None:
    h = Holder(1)
    b = Bumper[Cell]()
    # The subject: the `T&` getter result binds the method's T slot bare, so
    # the bump lands on the holder's own cell.
    b.bump_it(h.borrow())  # tpyc: ok
    print("free_function", h.c.n)


# method body
class Owner:
    h: Holder

    def __init__(self) -> None:
        self.h = Holder(10)

    def run(self) -> None:
        b = Bumper[Cell]()
        b.bump_it(self.h.borrow())  # tpyc: ok
        print("method", self.h.c.n)


# loop body: the same bind once per iteration accumulates on the one cell.
def in_loop() -> None:
    h = Holder(20)
    b = Bumper[Cell]()
    for i in range(3):
        b.bump_it(h.borrow())  # tpyc: ok
        print("in_loop", i, h.c.n)


# try/finally body
def in_try_finally() -> None:
    h = Holder(30)
    b = Bumper[Cell]()
    try:
        b.bump_it(h.borrow())  # tpyc: ok
    finally:
        print("try_finally", h.c.n)


def main() -> None:
    in_free_function()
    Owner().run()
    in_loop()
    in_try_finally()


main()
