# A TERNARY of two rvalues at an open `Own[T]` slot: a conditional binds as
# an lvalue reference, which the slot's move parameter cannot take.
from tpy import Int32, Own


class Wrap[T]:
    x: T

    def __init__(self, x: Own[T]) -> None:
        self.x = x


def wrap[T](v: Own[T]) -> Own[Wrap[T]]:
    return Wrap(v)


class Outer[T]:
    items: list[T]

    def __init__(self, items: Own[list[T]]) -> None:
        self.items = items

    def go(self, f: bool) -> Own[Wrap[T]]:
        # The argument is a ternary of two container-pop rvalues.
        return wrap(self.items.pop() if f else self.items.pop(0))  # tpyc: error(/expr\.call:call\.generic_arg_slot/)


def main() -> None:
    o = Outer([5, 6])
    w = o.go(True)
    print(w.x)


main()
