# A comprehension's `if` clause narrows the element it tests for what
# follows it, so the filtered optional access is proven and unchecked.
from typing import Optional
from tpy import int32


class Foo:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def main() -> None:
    items: list[Optional[Foo]] = [Foo(1), None]
    xs = [item.x for item in items if item is not None]  # tpyc: ok
    print(xs)


main()
