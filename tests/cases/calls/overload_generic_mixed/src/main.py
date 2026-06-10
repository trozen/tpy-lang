# A generic overloaded free function mixing a generic overload (pick[T](list[T]))
# and a non-generic one (pick(str)) under one generic impl: the non-generic
# overload must emit as a non-template inline def (else a C++ link error).
from tpy import Int32, Comparable
from typing import overload


@overload
def pick[T: Comparable](xs: list[T]) -> Int32: ...
@overload
def pick(xs: str) -> Int32: ...
def pick[T: Comparable](xs: list[T] | str) -> Int32:
    if isinstance(xs, str):
        return len(xs)
    n = 0
    for _ in xs:
        n += 1
    return n


def main() -> None:
    nums: list[Int32] = [3, 1, 2]
    print(pick(nums))     # tpyc: ok  -- generic overload
    print(pick("hello"))  # tpyc: ok  -- non-generic overload


main()
