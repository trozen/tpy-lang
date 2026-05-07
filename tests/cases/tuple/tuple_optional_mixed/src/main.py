# Mixed tuple: some elements are reference Optional (T | None), others
# are value types or plain reference types. Pointer-form rules apply only
# to T | None slots; value slots stay as values, plain T slots stay as &.
from tpy import Int32


class T:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def f(a: T, b: T) -> tuple[T | None, Int32, T | None]:
    return (a, 42, b)


def show(p: tuple[T | None, Int32, T | None]) -> None:
    a, n, b = p
    if a is not None:
        print(a.x)
    print(n)
    if b is not None:
        print(b.x)


def main() -> None:
    t1 = T(1)
    t2 = T(2)
    show(f(t1, t2))
    show((t1, 99, None))


main()
