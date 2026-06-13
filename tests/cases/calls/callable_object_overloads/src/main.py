# A record with multiple __call__ overloads: direct calls dispatch per
# arity, and the instance satisfies an Fn contract matching ONE of the
# overloads (previously an internal-error assert).
from typing import overload
from tpy import Fn, Int32


class Adder:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    @overload
    def __call__(self, x: Int32) -> Int32: ...

    @overload
    def __call__(self, x: Int32, y: Int32) -> Int32: ...

    def __call__(self, x: Int32, y: Int32 = 0) -> Int32:
        return self.base + x + y


def use(f: Fn[[Int32], Int32]) -> None:
    print(f(5))


def main() -> None:
    a = Adder(10)
    print(a(1))
    print(a(1, 2))
    use(a)


main()
