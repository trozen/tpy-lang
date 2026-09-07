# `Prio(n)` on an IntEnum resolves the from-value template, alongside the
# ordinary member reads, comparisons and the enum field write.
from enum import Enum, IntEnum
from tpy import Int32


class Color(Enum):
    RED = 1
    GREEN = 2


class Prio(IntEnum):
    LOW = 1
    HIGH = 5


class Holder:
    c: Color

    def __init__(self) -> None:
        self.c = Color.RED


def pick(c: Color) -> Color:
    x = Color.GREEN
    if c == Color.RED:
        return x
    return c


def prios(p: Prio, n: Int32) -> bool:
    ok = p >= Prio.HIGH
    m = Prio(n)  # the from-value template
    if p == m:
        return True
    return ok


def flip(h: Holder) -> Color:
    h.c = Color.GREEN
    r = pick(h.c)
    return r


def main() -> None:
    h = Holder()
    print(flip(h), prios(Prio.LOW, 5))
    print(f"p={Prio.HIGH}")


main()
