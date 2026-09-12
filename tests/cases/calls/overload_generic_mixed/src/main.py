# A generic overloaded free function mixing a generic overload (pick[T](list[T]))
# and a non-generic one (pick(str)) under one generic impl: the non-generic
# overload must emit as a non-template inline def (else a C++ link error).
from tpy import int32, Comparable
from typing import overload


@overload
def pick[T: Comparable](xs: list[T]) -> int32: ...
@overload
def pick(xs: str) -> int32: ...
def pick[T: Comparable](xs: list[T] | str) -> int32:
    if isinstance(xs, str):
        return len(xs)
    n = 0
    for _ in xs:
        n += 1
    return n


def main() -> None:
    nums: list[int32] = [3, 1, 2]
    print(pick(nums))     # tpyc: ok  -- generic overload
    print(pick("hello"))  # tpyc: ok  -- non-generic overload


main()
