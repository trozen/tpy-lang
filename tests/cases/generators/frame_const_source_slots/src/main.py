# Every slot a resumable frame spells off one of its CONST bindings takes the
# constness from that binding: a nested `for` over a const loop var, the
# `.items()` / `.values()` view (whose verdict is its RECEIVER's), the
# universal `iter_type_t` iterator, the element pointers of a tuple unpack,
# and an alias bound out of a const loop var. The bindings here are const
# because the Phase-2 verdict says the parameter is never mutated -- no
# `readonly[...]` is needed, and the sections deliberately use plain
# signatures (the explicit spelling is one section of its own).
#
# The const sections are read-only by construction, so they cannot mutate
# through the alias to prove it is one; the committed `.hpp` is what pins the
# `const T*` / `const S` spellings, and the alias-vs-copy axis of the same
# shapes is pinned by `generators/frame_unpack_ref_elem_lift`. The `mutated`
# section carries the negative: its param IS mutated, so the capture stays
# `T&`, and the loop var is written through after the yield and read back in
# the caller; `values_mutated` is the same negative for the view hop.
from typing import Iterator
from tpy import int32, readonly
from tplib.array_list import ArrayList
import asyncio


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class P:
    xs: list[int32]

    def __init__(self, xs: list[int32]) -> None:
        self.xs = xs


class H:
    pair: tuple[int32, Box]

    def __init__(self, n: int32) -> None:
        self.pair = (n, Box(n + 1))


# nested for over a const list-of-list param: the inner loop's iterator slot
# is spelled off the const loop var
def nested(ds: list[list[int32]]) -> Iterator[int32]:
    for d in ds:
        for x in d:  # tpyc: ok
            yield x


# same, one field hop further from the const root
def fieldhop(ps: list[P]) -> Iterator[int32]:
    for p in ps:
        for x in p.xs:  # tpyc: ok
            yield x


# the `.items()` view is deduced off the const receiver, and it is read across
# a suspension
def items(ds: list[dict[int32, int32]]) -> Iterator[int32]:
    for d in ds:
        for k, v in d.items():  # tpyc: ok
            yield k
            yield v


# a borrowing-view accessor over a REFERENCE-typed value: the view's element
# const comes from the receiver, so the loop var is a `const Box*`
def values_ref(m: dict[int32, Box]) -> Iterator[int32]:
    for b in m.values():  # tpyc: ok
        yield b.n


# the same through `.items()`, whose unpack target aliases the value in place
def items_ref(m: dict[int32, Box]) -> Iterator[int32]:
    for k, b in m.items():  # tpyc: ok
        yield k
        yield b.n


# the view receiver is itself a const LOOP VAR, one binding further out
def deep_items(ms: list[dict[int32, list[int32]]]) -> Iterator[int32]:
    for m in ms:
        for k, vs in m.items():  # tpyc: ok
            yield k + vs[0]


# a `list`-typed value: the unpack target is a `const std::vector<int32_t>*`
def values_list(m: dict[int32, list[int32]]) -> Iterator[int32]:
    for vs in m.values():  # tpyc: ok
        yield vs[0]


# the async twin of `values_ref`
async def avalues(m: dict[int32, Box]) -> int32:
    t = 0
    for b in m.values():  # tpyc: ok
        await asyncio.sleep(0)
        t += b.n
    return t


# the negative for the view hop: the receiver IS mutated, so the loop var stays
# mutable and the write after the yield reaches the caller's dict
def values_mutated(m: dict[int32, Box]) -> Iterator[int32]:
    m[9] = Box(9)
    for b in m.values():  # tpyc: ok
        yield b.n
        b.n += 100


# universal `__iter__` source: the iterator slot names the const container, so
# it holds the const iterator that `__iter__` actually returns here
def over_array_list(al: ArrayList[int32, 8]) -> Iterator[int32]:
    for x in al:  # tpyc: ok
        yield x


