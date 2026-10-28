# A select over an `Own[Box] | None` tuple element whose other arm is an
# owning binding keeps the `Own` marker, so it is refused as the owned name
# bind is (BUGS.md#own-optional-target-alias-rejects) -- never a silent copy.
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk(n: int32) -> tuple[Own[Box] | None, int32]:
    return (Box(n), n)


def peek(x: Box | None) -> int32:
    return -1 if x is None else x.n


def f(o: Own[Box] | None, c: bool) -> None:
    t = mk(2)
    x = t[0] if c else o  # tpyc: error(/decl.slot_type/)
    print(peek(x), peek(t[0]))


f(Box(3), False)
