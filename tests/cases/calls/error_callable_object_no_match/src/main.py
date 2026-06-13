# No __call__ overload satisfies the expected signature: located error
# listing the candidates (previously an internal-error assert when more
# than one overload existed).
from typing import overload
from tpy import Fn, Int32, StrView


class Adder:
    @overload
    def __call__(self, x: Int32) -> Int32: ...

    @overload
    def __call__(self, x: Int32, y: Int32) -> Int32: ...

    def __call__(self, x: Int32, y: Int32 = 0) -> Int32:
        return x + y


def use(f: Fn[[StrView], Int32]) -> None:
    print(f("hi"))


def main() -> None:
    use(Adder())  # tpyc: error(/no '__call__' overload .*candidates:/)


main()
