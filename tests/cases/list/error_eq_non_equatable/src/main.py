# `==` on lists whose element has no `__eq__` is refused at compile time:
# list's `__eq__` carries the element bound `index` / `count` carry.
from tpy import int32


class Foo:
    def __init__(self, x: int32) -> None:
        self.x = x


def main() -> None:
    a: list[Foo] = [Foo(1)]
    b: list[Foo] = [Foo(1)]
    print(a == b)  # tpyc: error(/'__eq__' requires its type argument to satisfy 'Equatable', but 'Foo' does not conform/)


main()
