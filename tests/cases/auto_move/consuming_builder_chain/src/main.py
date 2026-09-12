# `return self` in a CONSUMING method relocates the receiver -- in a consuming
# method `self` IS the owner at its last use, so the owning return moves it
# (`return std::move((*this));`) with no copy and no diagnostic. Every receiver
# spelling a builder chain uses is covered: constructor temporary, owning free
# call, owning method call on a named param. The `@nocopy` leg is the proof
# that the move is real -- a copy there would be a compile error.
from typing import Self
from tpy import int32, Own, nocopy


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    # The subject: a consuming builder step handing the receiver back.
    def updated(self: Own[Self]) -> Own[Self]:
        self.x += 1
        return self  # tpyc: ok


def make(n: int32) -> Own[Point]:
    return Point(n).updated()  # tpyc: ok


def owned(n: int32) -> Own[Point]:
    return make(n).updated()  # tpyc: ok


class Factory:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def make(self) -> Own[Point]:
        return Point(self.n)


def owned_method(f: Factory) -> Own[Point]:
    return f.make().updated()  # tpyc: ok


@nocopy
class Ticket:
    id: int32

    def __init__(self, id: int32) -> None:
        self.id = id

    # A copy here cannot compile, so this leg pins that the return MOVES.
    def stamped(self: Own[Self]) -> Own[Self]:
        self.id += 100
        return self  # tpyc: ok


def issue(n: int32) -> Own[Ticket]:
    return Ticket(n).stamped()  # tpyc: ok


def main() -> None:
    p = make(1)
    # The returned value is the frame's own object, so a mutation after the
    # boundary is what both sides read back.
    p.x = 10
    print(p.x, owned(1).x, owned_method(Factory(5)).x)
    # Chained steps accumulate on one object.
    print(Point(1).updated().updated().x)
    t = issue(7)
    t.id += 1
    print(t.id)


main()
