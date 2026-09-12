# Plain `tuple[T, T]` param with read-only body is inferred const:
# slots become `const T&`, matching the single-`T -> const T&`
# inference. Mirrors tuple-of-Optional behavior.
from tpy import int32


class T:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def show(p: tuple[T, T]) -> None:  # tpyc: ok
    a, b = p
    print(a.x + b.x)


def bump(p: tuple[T, T]) -> None:  # tpyc: ok
    # mutates through slot -- slots stay as `T&` (mutable).
    a, b = p
    a.x = a.x + 100


def main() -> None:
    t1 = T(1)
    t2 = T(2)
    show((t1, t2))
    bump((t1, t2))
    print(t1.x)


main()
