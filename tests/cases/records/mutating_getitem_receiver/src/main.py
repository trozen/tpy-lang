# A read through a MUTATING `__getitem__` (insert-on-miss) is a method call on
# its receiver: whatever only reads through it still takes the receiver mutable.
import asyncio
from typing import Callable, Iterator, Protocol
from tpy import Hashable, Own, copy, dispatch, int32, int64, readonly


class Counts:
    d: dict[str, int32]

    def __init__(self) -> None:
        self.d = {}

    # Inserts on a miss, so it mutates the receiver.
    @readonly(False)
    def __getitem__(self, k: str) -> int32:
        if k not in self.d:
            self.d[k] = 0
        return self.d[k]

    # Counts its lookups, so it mutates the receiver.
    @readonly(False)
    def __contains__(self, k: str) -> bool:
        self.d["?"] = 0
        return k in self.d

    def peek_self(self, k: str) -> int32:
        return self[k]  # tpyc: ok


class DefaultDict[K: Hashable, V]:
    factory: Callable[[], V]
    data: dict[K, V]

    def __init__(self, factory: Callable[[], V]) -> None:
        self.factory = factory
        self.data = {}

    # Node-based storage: the insert never moves an existing value.
    @readonly(False)
    def __getitem__(self, key: K) -> V:
        if key not in self.data:
            self.data[copy(key)] = self.factory()
        return self.data[key]


class Pt:
    x: int32

    def __init__(self) -> None:
        self.x = 0


class Slots:
    ps: list[Pt]

    def __init__(self) -> None:
        self.ps = []

    # Pointer-repr Optional result: a borrow of the slot, or None.
    @readonly(False)
    def __getitem__(self, i: int32) -> Pt | None:
        if i < 0:
            return None
        while len(self.ps) <= i:
            self.ps.append(Pt())
        return self.ps[i]


class PtRows:
    ps: list[Pt]

    def __init__(self) -> None:
        self.ps = []

    @readonly(False)
    def __getitem__(self, i: int32) -> Pt:
        while len(self.ps) <= i:
            self.ps.append(Pt())
        return self.ps[i]


class Plain:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [7]

    def __getitem__(self, i: int32) -> int32:
        return self.xs[i]


class Tally:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = []

    @readonly(False)
    def __getitem__(self, i: int32) -> int32:
        while len(self.xs) <= i:
            self.xs.append(0)
        return self.xs[i]


class Two:
    d: dict[str, int32]
    xs: list[int32]

    def __init__(self) -> None:
        self.d = {}
        self.xs = [5]

    @dispatch
    def __getitem__(self, i: int32) -> int32:
        return self.xs[i]

    # Only the str-keyed overload mutates (a str-key subscript is not
    # resolved per key yet: BUGS.md#record-getitem-overload-first-wins).
    @dispatch
    @readonly(False)
    def __getitem__(self, k: str) -> int32:
        if k not in self.d:
            self.d[k] = 0
        return self.d[k]


class KeyBase[T]:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [5]

    @dispatch
    def __getitem__(self, k: T) -> int32:
        return self.xs[0]

    # Only the int32-keyed overload mutates.
    @dispatch
    @readonly(False)
    def __getitem__(self, i: int32) -> int32:
        self.xs.append(1)
        return self.xs[0]


class KeyGen[U](KeyBase[U]):
    def __init__(self) -> None:
        super().__init__()


class KeyMono(KeyBase[int64]):
    def __init__(self) -> None:
        super().__init__()


class Bag:
    n: int32
    resets: int32

    def __init__(self) -> None:
        self.n = 0
        self.resets = 0

    # No `__contains__`: `x in bag` iterates, and `__iter__` mutates.
    def __iter__(self) -> "Bag":
        self.n = 0
        self.resets += 1
        return self

    def __next__(self) -> int32:
        self.n += 1
        if self.n > 3:
            raise StopIteration()
        return self.n


class Pair:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    # Not annotated, but it does not mutate: inferred readonly.
    def __iter__(self) -> Iterator[int32]:
        return iter(self.xs)


class Lookup(Protocol):
    @readonly(False)
    def __getitem__(self, i: int32) -> int32: ...


def new_list() -> Own[list[int32]]:
    return []


def free_function(c: Counts, k: str) -> str:
    # Free function: the param is read only through the mutating accessor.
    return f"{c[k]} {abs(c[k])}"  # tpyc: ok


def membership(c: Counts, k: str) -> bool:
    # `in` through a mutating `__contains__`.
    return k in c  # tpyc: ok


def generic(g: DefaultDict[str, list[int32]], k: str) -> int32:
    # Generic record at a container value.
    return len(g[k])  # tpyc: ok


def generic_alias(g: DefaultDict[str, list[int32]]) -> None:
    # The alias survives a later mutating read: the storage is node-based.
    row = g["a"]  # tpyc: ok
    row.append(1)
    print("generic alias:", len(g["b"]), g["a"])  # tpyc: ok
    row.append(2)
    print("generic alias after:", g["a"])


