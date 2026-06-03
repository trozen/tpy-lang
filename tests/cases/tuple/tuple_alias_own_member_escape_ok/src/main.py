# Regression guard: the Own[T] tuple-element escape survives an alias. The alias
# inherits the fresh-member fact, but the boundary re-check against the declared
# Own[] return type exempts the element (moved by value), so binding then
# returning via an alias must NOT be false-rejected.
from tpy import Int32, Own


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def make() -> tuple[Own[Box], Int32]:
    b = Box(5)
    pair = (b, 0)
    u = pair
    return u


def main() -> None:
    got, n = make()
    print(got.val + n)


main()
