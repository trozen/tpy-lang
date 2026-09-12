# Negative test: when the body mutates through the slot, inference does
# not fire and slots stay non-const so the mutation compiles.
from tpy import int32


class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def bump_first(p: tuple[T | None, T | None]) -> None:  # tpyc: ok
    a, _ = p
    if a is not None:
        a.x = a.x + 100


def main() -> None:
    t1 = T(1)
    t2 = T(2)
    bump_first((t1, t2))
    print(t1.x)
    print(t2.x)


main()
