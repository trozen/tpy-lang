# Method variant of error_generic_recursive_optional_return_dangle: a method
# returning a wrapper-shape value through an Optional[Wrapper] pointer-repr slot
# would emit `&(obj.method())` -- ill-formed C++. The TpyMethodCall branch of
# is_dangling_return must reject it the same way the free-function branch does.
from typing import Optional
from tpy import Int32, Own

type Tree[T] = T | list[Tree[T]]


class Factory:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    def make(self) -> Own[Tree[Int32]]:
        return [self.base, 1]


def g(f: Factory) -> Optional[Tree[Int32]]:
    return f.make()  # tpyc: error(/returned pointer would dangle/)


def main() -> None:
    g(Factory(5))


main()
