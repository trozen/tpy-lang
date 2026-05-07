# Test tuple[T | None, ...] of a record T as a function return.
# Lowers to std::tuple<T*, T*> -- pointer form, matching the top-level
# T | None -> T* convention. Destructuring binds T* locals; access via
# the pointer is a non-copying borrow.
from tpy import Int32


class T:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def both(a: T, b: T) -> tuple[T | None, T | None]:
    return (a, b)


def first_only(a: T) -> tuple[T | None, T | None]:
    return (a, None)


def neither() -> tuple[T | None, T | None]:
    return (None, None)


def main() -> None:
    a = T(1)
    b = T(2)

    p1, p2 = both(a, b)
    if p1 is not None:
        print(p1.x)
    if p2 is not None:
        print(p2.x)

    p3, p4 = first_only(a)
    if p3 is not None:
        print(p3.x)
    if p4 is None:
        print("p4 is None")

    p5, p6 = neither()
    if p5 is None and p6 is None:
        print("both None")


main()
