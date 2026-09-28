# Subclass `__init__`s that owe no parent call (protocol bases, an
# annotation-only parent, no own `__init__`), `Parent.__init__(self, ...)`
# when inherited, and a multi-base class calling a native and a TPy base.
from typing import Protocol

from tpy import int32, ValueType


class Tagged:
    tag: str

    def __init__(self, tag: str) -> None:
        print("tagged init", tag)
        self.tag = tag


class TaggedError(Exception, Tagged):
    code: int32

    # multi-base: the native base's `__init__` is owed like the TPy one's
    def __init__(self, code: int32) -> None:  # tpyc: ok
        Exception.__init__(self, f"code {code}")
        Tagged.__init__(self, "t")
        self.code = code


class Sized(Protocol):
    def size(self) -> int32: ...


class Money(ValueType, Sized):
    cents: int32

    # protocol bases only: no parent class has an initializer to call
    def __init__(self, cents: int32) -> None:  # tpyc: ok
        self.cents = cents

    def size(self) -> int32:
        return self.cents


class Point:
    x: int32
    y: int32


class Point3(Point):
    z: int32

    # annotation-only parent: nothing to call
    def __init__(self, x: int32, y: int32, z: int32) -> None:  # tpyc: ok
        self.x = x
        self.y = y
        self.z = z


class Counter:
    count: int32

    def __init__(self, start: int32) -> None:
        print("counter init", start)
        self.count = start


class Ticker(Counter):
    pass


class Named(Ticker):
    name: str

    def __init__(self, name: str, start: int32) -> None:
        # the explicit form reaches the grandparent's inherited `__init__`
        Ticker.__init__(self, start)  # tpyc: ok
        self.name = name


class Child(Counter):
    # no own `__init__`: it inherits `Counter.__init__`
    def bump(self) -> None:
        self.count += 1


def main() -> None:
    try:
        raise TaggedError(7)
    except TaggedError as e:
        print("multi-base native:", e.code, e.tag, str(e))
    m = Money(250)
    print("protocol bases:", m.size())
    p = Point3(1, 2, 3)
    print("annotation-only:", p.x, p.y, p.z)
    n = Named("n", 5)
    print("explicit:", n.name, n.count)
    c = Child(8)
    c.bump()
    print("inherited:", c.count)


main()
