# A generator (single yield in a tail loop) that yields a borrow of a local
# declared INSIDE the loop body: the borrow must survive the suspension, so the
# local has to live on the resumable frame rather than on the `__next__` stack,
# where it would dangle. Consumer mutation must be visible to the generator on
# resume (aliasing), matching CPython.
from typing import Iterator
from tpy import int32


def gen() -> Iterator[list[int32]]:
    i = 0
    while i < 2:
        buf: list[int32] = []
        buf.append(10)
        buf.append(20)
        yield buf                 # tpyc: ok
        print("resume sees len", len(buf))
        i += 1


# Tuple of loop-body locals (the os.walk shape): yield (key, vals); the consumer
# prunes vals in place and the generator observes the prune on resume.
def walk() -> Iterator[tuple[int32, list[int32]]]:
    stack: list[int32] = []
    stack.append(2)
    while len(stack) > 0:
        cur = stack.pop()
        kids: list[int32] = []
        if cur > 0:
            kids.append(cur - 1)
            kids.append(cur - 1)
        yield (cur, kids)         # tpyc: ok
        for k in kids:
            stack.append(k)


# Same hazard with a `for range` loop instead of a `while`.
def gen_range() -> Iterator[list[int32]]:
    for _ in range(2):
        buf: list[int32] = []
        buf.append(1)
        yield buf                 # tpyc: ok
        print("range resume len", len(buf))


# The borrow root can hide behind a ternary or walrus; both must still keep the
# loop-body local alive on the frame across the yield.
def gen_ternary(flag: bool) -> Iterator[list[int32]]:
    i = 0
    while i < 2:
        a: list[int32] = []
        a.append(7)
        b: list[int32] = []
        b.append(8)
        yield (a if flag else b)  # tpyc: ok
        print("ternary resume", len(a if flag else b))
        i += 1


def gen_walrus() -> Iterator[list[int32]]:
    i = 0
    while i < 2:
        buf: list[int32] = []
        buf.append(3)
        yield (x := buf)          # tpyc: ok
        print("walrus resume", len(buf))
        i += 1


# A generator METHOD yielding a loop-body local: same drain/eligibility path.
class Source:
    def gen(self) -> Iterator[list[int32]]:
        i = 0
        while i < 2:
            buf: list[int32] = []
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
