# A tplib class, a class storing a factory and one declaring a @dynamic
# base, at `Own[P]`: `ArrayList` owns its elements, `Lazy` an owning factory.
from __future__ import annotations
from typing import Callable, Protocol
from tpy import Own, dynamic, int32
from tplib.array_list import ArrayList
from fixtures import P, mk


class Lazy[T]:
    f: Callable[[], T]

    def __init__(self, f: Callable[[], T]) -> None:
        self.f = f

    def make(self) -> T:
        return self.f()


@dynamic
class Sized(Protocol):
    def size(self) -> int32: ...


# declaring the @dynamic protocol its base, the class needs no adapter
class Bin[T](Sized):
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def size(self) -> int32:
        return len(self.items)


def use(s: Sized) -> int32:
    return s.size()


def array_list() -> None:
    a = ArrayList[Own[P], 4]()  # tpyc: warning(/copies P into owned storage in 'ArrayList.__getitem__'/)
    a.append(P(1))
    a.append(P(2))
    # `pop` hands the element over
    x = a.pop()
    x.n += 5
    print("al", x.n, len(a))


def lazy() -> None:
    own_mk: Callable[[], Own[P]] = mk
    z = Lazy[Own[P]](own_mk)  # tpyc: ok
    a = z.make()
    a.n = 55
    print("lazy", a.n, z.make().n)


def dyn_base() -> None:
    b = Bin[Own[P]]()  # tpyc: ok
    b.items.append(P(1))
    print("dyn-base", use(b))  # tpyc: ok


def main() -> None:
    array_list()
    lazy()
    dyn_base()
