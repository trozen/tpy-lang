# Regression: a union-typed field initialized in __init__ from a bare
# alternative value (`A(1)`, not a pointer-variant) must NOT be wrapped
# with `to_value_variant` -- that helper expects a `std::variant<T*...>`
# source, and bare alternatives construct the value-variant directly.
class A:
    a: int
    def __init__(self, a: int) -> None:
        self.a = a


class B:
    b: int
    def __init__(self, b: int) -> None:
        self.b = b


class Holder:
    slot: A | B | None

    def __init__(self) -> None:
        self.slot = A(1)


def main() -> None:
    h = Holder()
    s = h.slot
    if isinstance(s, A):
        print(s.a)


main()
