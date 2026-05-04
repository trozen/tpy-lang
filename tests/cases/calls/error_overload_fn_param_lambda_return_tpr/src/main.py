# V1 limit: when 2+ Fn-bearing-by-supplied candidates exist and the
# supplied Fn arg is a lambda whose return type would need body
# analysis to pin (a return TPR after partial inference), the
# candidate is rejected. Workaround: hoist the lambda to a typed local
# or use a named function.
from typing import overload
from tpy import Fn, Int32


@overload
def m[T, U](f: Fn[[T], U], xs: list[T]) -> Int32:
    return Int32(0)


@overload
def m[T, U](f: Fn[[T, T], U], xs: list[T]) -> Int32:
    return Int32(0)


def main() -> None:
    xs: list[Int32] = [1, 2, 3]
    # The 1-arg overload's U comes only from the lambda body; with 2+
    # Fn-bearing candidates we can't run body analysis per-candidate.
    # Both candidates get rejected.
    print(m(lambda a: a + Int32(1), xs))  # tpyc: error(/.*/)


main()
