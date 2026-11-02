# A record with multiple __call__ overloads: direct calls dispatch per
# arity, and the instance reaches an Fn parameter through a lambda, since an
# object with several __call__ variants is refused at a callback slot.
from typing import overload
from tpy import Fn, int32


class Adder:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    @overload
    def __call__(self, x: int32) -> int32: ...

    @overload
    def __call__(self, x: int32, y: int32) -> int32: ...

    def __call__(self, x: int32, y: int32 = 0) -> int32:
        return self.base + x + y


def use(f: Fn[[int32], int32]) -> None:
    print(f(5))


def main() -> None:
    a = Adder(10)
    print(a(1))
    print(a(1, 2))
    use(lambda x: a(x))


main()
