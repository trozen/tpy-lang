# A simple-shape generator (single yield in a tail loop) that yields a borrow of
# a local declared INSIDE the loop body must route to the resumable path: the
# lambda peephole would keep that local on the lambda stack and the yielded
# borrow would dangle. Consumer mutation must be visible to the generator on
# resume (aliasing), matching CPython.
from typing import Iterator
from tpy import Int32


def gen() -> Iterator[list[Int32]]:
    i = 0
    while i < 2:
        buf: list[Int32] = []
        buf.append(10)
        buf.append(20)
        yield buf                 # tpyc: ok
        print("resume sees len", len(buf))
        i += 1


# Tuple of loop-body locals (the os.walk shape): yield (key, vals); the consumer
# prunes vals in place and the generator observes the prune on resume.
def walk() -> Iterator[tuple[Int32, list[Int32]]]:
    stack: list[Int32] = []
    stack.append(2)
    while len(stack) > 0:
        cur = stack.pop()
        kids: list[Int32] = []
        if cur > 0:
            kids.append(cur - 1)
            kids.append(cur - 1)
        yield (cur, kids)         # tpyc: ok
        for k in kids:
            stack.append(k)


# Same hazard on the for-range peephole branch (sibling of the while branch).
def gen_range() -> Iterator[list[Int32]]:
    for _ in range(2):
        buf: list[Int32] = []
        buf.append(1)
        yield buf                 # tpyc: ok
        print("range resume len", len(buf))


# The borrow root can hide behind a ternary or walrus; both must still route to
# the resumable path (the peephole would dangle the loop-body local).
def gen_ternary(flag: bool) -> Iterator[list[Int32]]:
    i = 0
    while i < 2:
        a: list[Int32] = []
        a.append(7)
        b: list[Int32] = []
        b.append(8)
        yield (a if flag else b)  # tpyc: ok
        print("ternary resume", len(a if flag else b))
        i += 1


def gen_walrus() -> Iterator[list[Int32]]:
    i = 0
    while i < 2:
        buf: list[Int32] = []
        buf.append(3)
        yield (x := buf)          # tpyc: ok
        print("walrus resume", len(buf))
        i += 1


# A generator METHOD yielding a loop-body local: same drain/eligibility path.
class Source:
    def gen(self) -> Iterator[list[Int32]]:
        i = 0
        while i < 2:
            buf: list[Int32] = []
            buf.append(5)
            yield buf             # tpyc: ok
            print("method resume", len(buf))
            i += 1


def main() -> None:
    seen = 0
    for v in gen():
        seen += 1
        v[:] = []                 # prune: generator's resume sees len 0

    for w in gen_range():
        w[:] = []

    for t in gen_ternary(True):
        t[:] = []

    for u in gen_walrus():
        u[:] = []

    s = Source()
    for m in s.gen():
        m[:] = []

    for level, kids in walk():
        print("level", level, "kids", len(kids))
        if level == 1:
            kids.clear()          # stop descending past level 1


main()
