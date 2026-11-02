# A container / record argument read from a name, field or element binds a
# generic protocol parameter as its concrete twin would: by reference, no copy.
import asyncio
from typing import Iterable, Iterator, Protocol, Sequence, Sized
from tpy import int32


class Adder[T](Protocol):
    def add(self, x: T) -> None: ...


class Bag:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def add(self, x: int32) -> None:
        self.n += x


def push(c: Adder[int32]) -> None:
    c.add(9)


def total_after(it: Iterable[int32], other: list[int32]) -> int32:
    other.append(10)
    t = 0
    for v in it:
        t += v
    return t


def size_after(s: Sized, other: list[int32]) -> int32:
    other.append(1)
    return len(s)


def seqsum(s: Sequence[int32]) -> int32:
    return s[0] + s[len(s) - 1]


def walk(it: Iterable[int32]) -> Iterator[int32]:
    for v in it:
        yield v


def gl(it: list[int32]) -> Iterator[int32]:
    for v in it:
        yield v


def bsum(it: Iterable[int]) -> int:
    t = 0
    for v in it:
        t += v
    return t


def bwalk(it: Iterable[int]) -> Iterator[int]:
    for v in it:
        yield v


def gsize(s: Sized) -> Iterator[int32]:
    yield len(s)


async def apush(c: Adder[int32]) -> int32:
    c.add(4)
    return 1


class Pusher:
    def __init__(self, c: Adder[int32]) -> None:
        c.add(2)


class H:
    b: Bag
    xs: list[int32]

    def __init__(self) -> None:
        self.b = Bag()
        self.xs = [1, 2]

    @property
    def items(self) -> list[int32]:
        return self.xs

    def push_into(self, c: Adder[int32]) -> None:
        c.add(5)

    def sum_after(self, it: Iterable[int32]) -> int32:
        self.xs.append(10)
        t = 0
        for v in it:
            t += v
        return t


def main() -> None:
    bags = [Bag(), Bag()]
    h = H()
    # free function: a list element at a writing generic protocol
    push(bags[1])  # tpyc: ok
    print("free_elem", bags[1].n)
    # method: the element at the method's protocol parameter
    h.push_into(bags[0])  # tpyc: ok
    print("method_elem", bags[0].n)
    # constructor: the element at the constructor's protocol parameter
    Pusher(bags[1])  # tpyc: ok
    print("ctor_elem", bags[1].n)
    # field at Iterable (free and method): the callee grows the field through
    # another name before iterating, and the protocol parameter sees the growth
    print("field_iter", total_after(h.xs, h.xs), h.sum_after(h.xs))  # tpyc: ok
    # field at a writing protocol (free and method)
    push(h.b)  # tpyc: ok
    h.push_into(h.b)  # tpyc: ok
    print("field_push", h.b.n)
    # element and field at a non-generic protocol, grown through another name
    ll = [[1], [2, 3]]
    print("sized", size_after(ll[1], ll[1]), size_after(h.xs, h.xs))  # tpyc: ok
    # element at Sequence[T]: indexed through the protocol parameter
    print("seq", seqsum(ll[1]))  # tpyc: ok
    # dict element at a writing generic protocol
    d = {"a": Bag()}
    push(d["a"])  # tpyc: ok
    print("dict_elem", d["a"].n)
    # bytearray at a generic protocol and at a generator, grown before driven
    ba = bytearray(b"ab")
    print("ba", bsum(ba))  # tpyc: ok
    bg = bwalk(ba)  # tpyc: ok
    # warns as on any container a frame holds (BUGS.md#frame-held-container-mutation-warns)
    ba.append(1)  # tpyc: warning(/Mutation of 'ba' while borrowed/)
    bt = 0
    for b in bg:
        bt += b
    print("ba_gen", bt)
    # Sized generator over an element, the element grown before it is driven
    sg = gsize(ll[1])  # tpyc: ok
    ll[1].append(4)
    for n in sg:
        print("gen_sized", n)
    # borrowed property: the getter's list is the field itself; no warning
    # where the `h.xs` spelling has one (BUGS.md#property-iter-loan-misses-getter-storage)
    pg = walk(h.items)  # tpyc: ok
    h.xs.append(7)
    pt = 0
    for v in pg:
        pt += v
    print("getter", pt)
    # generator over an element, the element mutated before it is driven
    it = walk(ll[1])  # tpyc: ok
    ll[1].append(7)
    t = 0
    for v in it:
        t += v
    print("gen_elem", t)
    # and/or of two lists at a generator: Python picks one of the two objects,
    # so the frame holds `la` itself and sees it grow (protocol and concrete twin)
    la = [1, 2]
    lb = [3, 4]
    ag = walk(la or lb)  # tpyc: ok
    tg = gl(la or lb)  # tpyc: ok
    la.append(5)  # tpyc: warning(/Mutation of 'la' while borrowed/)
    print("andor", sum(ag), sum(tg))


async def amain() -> None:
    bags = [Bag(), Bag()]
    # async: the coroutine writes through the element
    r = await apush(bags[1])  # tpyc: ok
    print("async_elem", r, bags[1].n)


def warn() -> None:
    ll = [[1], [2, 3]]
    it = walk(ll[1])
    # growing `ll` may reallocate it, leaving the frame's reference to
    # `ll[1]` dangling: the warning is right
    ll.append([4])  # tpyc: warning(/Mutation of 'll' while borrowed/)
    print("warn", len(ll))
    h = H()
    fit = walk(h.xs)
    # a generator over a field: growing the field under a frame that may be
    # iterating it invalidates the frame's iterator
    h.xs.append(3)  # tpyc: warning(/Mutation of 'h.xs' while borrowed/)
    print("warn_field", len(h.xs))


main()
asyncio.run(amain())
warn()
# module level: a global list's element at the protocol parameter
gbags = [Bag(), Bag()]
push(gbags[0])  # tpyc: ok
print("module_elem", gbags[0].n)
