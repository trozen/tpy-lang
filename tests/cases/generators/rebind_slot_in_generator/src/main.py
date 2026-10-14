# A rebind slot (a record local reassigned to a fresh rvalue) inside a
# generator body: the slot is a frame field, so it has the same home whether
# the local is declared inside the loop (while / for) or BEFORE the loop and
# rebound inside it, and whether an earlier same-scope rebind already drained
# its declaration. The alias-clobber shape (an alias taken before the rebind)
# is pinned by records/warn_alias_rebind_clobber.
from tpy import Ptr, int32, take_ptr
from typing import Iterator


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def bump(self) -> None:
        self.x += 1


def decl_inside_while(n: int32) -> Iterator[int32]:
    i = 0
    while i < n:
        p = Point(0)
        p = Point(i)
        yield p.x
        i += 1


def decl_inside_for(xs: list[int32]) -> Iterator[int32]:
    for v in xs:
        p = Point(0)
        p = Point(v)
        yield p.x


# declared BEFORE the loop, rebound inside it
def before_while(n: int32) -> Iterator[int32]:
    p = Point(11)
    i = 0
    while i < n:
        p = Point(i)  # tpyc: ok
        yield p.x
        i += 1


# ... with an earlier same-scope rebind that already drained the declaration
def before_while_after_drain(n: int32) -> Iterator[int32]:
    p = Point(11)
    p = Point(12)
    i = 0
    while i < n:
        p = Point(i)  # tpyc: ok
        yield p.x
        i += 1


# a rebound record local that is yielded: the consumer's reference reaches
# the object the frame holds, so its mutation shows in the generator
def yield_rebound() -> Iterator[Point]:
    p = Point(1)
    yield p
    print("yield_rebound gen:", p.x)
    p = Point(10)  # tpyc: ok
    yield p
    print("yield_rebound gen:", p.x)


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


# the consumer keeps the first yielded object across the next pull: the
# rebind at its own site takes a frame field of its own, so the first object
# survives it
def yield_kept_across_pull() -> Iterator[Cell]:
    c = Cell(1)
    yield c
    c = Cell(2)  # tpyc: ok
    yield c


# the consumer's handle on the first object, kept across the next pull (a
# `Ptr`: a loop variable cannot outlive its pass, and a reference-typed
# `next()` result does not lower yet)
def kept_across_pull() -> None:
    first: Ptr[Cell] = None
    for c in yield_kept_across_pull():
        if first is None:
            first = take_ptr(c)
        else:
            first.v += 100
            print("yield_kept_across_pull:", first.v, c.v)


def main() -> None:
    for got in decl_inside_while(3):
        print("while:", got)

    for got in decl_inside_for([1, 2, 3]):
        print("for:", got)

    for got in before_while(2):
        print("before_while:", got)

    for got in before_while_after_drain(2):
        print("after_drain:", got)

    for q in yield_rebound():
        q.bump()
        print("yield_rebound:", q.x)

    kept_across_pull()


main()
