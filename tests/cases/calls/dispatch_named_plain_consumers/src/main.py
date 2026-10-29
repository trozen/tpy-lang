# What C++ calls on its own over a class with @dispatch sets -- sorted's `<`,
# print's `__str__`, a protocol-bounded body, a @dynamic adapter, a callable
# object -- reaches the plain-name forwarders and behaves as before.
from typing import Callable, Protocol
from tpy import dispatch, dynamic, int32


class Key:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    @dispatch
    def __lt__(self, o: "Key") -> bool:
        return self.v < o.v

    @dispatch
    def __lt__(self, o: int32) -> bool:
        return self.v < o

    def __str__(self) -> str:
        return f"Key({self.v})"


class Sink(Protocol):
    def put(self, x: int32) -> int32: ...


class Box:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    @dispatch
    def put(self, x: int32) -> int32:
        self.total += x
        return self.total

    @dispatch
    def put(self, x: str) -> int32:
        self.total += len(x)
        return self.total


def feed[S: Sink](s: S) -> int32:
    # A protocol-bounded body calls the plain name.
    return s.put(5)


@dynamic
class Shape(Protocol):
    def area(self) -> int32: ...


class Sq:
    side: int32

    def __init__(self, side: int32) -> None:
        self.side = side

    def area(self) -> int32:
        return self.side * self.side

    @dispatch
    def scale(self, k: int32) -> None:
        self.side *= k

    @dispatch
    def scale(self, k: str) -> None:
        self.side += len(k)


def total_area(s: Shape) -> int32:
    return s.area()


class Adder:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    @dispatch
    def __call__(self, x: int32) -> int32:
        return self.base + x

    @dispatch
    def __call__(self, x: str) -> int32:
        return self.base + len(x)


def apply(f: Callable[[int32], int32]) -> int32:
    return f(2)


def main() -> None:
    ks = [Key(3), Key(1), Key(2)]
    # sorted: the friend `<` shim calls the plain-name forwarder.
    print("sorted:", [str(k) for k in sorted(ks)])  # tpyc: ok
    print("print:", ks[0])  # tpyc: ok
    b = Box()
    # protocol bound: the template body reaches the plain name.
    print("protocol:", feed(b), b.total)  # tpyc: ok
    sq = Sq(2)
    sq.scale(3)
    # @dynamic adapter: the slot is a single def beside a set of another name.
    print("dynamic:", total_area(sq))  # tpyc: ok
    a = Adder(10)
    # Callable: the callable object's `operator()` forwards to the plain name.
    print("callable:", apply(a), a(1), a("xyz"))  # tpyc: ok


main()
