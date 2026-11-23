# A head keeps what a conversion or a callee frame needs: a union member, an
# adapter, a subclass, an Optional, a generator factory's fresh argument.
from typing import Iterator, Protocol
from tpy import Own, StrView, dynamic, int32


class Src:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]


class Child(Src):
    def __init__(self, n: int32) -> None:
        super().__init__(n * 10)


class Empty:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = []


@dynamic
class Has(Protocol):
    def first(self) -> int32: ...


class Bag:
    ys: list[int32]

    def __init__(self, n: int32) -> None:
        self.ys = [n, n * 2]

    def first(self) -> int32:
        return self.ys[0]


def walk(s: Src) -> list[int32]:
    return s.xs


def cells(u: Src | Empty) -> list[int32]:
    if isinstance(u, Src):
        return u.xs
    return u.xs


def walk_opt(s: Src | None, fb: list[int32]) -> list[int32]:
    if s is not None:
        return s.xs
    return fb


def mk_opt(n: int32) -> Own[Src | None]:
    return Src(n)


def gwalk(s: Src) -> Iterator[int32]:
    for x in s.xs:
        yield x


def ggrow(s: Src) -> Iterator[int32]:
    # writes the kept argument after the boundary: the caller's loop sees it
    yield len(s.xs)
    s.xs.append(7)
    yield len(s.xs)


def ugrow(u: Src | Empty) -> Iterator[int32]:
    # writes the kept union member after the boundary
    if isinstance(u, Src):
        yield len(u.xs)
        u.xs.append(1)
        yield len(u.xs)


def gfirst(h: Has) -> Iterator[int32]:
    yield h.first()
    yield h.first() + 1


def mks(n: int32) -> str:
    return "view-long-enough-to-heap-" + str(n)


def sp(s: str) -> StrView:
    return s


def chars(s: StrView) -> Iterator[str]:
    for c in s[0:2]:
        yield c


class Maker:
    t: str

    def __init__(self, n: int32) -> None:
        self.t = mks(n)

    def name(self) -> StrView:
        return self.t


def mkm(n: int32) -> Own[Maker]:
    return Maker(n)


def junk() -> int32:
    return len([Src(99) for _ in range(4)])


def plain() -> None:
    # a member at a union of records, a subclass at its base, an Optional
    for x in cells(Src(1)):  # tpyc: ok
        print("convs union", x)
    for x in walk(Child(2)):  # tpyc: ok
        print("convs covariant", x)
    for x in walk_opt(mk_opt(3), [0]):  # tpyc: ok
        print("convs optional", x)
    for x in gfirst(Bag(4)):  # tpyc: ok
        print("convs adapter", x)


def gen(c: bool) -> Iterator[int32]:
    # the same conversions on the frame; the consumer allocates between yields
    for x in cells(Src(5)):  # tpyc: ok
        yield x
    for x in walk(Child(6)):  # tpyc: ok
        yield x
    for x in walk_opt(mk_opt(7), [0]):  # tpyc: ok
        yield x
    # a factory's fresh argument under a condition
    for y in (gwalk(Src(9)) if c else gwalk(Src(10))):  # tpyc: ok
        yield y
    for y in ggrow(Src(11)):  # tpyc: ok
        yield y
    for y in ugrow(Src(12)):  # tpyc: ok
        yield y


def gen_views(n: int32) -> Iterator[str]:
    # a factory's argument viewing a fresh string, and off a fresh receiver
    for i in range(n):
        for ch in chars(sp(mks(i))):  # tpyc: ok
            yield ch
    for ch in chars(mkm(5).name()):  # tpyc: ok
        yield ch


def main() -> None:
    plain()
    out: list[int32] = []
    for v in gen(True):
        out.append(v + junk() * 0)
    print("convs gen", out, list(gen(False))[-2:])
    got: list[str] = []
    for ch in gen_views(2):
        got.append(ch + str(junk() * 0))
    print("convs views", got)
