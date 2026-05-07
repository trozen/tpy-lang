# Test tuple[T | None, ...] of a record T as a function parameter.
# Lowers to const std::tuple<const T*, const T*>& -- pointer form, no copies.
# Construction at call site uses & for lvalues, nullptr for None.
from tpy import Int32


class T:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


def show(p: tuple[T | None, T | None]) -> None:
    a, b = p
    if a is not None:
        print(a.x)
    else:
        print("None")
    if b is not None:
        print(b.x)
    else:
        print("None")


def main() -> None:
    a = T(10)
    b = T(20)

    show((a, b))
    show((a, None))
    show((None, None))


main()
