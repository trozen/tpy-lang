# A Deref wrapper's own members stay on the wrapper, while a member it does
# not declare reaches the payload, under TPy and CPython alike.
from __future__ import annotations
from tpy import Deref, Own


class Point:
    x: int
    _y: int
    size: int

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self._y = y
        self.size = 0


class Handle(Deref[Point]):
    target: Point
    label: str

    def __init__(self, target: Own[Point], label: str) -> None:
        self.target = target  # the wrapper's own annotated field
        self.label = label

    def __deref__(self) -> Point:
        return self.target

    @property
    def size(self) -> int:
        raise AttributeError("no size")


class Inferred(Deref[Point]):
    def __init__(self, target: Own[Point]) -> None:
        self.target = target  # a field declared by this assignment alone

    def __deref__(self) -> Point:
        return self.target


class Peer(Deref[Point]):
    target: Point

    def __init__(self, target: Own[Point], others: list[Peer]) -> None:
        self.target = target
        for other in others:
            other.x = 42  # another instance's payload, written in a constructor

    def __deref__(self) -> Point:
        return self.target


def main() -> None:
    h = Handle(Point(1, 2), "a")
    print("read:", h.label, h.x, h._y)
    h.label = "b"  # rebinds the wrapper's field
    h.x = 10  # writes the payload's field
    h._y = 20  # an underscore name the wrapper does not declare is the payload's too
    print("write:", h.label, h.x, h.target.x, h._y, h.target._y)
    try:
        print(h.size)
    except AttributeError:
        print("property: raised")  # the wrapper's own property fails, no payload lookup

    i = Inferred(Point(3, 4))
    i.x = 30
    print("inferred:", i.x, i.target.x)

    no_peers: list[Peer] = []
    a = Peer(Point(5, 0), no_peers)
    peers = [a]
    b = Peer(Point(6, 0), peers)
    print("peer:", peers[0].x, peers[0].target.x, b.x)


main()
