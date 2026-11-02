# An object with several __call__ overloads at a callback slot: located
# error listing the candidates.
from typing import overload
from tpy import Fn, int32, StrView


class Adder:
    @overload
    def __call__(self, x: int32) -> int32: ...

    @overload
    def __call__(self, x: int32, y: int32) -> int32: ...

    def __call__(self, x: int32, y: int32 = 0) -> int32:
        return x + y


def use(f: Fn[[StrView], int32]) -> None:
    print(f("hi"))


def main() -> None:
    use(Adder())  # tpyc: error(/several '__call__' variants.*candidates:/)


main()
