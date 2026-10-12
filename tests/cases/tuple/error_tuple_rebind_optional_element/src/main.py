# A rebound tuple local whose owned element is an Optional has no reference
# local of its own for that element yet: refused in words at the binding
# that owns it (BUGS.md#tuple-rebind-optional-element-rejects).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make(n: int32) -> tuple[Own[Box | None], int32]:
    return (Box(n), n)


def main() -> None:
    p = make(1)  # tpyc: error(/rebinding 'p' while it owns element 0, an Optional, is not yet supported here/)
    p = make(9)
    a, n = p
    if a is not None:
        print(a.n, n)


main()
