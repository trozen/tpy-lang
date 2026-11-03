# The explicit `c.__getitem__(k)` call compiles every shape its subscript twin
# `c[k]` does: a reference result held across a yield / await, a match subject
# chained off it, and an Optional ternary arm -- each writing through the
# result so the write shows in the container. A capture of a call subject
# writes through to the container as the subscript's does.
import asyncio
from typing import Iterator
from tpy import int32, Own, Ptr, deref, take_ptr


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    items: list[Item]

    def __init__(self) -> None:
        self.items = [Item(10), Item(20), Item(30)]

    def __getitem__(self, i: int32) -> Item:
        return self.items[i]

    def get(self, i: int32) -> Item:
        return self.items[i]

    def classify(self, i: int32) -> str:
        # match position: a method body.
        match self.__getitem__(i).n:  # tpyc: ok
            case 10:
                return "ten"
            case _:
                return "other"

    def classify_sub(self, i: int32) -> str:
        match self[i].n:  # tpyc: ok
            case 10:
                return "ten"
            case _:
                return "other"

    def pick(self, f: bool) -> None:
        # Optional ternary arm position: a method body.
        p: Item | None = self.__getitem__(1) if f else None  # tpyc: ok
        if p is not None:
            p.n += 1
        q: Item | None = self[1] if f else None  # tpyc: ok
        if q is not None:
            q.n += 1


class Other:
    s: int32

    def __init__(self, s: int32) -> None:
        self.s = s


class Wrap:
    inner: Item

    def __init__(self, n: int32) -> None:
        self.inner = Item(n)


class Shelf:
    wraps: list[Wrap]
    other: Other

    def __init__(self) -> None:
        self.wraps = [Wrap(1)]
        self.other = Other(7)

    def wrap(self, i: int32) -> Wrap:
        return self.wraps[i]

    def __getitem__(self, i: int32) -> Wrap | Other:
        if i < 0:
            return self.other
        return self.wraps[i]


def capture_read(c: Bag) -> int32:
    # a read-only capture of a call subject leaves the receiver const.
    match c.__getitem__(0):  # tpyc: ok
        case Item() as it:
            return it.n
    return 0


def capture_nested(s: Shelf) -> None:
    # a nested capture of a call subject writes through to the receiver.
    match s.wrap(0):  # tpyc: ok
        case Wrap(inner=x):
            x.n = 2


def capture_union(s: Shelf) -> None:
    # a capture of a union result held by value writes through its pointer.
    match s.__getitem__(-1):  # tpyc: ok
        case Other() as o:
            o.s = 8
        case _:
            pass


def capture_then_clear(d: dict[str, Item], fallback: Item) -> None:
    # the capture's loan ends with the arm: clearing after it is quiet.
    match d.get("a", fallback):  # tpyc: ok
        case Item() as it:
            print("capture_then_clear", it.n)
    # The capture borrows as `it = d.get(...)` would, and the warning does
    # not yet know the borrow ended (BUGS.md#borrow-warning-not-last-use-aware).
    d.clear()  # tpyc: warning(/Mutation of 'd' while borrowed/)
    print("capture_then_clear", len(d))


class Pair[T]:
    x: T

    def __init__(self, x: Own[T]) -> None:
        self.x = x

    def pair(self) -> tuple[T, int32]:
        return (self.x, 1)


def pair_count[T](p: Pair[T]) -> int32:
    # open-T tuple result of a method in a generic body.
    t = p.pair()  # tpyc: ok
    return t[1]


def gen(c: Bag) -> Iterator[int32]:
    # generator: the result is held across the yield and written after it.
    x = c.__getitem__(0)  # tpyc: ok
    y = c[1]  # tpyc: ok
    yield x.n + y.n
    x.n = 11
    y.n = 21
    yield x.n + y.n


async def agen(c: Bag) -> int32:
    # async: the result is held across the await and written after it.
    x = c.__getitem__(2)  # tpyc: ok
    y = c[0]  # tpyc: ok
    await asyncio.sleep(0)
    x.n = 33
    y.n = 12
    return x.n + y.n


def gen_match(c: Bag) -> Iterator[str]:
    # match subject in a generator.
    match c.__getitem__(0).n:  # tpyc: ok
        case 100:
            yield "hundred"
        case _:
            yield "other"


async def agen_match(c: Bag) -> str:
    # match subject in a coroutine (an Optional ternary local in a frame body
    # is BUGS.md#select-pointer-slot-gaps for both spellings).
    await asyncio.sleep(0)
    c.__getitem__(2).n += 1000
    match c.__getitem__(2).n:  # tpyc: ok
        case 1300:
            return "a1300"
        case _:
            return "other"


