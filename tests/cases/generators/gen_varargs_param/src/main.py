# A `*args` pack is an admitted resumable-frame parameter: the frame field is
# the same `varargs<E>` view the sync signature spells, and a suspending loop
# over the pack aliases its elements (`const E*` / `E*`) instead of copying
# them into the frame. Covers every element family and every body position.
from typing import Iterator, Protocol

from tpy import Comparable, int32, readonly


class Counter(Protocol):
    """Bound for the bare-`T` pack element, so its body can touch the element.

    A class-bounded type param cannot -- member access on it is rejected
    (BUGS.md#class-bounded-typeparam-member-access) -- and an unbounded one
    has no members.
    """

    def bump(self) -> int32: ...


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def bump(self) -> int32:
        self.x += 1
        return self.x


class Trace:
    """Plain context manager, so the `with` section needs no TPy-only type."""

    def __enter__(self) -> "Trace":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        print("with: exit")
        return False


# free function / scalar element: the pack is a value-element varargs.
def scalars(*xs: int32) -> Iterator[int32]:  # tpyc: ok
    n = 0
    for x in xs:
        n += x
        yield n
    yield -1


# free function / str element: the pack views the caller's string_view array.
def strings(*ss: str) -> Iterator[int32]:  # tpyc: ok
    n = 0
    for s in ss:
        n += len(s)
        yield n
    yield -1


# free function / container element in the `heapq.merge` shape: a
# NON-suspending loop over the pack, then a suspending `while`.
def merge_shape[T: Comparable](*xs: list[T]) -> Iterator[int32]:  # tpyc: ok
    total = 0
    for s in xs:
        total += len(s)
    i = 0
    while i < total:
        yield i
        i += 1
    yield -1


# free function / record element, MUTATED through a suspending loop: the loop
# var must alias the caller's record, so the mutation is visible afterwards.
def bump(*ps: Point) -> Iterator[int32]:  # tpyc: ok
    for p in ps:
        p.x += 1
        yield p.x
    yield -1


# free function / record element, read-only: sema flips the pack to
# `varargs[readonly[Point]]`, so the loop var is a `const Point*` borrow --
# a source mutated between two pulls must be seen on the next pull.
def read_pack(*ps: Point) -> Iterator[int32]:  # tpyc: ok
    for p in ps:
        yield p.x
    yield -1


# generator METHOD: the pack rides beside the `self` capture.
class Collector:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    def sizes(self, *xs: list[int32]) -> Iterator[int32]:  # tpyc: ok
        for s in xs:
            yield self.base + len(s)
        yield -1


def total_of(*xs: int32) -> int32:
    n = 0
    for x in xs:
        n += x
    return n


# whole-pack forward out of the frame.
def forward(*xs: int32) -> Iterator[int32]:  # tpyc: ok
    yield total_of(*xs)
    yield -1


# subscript + len on the frame's pack (the reads `heapq.merge` makes).
def indexed(*xs: int32) -> Iterator[int32]:  # tpyc: ok
    i = 0
    while i < len(xs):
        yield xs[i]
        i += 1
    yield -1


# a suspending pack loop inside try/finally.
def in_finally(*xs: list[int32]) -> Iterator[int32]:  # tpyc: ok
    try:
        for s in xs:
            yield len(s)
    finally:
        print("tryfinally: cleanup")
    yield -1


# a suspending pack loop inside a `with` body.
def in_with(*xs: list[int32]) -> Iterator[int32]:  # tpyc: ok
    with Trace():
        for s in xs:
            yield len(s)
    yield -1


# Sibling shape with no varargs: a `readonly` CONTAINER param puts the const on
# the source, not the element, and the frame loop var must take it from there.
def readonly_param(ps: readonly[list[Point]]) -> Iterator[int32]:  # tpyc: ok
    for p in ps:
        yield p.x
    yield -1


# Second no-varargs sibling: an inferred-`@readonly` receiver makes the
# `self` field a const container, so the const comes from the SOURCE's
# root rather than from the element's own type.
class Album:
    items: list[Point]

    def __init__(self) -> None:
        self.items = [Point(1), Point(2)]

    def each(self) -> Iterator[int32]:  # tpyc: ok
        for p in self.items:
            yield p.x
            yield p.x + 100


# free function / bare generic pack element: the frame loop var borrows the
# element instead of copying it into an owning slot, so the bump is seen by
# the caller. The bound is a protocol so the body can call through `T`.
def bump_generic[T: Counter](*xs: T) -> Iterator[int32]:  # tpyc: ok
    for x in xs:
        yield x.bump()
    yield -1


# Producer for the `next`-strategy section: a `readonly` element yielded from a
# frame (two yields keep it off the simple-generator peephole).
def points(ps: readonly[list[Point]]) -> Iterator[readonly[Point]]:  # tpyc: ok
    for p in ps:
        yield p
    for p in ps:
        yield p


# `next` strategy (an `Iterator[T]` protocol source) with a readonly element:
# the loop var is a `const Point*` reaching through the producer's yield slot
# to the ORIGINAL list. Read twice around a suspension so the caller can mutate
# the source in between: a copying slot would repeat the first read.
def readonly_next(it: Iterator[readonly[Point]]) -> Iterator[int32]:  # tpyc: ok
    for p in it:
        # `yield p.x` copies an int32, but the ephemeral-borrow escape check
        # roots on `p` and refuses it -- BUGS.md#ephemeral-value-read-escape.
        before = p.x
        yield before
        # Same borrow, after the caller's mutation of the source.
        after = p.x
        yield after
    yield -1


