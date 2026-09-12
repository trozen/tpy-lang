# No __call__ overload satisfies the expected signature: located error
# listing the candidates (previously an internal-error assert when more
# than one overload existed).
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
    use(Adder())  # tpyc: error(/no '__call__' overload .*candidates:/)


main()
