# A tuple bound to a local of a resumable frame: ownership of each element comes
# from what the INIT does with it, not from the tuple type. A fresh element (a
# literal's rvalue, or any element of an owning call's result) is storage the
# frame owns; an lvalue element stays a pointer at the caller's object, so a
# mutation made after the suspension is read back through the tuple. The
# lvalue-only sections pin the borrow field as unchanged.
import asyncio
from typing import Iterator

from tpy import Int32, Own


class A:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def make() -> Own[A]:
    return A(7)


def make_pair() -> Own[tuple[A, Int32]]:
    return (A(7), 3)


# per-element ownership in the return type, not `Own[tuple[...]]`
def own_elem_pair() -> tuple[Own[A], Int32]:
    return (A(7), 3)


# all-fresh literal: the frame owns both elements
def fresh() -> Iterator[Int32]:
    t = (A(1), 2)  # tpyc: ok
    yield t[0].v
    t[0].v = 42
    yield t[0].v + t[1]


# an Own-returning call is a fresh element too
def own_call() -> Iterator[Int32]:
    t = (1, make())  # tpyc: ok
    yield t[1].v
    t[1].v = 42
    yield t[1].v


# plain lvalue element: the frame points at the caller's object, so the
# caller's later mutation is visible through the tuple
def lvalue(a: A) -> Iterator[Int32]:
    t = (a, 2)  # tpyc: ok
    yield t[0].v
    a.v = 99
    yield t[0].v


# mixed, fresh element first: element 0 is owned, element 1 aliases `a`
def mixed_fresh_first(a: A) -> Iterator[Int32]:
    t = (A(1), a)  # tpyc: ok
    yield t[0].v
    a.v = 99
    yield t[1].v


# mixed, lvalue element first -- the same verdict, other order
def mixed_lvalue_first(a: A) -> Iterator[Int32]:
    t = (a, A(1))  # tpyc: ok
    yield t[1].v
    a.v = 99
    yield t[0].v


# a fresh local dead after the literal is MOVED into the owned element
def moved_last_use() -> Iterator[Int32]:
    a = A(1)
    t = (A(2), a)  # tpyc: ok
    yield t[0].v
    yield t[1].v


# reassigned once per iteration: the per-element verdict joins across inits
def loop_reassigned() -> Iterator[Int32]:
    i = 0
    while i < 2:
        t = (i, A(i * 10))  # tpyc: ok
        yield t[0]
        t[1].v += 1
        yield t[1].v
        i += 1


# a fresh literal in one branch and an OWNING CALL in the other: both hand the
# frame the element storage, so the join agrees and one owning slot serves both
def literal_then_call(c: bool) -> Iterator[Int32]:
    if c:
        t = (A(1), 2)  # tpyc: ok
    else:
        t = make_pair()  # tpyc: ok
    yield t[0].v
    t[0].v = 42
    yield t[0].v + t[1]


# the same two inits, other order -- the join is order-independent
def call_then_literal(c: bool) -> Iterator[Int32]:
    if c:
        t = make_pair()  # tpyc: ok
    else:
        t = (A(1), 2)  # tpyc: ok
    yield t[0].v
    t[0].v = 42
    yield t[0].v + t[1]


# an owning call REASSIGNED across a suspension: every init emplaces fresh
# storage into the one slot, so the second binding is not a dangling borrow
def call_reassigned() -> Iterator[Int32]:
    t = make_pair()  # tpyc: ok
    yield t[0].v
    t = make_pair()
    t[0].v += 1
    yield t[0].v


# a call whose return type spells ownership PER ELEMENT (`tuple[Own[A], Int32]`)
# rather than over the whole tuple: the Own slot is the frame's, so the
# post-suspension mutation lands in the frame's own storage
def own_elem_call() -> Iterator[Int32]:
    t = own_elem_pair()  # tpyc: ok
    yield t[0].v
    t[0].v = 42
    yield t[0].v + t[1]


# branch-nested decl: the try body's arm takes the same classification
def try_body() -> Iterator[Int32]:
    try:
        t = (A(1), 2)  # tpyc: ok
        yield t[0].v
        t[0].v = 42
        yield t[0].v
    finally:
        print("try_body finally")


class H:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    # generator method: same classification behind a receiver
    def g(self) -> Iterator[Int32]:
        t = (A(self.n), 2)  # tpyc: ok
        yield t[0].v
        t[0].v = 42
        yield t[0].v


# async def: the coroutine frame takes the same slot
async def coro(a: A) -> Int32:
    t = (A(1), a)  # tpyc: ok
    await asyncio.sleep(0)
    t[0].v = 42
    return t[0].v + t[1].v


async def async_section() -> None:
    a = A(5)
    print("async", await coro(a))


def main() -> None:
    for n in fresh():
        print("fresh", n)

    for n in own_call():
        print("own_call", n)

    a = A(1)
    for n in lvalue(a):
        print("lvalue", n)

    b = A(5)
    for n in mixed_fresh_first(b):
        print("mixed_fresh_first", n)

    c = A(5)
    for n in mixed_lvalue_first(c):
        print("mixed_lvalue_first", n)

    for n in moved_last_use():
        print("moved", n)

    for n in loop_reassigned():
        print("loop", n)

    for n in literal_then_call(True):
        print("literal_then_call literal", n)

    for n in literal_then_call(False):
        print("literal_then_call call", n)

    for n in call_then_literal(True):
        print("call_then_literal call", n)

    for n in call_then_literal(False):
        print("call_then_literal literal", n)

    for n in call_reassigned():
        print("call_reassigned", n)

    for n in own_elem_call():
        print("own_elem_call", n)

    for n in try_body():
        print("try", n)

    h = H(1)
    for n in h.g():
        print("method", n)

    asyncio.run(async_section())


main()
