# IntEnum unary minus: -p negates the underlying value (result is int, not Prio).
from enum import IntEnum
from tpy import Int32


class Prio(IntEnum):
    LOW = 2
    HIGH = 5


def neg(p: Prio) -> Int32:
    m = -p
    return m


def main() -> None:
    p = Prio.HIGH
    print(-p)
    print(neg(Prio.LOW))
    print(f"m={-p}")


main()