# for-unpack over a const container: the target pointers are const too
def unpack_loop(pairs: list[tuple[int32, Box]]) -> Iterator[int32]:
    for a, b in pairs:  # tpyc: ok
        yield a
        yield b.n


# standalone unpack off a const subscript receiver, read after the suspension
def unpack_subscript(pairs: list[tuple[int32, Box]]) -> Iterator[int32]:
    a, b = pairs[0]  # tpyc: ok
    yield a
    yield b.n


# same off a const field receiver
def unpack_field(h: H) -> Iterator[int32]:
    a, b = h.pair  # tpyc: ok
    yield a
    yield b.n


# the explicit spelling of the same verdict
def unpack_readonly(pairs: readonly[list[tuple[int32, Box]]]) -> Iterator[int32]:
    a, b = pairs[0]  # tpyc: ok
    yield a
    yield b.n


# a borrow alias bound out of a const loop var, read after the suspension
def alias_in_loop(ds: list[list[Box]]) -> Iterator[int32]:
    for d in ds:
        a = d[0]  # tpyc: ok
        yield a.n
        yield a.n + 1


class Bag:
    rows: list[list[int32]]

    def __init__(self, rows: list[list[int32]]) -> None:
        self.rows = rows

    # method position: `self` is the const binding the nested slot reads
    def scan(self) -> Iterator[int32]:
        for r in self.rows:
            for x in r:  # tpyc: ok
                yield x


# the async twin of `nested`
async def anested(ds: list[list[int32]]) -> int32:
    t = 0
    for d in ds:
        for x in d:  # tpyc: ok
            await asyncio.sleep(0)
            t += x
    return t


# the negative: a mutated param keeps the mutable capture, and the loop var is
# written through after the yield -- the caller sees it, so a copy would fail
def mutated(ds: list[list[Box]]) -> Iterator[int32]:
    ds.append([Box(9)])
    for d in ds:
        for b in d:  # tpyc: ok
            yield b.n
            b.n += 100


def main() -> None:
    for v in nested([[1, 2], [3]]):
        print("nested", v)

    for v in fieldhop([P([1, 2]), P([3])]):
        print("fieldhop", v)

    for v in items([{1: 10}, {2: 20}]):
        print("items", v)

    mb: dict[int32, Box] = {1: Box(10), 2: Box(20)}
    for v in values_ref(mb):
        print("values_ref", v)
    for v in items_ref(mb):
        print("items_ref", v)
    print("avalues", asyncio.run(avalues(mb)))

    ml: dict[int32, list[int32]] = {}
    ml[1] = [11, 12]
    ml[2] = [21]
    for v in values_list(ml):
        print("values_list", v)
    mls = [ml]
    for v in deep_items(mls):
        print("deep_items", v)

    mm: dict[int32, Box] = {1: Box(3)}
    for v in values_mutated(mm):
        print("values_mutated", v)
    print("values_mutated after", mm[1].n, mm[9].n)

    al = ArrayList[int32, 8]()
    al.append(1)
    al.append(2)
    for v in over_array_list(al):
        print("arraylist", v)

    pairs = [(1, Box(2)), (3, Box(4))]
    for v in unpack_loop(pairs):
        print("unpack_loop", v)
    for v in unpack_subscript(pairs):
        print("unpack_subscript", v)
    for v in unpack_field(H(5)):
        print("unpack_field", v)
    for v in unpack_readonly(pairs):
        print("unpack_readonly", v)

    for v in alias_in_loop([[Box(7)], [Box(8)]]):
        print("alias_in_loop", v)

    for v in Bag([[1, 2], [3]]).scan():
        print("method", v)

    print("async", asyncio.run(anested([[1, 2], [3]])))

    boxes = [[Box(1)], [Box(2)]]
    for v in mutated(boxes):
        print("mutated", v)
    print("mutated after", boxes[0][0].n, boxes[1][0].n, boxes[2][0].n)


main()
