# A `T: ReferenceType` bound admits the types a function returns as a
# reference to the object itself, and the result aliases the argument.
from tpy import ComparableRef, ReferenceType


class Box:
    def __init__(self, n: int) -> None:
        self.n = n


class Rank:
    def __init__(self, n: int) -> None:
        self.n = n

    def __lt__(self, other: "Rank") -> bool:
        return self.n < other.n


def touch[T: ReferenceType](x: T) -> T:
    return x


# A generic body whose own T carries the bound satisfies the callee's.
def relay[T: ReferenceType](x: T) -> T:
    return touch(x)  # tpyc: ok


def lesser[T: ComparableRef](a: T, b: T) -> T:
    if b < a:
        return b
    return a


class Owner:
    n: int

    def __init__(self) -> None:
        self.n = 1

    def bump(self) -> None:
        # Method body, over the receiver itself.
        touch(self).n = 40  # tpyc: ok


def main() -> None:
    # Class instance: the write through the result reaches the source.
    b = Box(1)
    touch(b).n = 5  # tpyc: ok
    print("instance:", b.n)

    # list
    xs = [1, 2]
    ys = touch(xs)  # tpyc: ok
    ys.append(3)
    print("list:", xs)

    # dict
    d = {"a": 1}
    e = touch(d)  # tpyc: ok
    e["b"] = 2
    print("dict:", d)

    # set and bytearray
    s = {1}
    t = touch(s)  # tpyc: ok
    t.add(2)
    print("set:", len(s))
    ba = bytearray(b"ab")
    bb = touch(ba)  # tpyc: ok
    bb.append(99)
    print("bytearray:", len(ba))

    # Generic body forwarding its own bounded T.
    relay(b).n = 7
    print("relay:", b.n)

    o = Owner()
    o.bump()
    print("method:", o.n)

    # ComparableRef: the lesser argument itself comes back.
    r1 = Rank(3)
    r2 = Rank(2)
    lesser(r1, r2).n = 9  # tpyc: ok
    print("comparable_ref:", r1.n, r2.n)


main()
