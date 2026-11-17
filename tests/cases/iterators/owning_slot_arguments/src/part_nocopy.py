# A `@nocopy` element: only a container handed over reaches an owning slot,
# at every callee kind (a copy anywhere would fail the C++ build).
import asyncio
from typing import Iterable, Iterator, Protocol
from tpy import Own, int32, nocopy


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


class Counted[T](Iterable[T], Protocol):
    def __len__(self) -> int32: ...


class Rows(Iterable[Own[N]], Protocol):
    pass


def take_rows(xs: Rows) -> int32:
    # The ancestor fixes the `Own` step.
    t = 0
    for x in xs:
        t += x.v
    return t


class Bin:
    items: list[N]

    def __init__(self, xs: Iterable[Own[N]]) -> None:
        self.items = []
        for x in xs:
            self.items.append(x)

    def add(self, xs: Iterable[Own[N]]) -> None:
        for x in xs:
            self.items.append(x)


def take(xs: Iterable[Own[N]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


def take_counted(xs: Counted[Own[N]]) -> int32:
    n = len(xs)
    t = 0
    for x in xs:
        t += x.v
    return t + n


def relay(xs: Iterable[Own[N]]) -> int32:
    # A second name forwards the parameter on in the category it arrived in.
    b = xs  # tpyc: ok
    return take(b)  # tpyc: ok


def gen(xs: Iterable[Own[N]]) -> Iterator[int32]:
    for x in xs:
        yield x.v


async def atake(xs: Iterable[Own[N]]) -> int32:
    t = 0
    for x in xs:
        await asyncio.sleep(0)
        t += x.v
    return t


def slice_in(xs: Iterable[Own[N]]) -> Own[list[int32]]:
    # A slice store passes the parameter on in the category it arrived in.
    out: list[N] = [N(7), N(8)]
    out[0:2] = xs  # tpyc: ok
    return [n.v for n in out]


def stepped_in(xs: Iterable[Own[N]]) -> Own[list[int32]]:
    out: list[N] = [N(7), N(8), N(9)]
    out[::2] = xs  # tpyc: ok
    return [n.v for n in out]


def gen_slice_in(xs: Iterable[Own[N]]) -> Iterator[int32]:
    # A slice store in a generator body passes the parameter on as well.
    out: list[N] = [N(7), N(8)]
    out[0:2] = xs  # tpyc: ok
    for n in out:
        yield n.v


def keep(n: Own[N]) -> Own[N]:
    return n


def nkey(n: N) -> int32:
    return n.v


def mk() -> Own[list[N]]:
    return [N(1), N(2)]


def gen_caller() -> Iterator[int32]:
    # A generator caller hoists a fresh argument onto its frame and moves it in.
    for y in gen([N(i) for i in range(3)]):  # tpyc: ok
        yield y
    for z in gen([N(7)]):  # tpyc: ok
        yield z


async def amain() -> None:
    d = [N(1), N(2)]
    print("nocopy async dead", await atake(d))  # tpyc: ok
    print("nocopy async fresh", await atake(mk()))  # tpyc: ok
    print("nocopy async comprehension", await atake([N(i) for i in range(4)]))  # tpyc: ok


def main() -> None:
    d = [N(1), N(2)]
    print("nocopy dead", take(d))  # tpyc: ok
    print("nocopy fresh", take(mk()))  # tpyc: ok
    print("nocopy literal", take([N(3)]))  # tpyc: ok
    print("nocopy comprehension", take([N(i) for i in range(3)]))  # tpyc: ok
    c = [N(4)]
    print("nocopy counted", take_counted(c))  # tpyc: ok
    g = [N(5), N(6)]
    print("nocopy gen dead", list(gen(g)))  # tpyc: ok
    print("nocopy gen fresh", list(gen(mk())))  # tpyc: ok
    print("nocopy gen caller", list(gen_caller()))
    print("nocopy alias", relay(mk()))
    b = Bin(mk())  # tpyc: ok
    a = [N(3)]
    b.add(a)  # tpyc: ok
    b.add([N(i) for i in range(2)])  # tpyc: ok
    print("nocopy ctor method", [n.v for n in b.items])
    s = [N(2), N(1)]
    print("nocopy sorted", [n.v for n in sorted(s, key=nkey)])  # tpyc: ok
    out: list[N] = []
    e = [N(5)]
    out.extend(e)  # tpyc: ok
    out.extend(mk())  # tpyc: ok
    print("nocopy extend", [n.v for n in out])
    m = [N(8), N(9)]
    print("nocopy map dead", [n.v for n in map(keep, m)])  # tpyc: ok
    print("nocopy map fresh", [n.v for n in map(keep, mk())])  # tpyc: ok
    # An iterator holding a fresh list owns its elements: each is moved.
    print("nocopy map iter", [n.v for n in map(keep, iter(mk()))])  # tpyc: ok
    print("nocopy map reversed", [n.v for n in map(keep, reversed(mk()))])  # tpyc: ok
    rd = [N(1), N(2)]
    print("nocopy rows dead", take_rows(rd))  # tpyc: ok
    print("nocopy rows fresh", take_rows(mk()))  # tpyc: ok
    sd = [N(1), N(2)]
    print("nocopy slice store", slice_in(sd))  # tpyc: ok
    print("nocopy stepped slice store", stepped_in(mk()))  # tpyc: ok
    print("nocopy gen slice store", list(gen_slice_in(mk())))  # tpyc: ok
    asyncio.run(amain())