# Producer for the multi-root section: the pack element is yielded straight
# out, so the consumer's loop var borrows EVERY operand of the one pack slot.
def each_pack(*xs: list[list[int32]]) -> Iterator[list[list[int32]]]:  # tpyc: ok
    for s in xs:
        yield s


# Consumer whose two params are the pack's operands: a structural mutation
# through the loop var must be recorded against BOTH of them, not just the
# last operand of the slot.
def grow_both(p: list[list[int32]], q: list[list[int32]]) -> None:  # tpyc: ok
    for v in each_pack(p, q):
        v.append([9])


# METHOD flavour of the same climb: the pack's operands are the enclosing
# METHOD's params, so the mutation has to be recorded self-relative against
# both of them -- the generator producing the pack is a method too.
class Grower:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag

    def each_pack(self, *xs: list[list[int32]]) -> Iterator[list[list[int32]]]:  # tpyc: ok
        for s in xs:
            yield s

    def grow_both(self, p: list[list[int32]], q: list[list[int32]]) -> None:  # tpyc: ok
        for v in self.each_pack(p, q):
            v.append([self.tag])


def main() -> None:
    for v in scalars(1, 2, 3):
        print("scalar:", v)

    for v in strings("ab", "cde"):
        print("str:", v)

    # Annotated: a bare list literal infers Array[int32, N], not list.
    la: list[int32] = [1, 2]
    lb: list[int32] = [3]
    for v in merge_shape(la, lb):
        print("container:", v)

    a = Point(1)
    b = Point(2)
    for v in bump(a, b):
        print("record-mut:", v)
    print("record-mut: after", a.x, b.x)

    c = Point(1)
    d = Point(2)
    for v in read_pack(c, d):
        print("record-ro:", v)
        # Mutate the source between two pulls: a copying frame would keep 2.
        # The pack borrow now reaches every element, so the mutation-while-
        # borrowed net fires here exactly as it does for a plain param.
        d.x = 20  # tpyc: warning(/while borrowed/)

    # Own lists per suspending section: each grows its second element between
    # two pulls, so a frame that COPIED the element would print the stale
    # length on the second pull.
    ma: list[int32] = [1]
    mb: list[int32] = [3]
    coll = Collector(10)
    for v in coll.sizes(ma, mb):
        print("method:", v)
        mb.append(0)  # tpyc: warning(/while iterating/)

    for v in forward(4, 6):
        print("forward:", v)

    for v in indexed(7, 8):
        print("subscript:", v)

    fa: list[int32] = [1]
    fb: list[int32] = [3]
    for v in in_finally(fa, fb):
        print("tryfinally:", v)
        fb.append(0)  # tpyc: warning(/while iterating/)

    wa: list[int32] = [1]
    wb: list[int32] = [3]
    for v in in_with(wa, wb):
        print("with:", v)
        wb.append(0)  # tpyc: warning(/while iterating/)

    pts: list[Point] = [Point(5), Point(6)]
    for v in readonly_param(pts):
        print("readonly-param:", v)
        # Mutate the source between pulls: a copying loop var would keep 6.
        pts[1].x = 60

    e = Point(1)
    f = Point(2)
    for v in bump_generic(e, f):
        print("generic:", v)
    print("generic: after", e.x, f.x)

    alb = Album()
    for v in alb.each():
        print("self-field:", v)
        # Mutate the source between pulls: a copying loop var would keep 2.
        alb.items[1].x = 60

    rp: list[Point] = [Point(7)]
    pulls = 0
    for v in readonly_next(points(rp)):
        print("readonly-next:", v)
        pulls += 1
        if pulls == 1:
            # Mutate the SOURCE while the callee's element borrow is live: the
            # next read through `p` sees 70, where a copying yield slot would
            # print 7 again.
            rp[0].x = 70

    ga: list[list[int32]] = [[1]]
    gb: list[list[int32]] = [[2]]
    ea = ga[0]
    eb = gb[0]
    print("multi-root:", len(ea), len(eb))
    # Both operands of the pack slot are structurally mutated through the
    # callee's loop var, so BOTH outstanding element borrows are flagged --
    # not only the slot's last operand.
    grow_both(ga, gb)  # tpyc: warning(/borrowed container 'ga'/) warning(/borrowed container 'gb'/)
    print("multi-root:", len(ga), len(gb))
    # Same call with the operands swapped: a slot that kept only its LAST
    # source would flag a different single operand here, so the both-warn
    # result cannot come from operand order.
    grow_both(gb, ga)  # tpyc: warning(/borrowed container 'gb'/) warning(/borrowed container 'ga'/)
    print("multi-root swapped:", len(ga), len(gb))

    gr = Grower(7)
    ha: list[list[int32]] = [[1]]
    hb: list[list[int32]] = [[2]]
    ha_e = ha[0]
    hb_e = hb[0]
    print("method-multi-root:", len(ha_e), len(hb_e))
    # Both of the METHOD's params are structurally mutated through its loop
    # var, so both outstanding element borrows are flagged at this call.
    gr.grow_both(ha, hb)  # tpyc: warning(/borrowed container 'ha'/) warning(/borrowed container 'hb'/)
    print("method-multi-root:", len(ha), len(hb))


main()
