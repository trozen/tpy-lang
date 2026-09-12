# A record with multiple __call__ overloads: direct calls dispatch per
# arity, and the instance satisfies an Fn contract matching ONE of the
# overloads (previously an internal-error assert).
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
    use(a)


main()
