# A rebind in a loop whose own value keeps the element it replaces reuses the
# site's slot on the next pass: warned, and the carried element reads the new
# object (BUGS.md#loop-rebind-site-slot-reuse-warned). CPython prints
# `10 0`, `11 10`, `12 11`. A generator's loop reuses its site's frame field
# the same way: CPython prints `gen-loop-alias 110` and `gen-loop-alias 11`,
# and `holder-loop [1, 10, 20] 30`. The nested-loop sections rebind at the
# inner loop's last element, so they print CPython's values; holder-two-hop-loop
# rebinds on its first pass only, for the same reason.
import asyncio
from typing import Iterator

from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class N:
    ns: list[int32]

    def __init__(self, i: int32) -> None:
        self.ns = [i, i + 1]


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def loop_retain(v: Box) -> None:
    p = make_mixed(0, v)
    for i in range(3):
        # The rebind's own value keeps the old element 0 as its element 1.
        p = make_mixed(10 + i, p[0])  # tpyc: warning(/'p' will not keep the object it carries over from its previous value/)
        print("loop-retain", p[0].n, p[1].n)


loop_retain(Box(5))


# generator: the alias rebound in the loop body reads the site's one field,
# which the next pass overwrites -- a WARNED wrong value (`111 111`, CPython
# `110 11`; BUGS.md#loop-rebind-site-slot-reuse-warned)
def gen_loop_alias(v: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    r = p
    for i in range(2):
        r = p
        p = make_mixed(10 + i, v)  # tpyc: warning(/'r' will not keep the object it was given/)
    r[0].n += 100
    yield r[0].n
    yield p[0].n


# an element alias bound on each pass, then the rebind on the same pass: the
# next pass writes into the slot the alias still holds
def holder_loop() -> None:
    t = (N(1), 5)
    out: list[int32] = []
    for k in range(3):
        a = t[0]
        t = (N(10 * (k + 1)), 6)  # tpyc: warning(/'a' will not keep the object it was given/)
        out.append(a.ns[0])
    print("holder-loop", out, t[0].ns[0])


# a rebind inside an inner loop over the element: on the next outer pass the
# inner loop iterates the object of the rebind's own slot, which the rebind
# then replaces (it comes at the last element, so no read follows it here)
def nested_loop_rebind(c: bool) -> None:
    t = (N(1), 5)
    out: list[int32] = []
    for k in range(3):
        for i, x in enumerate(t[0].ns):
            out.append(x)
            if c and i == 1:
                t = (N(10 * (k + 1)), 6)  # tpyc: warning(/the loop over `enumerate\(t\[0\]\.ns\)` will not keep iterating the object it was given/)
    print("nested-loop", out)


def gen_nested_loop_rebind(c: bool) -> Iterator[int32]:
    t = (N(1), 5)
    for k in range(3):
        for i, x in enumerate(t[0].ns):
            yield x
            if c and i == 1:
                t = (N(10 * (k + 1)), 6)  # tpyc: warning(/the loop over `enumerate\(t\[0\]\.ns\)` will not keep iterating the object it was given/)


async def pause(n: int32) -> int32:
    return n


# the coroutine face: the inner loop's iterator is held across an await
async def co_nested_loop_rebind(c: bool) -> None:
    t = (N(1), 5)
    out: list[int32] = []
    for k in range(3):
        for i, x in enumerate(t[0].ns):
            out.append(await pause(x))
            if c and i == 1:
                t = (N(10 * (k + 1)), 6)  # tpyc: warning(/the loop over `enumerate\(t\[0\]\.ns\)` will not keep iterating the object it was given/)
    print("co-nested-loop", out)


# the scalar face: a record local rebound inside an inner loop over its field
def scalar_nested_loop_rebind(c: bool) -> None:
    p = N(1)
    out: list[int32] = []
    for k in range(3):
        j = 0
        for x in p.ns:
            out.append(x)
            j += 1
            if c and j == 2:
                p = N(10 * (k + 1))  # tpyc: warning(/the loop over `p\.ns` will not keep iterating the object it was given/)
    print("scalar-nested-loop", out)


# an untracked holder (two hops into the element) bound on each pass, then the
# rebind on the same pass: the next pass's rebind reuses the slot the holder
# still reads (it rebinds on the first pass only, so both runs agree)
class NB:
    xs: list[Box]

    def __init__(self, i: int32) -> None:
        self.xs = [Box(i), Box(i + 1)]


def holder_two_hop_loop(c: bool) -> None:
    t = (NB(1), 5)
    out: list[int32] = []
    for k in range(2):
        saved = t[0].xs[1]
        if c and k == 0:
            t = (NB(10), 6)  # tpyc: warning(/'saved' will not keep the object it was given/)
        saved.n += 100
        out.append(saved.n)
    print("holder-two-hop-loop", out, t[0].xs[1].n)


# a loop over a FRESH iterable whose type keeps no pointer: the iterator runs
# over that object, not over `t`, so rebinding `t` inside the loop is silent
class Bag:
    xs: list[int32]

    def __init__(self, n: N) -> None:
        self.xs = []
        for x in n.ns:
            self.xs.append(x * 2)

    def __iter__(self) -> Iterator[int32]:
        return iter(self.xs)


def fresh_iterable_loop(c: bool) -> None:
    t = (N(1), 5)
    out: list[int32] = []
    for k in range(2):
        for v in Bag(t[0]):
            if c:
                t = (N(10 * (k + 1)), 6)  # tpyc: ok
            out.append(v + t[0].ns[0])
    print("fresh-iterable-loop", out)



# a nested loop over `range(len(...))` of the name's element: the `int32` bound
# reaches `range` as a copy, so the iterator keeps nothing of `t` / `p` and the
# rebind inside it is silent
def range_len_loop(c: bool) -> None:
    t = (N(1), 5)
    out: list[int32] = []
    for k in range(2):
        for i in range(len(t[0].ns)):
            if c:
                t = (N(10 * (k + 1)), 6)  # tpyc: ok
            out.append(t[0].ns[i])
    p = N(1)
    for k in range(2):
        for i in range(len(p.ns)):
            if c:
                p = N(10 * (k + 1))  # tpyc: ok
            out.append(p.ns[i])
    print("range-len-loop", out)


def main() -> None:
    for n in gen_loop_alias(Box(5)):
        print("gen-loop-alias", n)
    holder_loop()
    nested_loop_rebind(True)
    print("gen-nested-loop", list(gen_nested_loop_rebind(True)))
    asyncio.run(co_nested_loop_rebind(True))
    scalar_nested_loop_rebind(True)
    holder_two_hop_loop(True)
    fresh_iterable_loop(True)
    range_len_loop(True)


main()
