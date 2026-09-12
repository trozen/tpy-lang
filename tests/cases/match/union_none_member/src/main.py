# match over a union with a None member: `case None:` dispatches on the
# monostate variant index like any other alternative.
from tpy import int32


class A:
    v: int32

    def __init__(self) -> None:
        self.v = 1


class B:
    v: int32

    def __init__(self) -> None:
        self.v = 2


def pick(x: A | B | None) -> int32:
    match x:
        case None:
            return 0
        case A():
            return x.v
        case B():
            return x.v + 10


def main() -> None:
    print(pick(None))
    print(pick(A()))
    print(pick(B()))


main()
