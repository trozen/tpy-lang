# Positions that refused a fresh argument because no statement could hold it
# in a named temp: it binds inline there now, as everywhere else.
from typing import Callable, Optional, Protocol
from tpy import dynamic, int32, Own, StrView
from tplib import Box, Rc
import pets
from pets import Dog


class Base:
    n: int32

    def __init__(self, xs: list[int32]) -> None:
        xs.append(1)
        self.n = len(xs)


class Child(Base):
    def __init__(self, n: int32) -> None:
        # base-init: a fresh list at a parameter the base writes
        super().__init__([n])  # tpyc: ok


@dynamic
class DynP(Protocol):
    def ping(self) -> int32: ...


class KMut:
    tag: int32

    def __init__(self, p: list[int32]) -> None:
        p.append(99)
        self.tag = len(p)

    def ping(self) -> int32:
        return self.tag


class Cell:
    def __init__(self, n: int32) -> None:
        self.n = n


def anyslot[T](name: str, v: T) -> bool:
    return name != ""


def mk_cell() -> Own[Cell]:
    return Cell(7)


def take_union(u: int32 | StrView | None) -> int32:
    return 0


class Collar:
    size: int32

    def __init__(self, size: int32) -> None:
        self.size = size


class Pup:
    collar: Optional[Collar]

    def __init__(self, c: Optional[Collar]) -> None:
        # a field holds its own collar: the copy of the caller's is warned
        self.collar = c  # tpyc: warning(/copies Collar \| None into field/)


class Cat:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag


def check(a: Pup | Cat) -> bool:
    return True


@dynamic
class Surface(Protocol):
    def hit(self) -> str: ...


class Mat(Surface):
    hits: int

    def __init__(self) -> None:
        self.hits = 0

    def hit(self) -> str:
        self.hits += 1
        return "mat" + str(self.hits)


class Chrome(Mat):
    def hit(self) -> str:
        self.hits += 1
        return "chrome" + str(self.hits)


class Tracer:
    def __init__(self) -> None:
        pass

    def run(self, m: Mat) -> str:
        return m.hit()


def ret(k: int32) -> tuple[bool, tuple[int32, int32]]:
    # a return: a union-converted argument two tuple levels deep
    return (True, (take_union(k), 1))  # tpyc: ok


def classify(n: int) -> str:
    match n:
        # a match guard: a union built from a fresh member, module-qualified
        case 1 if pets.code(Dog(4)) > 3:  # tpyc: ok
            return "big"
        case _:
            return "other"


def main() -> None:
    print("opened base-init", Child(3).n)
    # a nested constructor argument at a written slot
    rc: Rc[Box[DynP]] = Rc.new(Box(KMut([1, 2])))  # tpyc: ok
    print("opened nested ctor", rc.get().get().ping())
    n = 0
    # a while head: the generic `T&` slot's fresh argument
    while anyslot("w", mk_cell()) and n < 1:  # tpyc: ok
        n += 1
    print("opened while", n)
    print("opened guard", classify(1))
    # a tuple literal element at a declaration
    t = (True, take_union(n))  # tpyc: ok
    print("opened tuple", t[1])
    ok, inner = ret(n)
    print("opened return", ok, inner[0], inner[1])
    # a nested constructor inside a ternary arm, built only when taken
    print("opened ternary", check(Pup(Collar(2))) if n > 0 else False)  # tpyc: ok
    tr = Tracer()
    # a lambda body: a subclass temporary at a base parameter. The lambda
    # holds its own copy of `tr` (a Python closure would see later changes
    # to it): warned, and nothing changes `tr` after this, so the output
    # matches
    f: Callable[[], str] = lambda: tr.run(Chrome())  # tpyc: warning(/copies the object 'tr'/)
    print("opened lambda", f())
