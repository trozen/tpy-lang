# A value two members of one C++ alternative both take lands in neither:
# sema records no member for it, and the argument is refused rather than
# left to C++ (BUGS.md#exempt-union-landing-rejects).
from typing import Literal
from tpy import int32


def f(v: Literal["a"] | Literal["b"] | int32) -> bool:
    return isinstance(v, int32)


def main() -> None:
    print(f("a"))  # tpyc: error(/call.arg_shape.union/)


main()