def capture(c: Bag) -> None:
    # a capture of a call subject writes through to the container.
    match c.__getitem__(0):  # tpyc: ok
        case Item(n=100) as it:
            it.n = 12
        case _:
            pass
    match c[1]:  # tpyc: ok
        case Item() as it:
            it.n += 1
    print("capture", c.items[0].n, c.items[1].n)


class Builder:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    def add(self, k: int32) -> "Builder":
        self.total += k
        return self


def make_item(n: int32) -> Own[Item]:
    return Item(n)


def write_neighbours(c: Bag) -> None:
    # writes the fresh-result rule leaves alone: through a reference result,
    # into a bound fresh result, and mutating calls on a temporary.
    c.get(2).n = 5  # tpyc: ok
    it = make_item(1)
    it.n = 6  # tpyc: ok
    print("write_neighbours", c.items[2].n, it.n, Builder().add(1).add(2).total)


class Picker:
    p: Ptr[Item]

    def __init__(self, it: Item) -> None:
        self.p = take_ptr(it)

    def __getitem__(self, i: int32) -> Item:
        p = self.p
        return deref(p)


class Shell:
    _n: int32
    held: Item

    def __init__(self, n: int32) -> None:
        self._n = n
        self.held = Item(n)

    @property
    def size(self) -> int32:
        return self._n

    @size.setter
    def size(self, v: int32) -> None:
        print("reach property setter", v)
        self._n = v


SHELL = Shell(1)


def shell_ptr() -> Ptr[Shell]:
    return take_ptr(SHELL)


def make_shell() -> Own[Shell]:
    return Shell(2)


async def borrow_item(it: Item) -> Item:
    return it


async def await_write() -> None:
    # a borrowed await result writes the existing object.
    it = Item(1)
    (await borrow_item(it)).n = 5  # tpyc: ok
    print("reach await", it.n)


def reach_existing() -> None:
    # writes through a call result that reach existing storage compile.
    shell_ptr().held.n = 7  # tpyc: ok
    make_shell().size = 9  # tpyc: ok
    it = Item(1)
    Picker(it).__getitem__(0).n = 5  # tpyc: ok
    print("reach", SHELL.held.n, it.n)
    asyncio.run(await_write())


def capture_dangle(c: Bag) -> None:
    # a capture of a call subject borrows the receiver's storage: replacing it
    # in the arm is warned, as for the binding twin (not run: it dangles).
    match c.get(0):  # tpyc: ok
        case Item() as it:
            c.items = []  # tpyc: warning(/Mutation of 'c' while borrowed/)
            it.n = 3


def plain_getter(c: Bag, f: bool) -> None:
    # a plain getter's reference result: a match subject and a ternary arm.
    match c.get(0).n:  # tpyc: ok
        case 12:
            print("plain_getter match twelve")
        case _:
            print("plain_getter match other")
    p: Item | None = c.get(0) if f else None  # tpyc: ok
    if p is not None:
        p.n = 13
    print("plain_getter", c.items[0].n)


def classify(c: Bag, i: int32) -> str:
    # match position: a free function.
    match c.__getitem__(i).n:  # tpyc: ok
        case 10:
            return "ten"
        case _:
            return "other"


def classify_sub(c: Bag, i: int32) -> str:
    match c[i].n:  # tpyc: ok
        case 10:
            return "ten"
        case _:
            return "other"


def pick(c: Bag, f: bool) -> None:
    # Optional ternary arm position: a free function.
    p: Item | None = c.__getitem__(0) if f else None  # tpyc: ok
    if p is not None:
        p.n = 100
    q: Item | None = c[2] if f else None  # tpyc: ok
    if q is not None:
        q.n = 300
    r: Item | None = c.__getitem__(1) if not f else None
    print("ternary", r is None)


def main() -> None:
    c = Bag()
    print("match", classify(c, 0), classify_sub(c, 1),
          c.classify(0), c.classify_sub(2))
    for v in gen(c):
        print("gen", v)
    print("gen", c.items[0].n, c.items[1].n)
    print("async", asyncio.run(agen(c)), c.items[2].n, c.items[0].n)
    pick(c, True)
    print("ternary", c.items[0].n, c.items[2].n)
    c.pick(True)
    print("ternary", c.items[1].n)
    for w in gen_match(c):
        print("gen_match", w)
    print("agen_match", asyncio.run(agen_match(c)), c.items[2].n)
    capture(c)
    print("open_t", pair_count(Pair(Item(1))), pair_count(Pair(7)))
    q = Pair(Item(1))
    t = q.pair()
    t[0].n = 9  # the tuple's element is the pair's object
    print("open_t", q.x.n)
    print("capture_read", capture_read(c))
    write_neighbours(c)
    reach_existing()
    sh = Shelf()
    capture_nested(sh)
    capture_union(sh)
    print("capture_nested", sh.wraps[0].inner.n, sh.other.s)
    capture_then_clear({"a": Item(1)}, Item(2))
    plain_getter(c, True)


main()