def comprehension(r: PtRows, idx: list[int32]) -> Own[list[int32]]:
    # Comprehension element.
    return [r[i].x for i in idx]  # tpyc: ok


def walk(c: Counts, keys: list[str]) -> Iterator[int32]:
    # Generator body.
    for k in keys:
        yield c[k]  # tpyc: ok


def closure(c: Counts, k: str) -> int32:
    # Nested function reading the captured receiver.
    def read() -> int32:
        return c[k]  # tpyc: ok
    return read()


def loop_receiver(cs: list[Counts], k: str) -> int32:
    # Loop variable over a list param.
    t = 0
    for c in cs:
        t += c[k]  # tpyc: ok
    return t


def element_alias(cs: list[Counts], k: str) -> int32:
    # Local alias of a container element.
    c = cs[0]
    return c[k]  # tpyc: ok


def optional_result(s: Slots) -> int32:
    # Optional pointer-repr result: mutate through the borrow, read back.
    p = s[2]  # tpyc: ok
    if p is not None:
        p.x = 9
    return s.ps[2].x


def protocol_param(p: Lookup, i: int32) -> int32:
    # Protocol param whose `__getitem__` is declared mutating.
    return p[i]  # tpyc: ok


def int_key(t: readonly[Two]) -> int32:
    # Overloads: the int32 key calls the readonly overload, so a readonly
    # receiver is fine and stays const.
    return t[0]  # tpyc: ok


def mono_key(c: readonly[KeyMono], k: int64) -> int32:
    # Inherited overloads: the int64 key picks the readonly `T = int64` one.
    return c[k]  # tpyc: ok


def generic_key(c: readonly[KeyGen[int64]], k: int64) -> int32:
    # The same through a generic subclass: the inherited substitution composes
    # with the instance's, as the monomorphic twin above resolves it.
    return c[k]  # tpyc: ok


def readonly_iteration(p: readonly[Pair]) -> int32:
    # Inverse: a non-mutating `__iter__` over a readonly source compiles.
    t = 0
    for x in p:  # tpyc: ok
        t += x
    return t


def in_by_iteration(b: Bag, x: int32) -> bool:
    # `in` falling back to a mutating `__iter__`.
    return x in b  # tpyc: ok


async def async_read(c: Counts, k: str) -> int32:
    # Async body.
    await asyncio.sleep(0)
    return c[k]  # tpyc: ok


def match_arm(c: Counts, k: str) -> int32:
    # `match` arm.
    match k:
        case "m1":
            return c[k]  # tpyc: ok
        case _:
            return -1


def guarded(c: Counts, k: str) -> int32:
    # try/finally body.
    try:
        return c[k]  # tpyc: ok
    finally:
        print("finally: ran")


def plain_reader(p: Plain) -> int32:
    # Inverse: a readonly `__getitem__` keeps the param const.
    return p[0]  # tpyc: ok


class Holder:
    c: Counts

    def __init__(self) -> None:
        self.c = Counts()
        # Constructor body.
        print("ctor:", self.c["init"])  # tpyc: ok

    def look(self, k: str) -> int32:
        # Method: the receiver is a field of self.
        return self.c[k]  # tpyc: ok

    def look_alias(self, k: str) -> int32:
        # Method: a local alias of the field.
        c = self.c
        return c[k]  # tpyc: ok


def main() -> None:
    c = Counts()
    print("free:", free_function(c, "a"), len(c.d))
    print("in:", membership(c, "a"), membership(c, "z"), len(c.d))
    print("self:", c.peek_self("s"), len(c.d))
    g: DefaultDict[str, list[int32]] = DefaultDict(new_list)
    print("generic:", generic(g, "x"), len(g.data))
    generic_alias(g)
    r = PtRows()
    print("comprehension:", comprehension(r, [1, 3]), len(r.ps))
    for v in walk(c, ["w1", "w2"]):
        print("generator:", v)
    print("closure:", closure(c, "cl"), "cl" in c.d)
    cs = [Counts(), Counts()]
    print("loop:", loop_receiver(cs, "l"), len(cs[0].d), len(cs[1].d))
    print("element:", element_alias(cs, "e"), len(cs[0].d))
    s = Slots()
    print("optional:", optional_result(s), len(s.ps))
    t = Tally()
    print("protocol:", protocol_param(t, 3), len(t.xs))
    print("plain:", plain_reader(Plain()))
    two = Two()
    print("overload:", int_key(two), len(two.d))
    k64: int64 = 0
    km = KeyMono()
    kg = KeyGen[int64]()
    print("inherited overload:", mono_key(km, k64), generic_key(kg, k64), len(km.xs), len(kg.xs))
    print("readonly iteration:", readonly_iteration(Pair()))
    bag = Bag()
    print("in iter:", in_by_iteration(bag, 2), bag.resets)
    print("async:", asyncio.run(async_read(c, "as")), "as" in c.d)
    print("match:", match_arm(c, "m1"), match_arm(c, "m2"), "m1" in c.d)
    v = guarded(c, "t")
    print("try:", v, "t" in c.d)
    h = Holder()
    print("method:", h.look("m"), h.look_alias("n"), len(h.c.d))


main()
