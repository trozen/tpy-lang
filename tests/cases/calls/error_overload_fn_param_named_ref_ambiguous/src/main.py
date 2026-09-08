# Named function reference is ambiguous against one Regime C candidate
# (multiple overloads of the function match its Fn shape) but the
# other candidate is rejected for an unrelated reason. The dry
# matcher's ambiguity SemanticError is stashed and surfaces only when
# no candidate ultimately wins.
from typing import overload
from tpy import Fn, Int32


@overload
def apply[T, U](f: Fn[[T], U], xs: list[T]) -> Int32:
    return Int32(0)


@overload
def apply[T, U](f: Fn[[T, T], U], xs: list[T]) -> Int32:
    return Int32(0)


# Two 1-arg `convert` overloads with different param types. Both match
# the 1-param apply candidate's hint Fn[[Int32], U] -- the second via
# Int32 -> int numeric coercion -- making the dry matcher raise
# "Ambiguous function reference" against THAT candidate.
#
# The 2-param apply candidate has no matching 2-arg `convert` overload,
# so it rejects silently in the dry matcher (returns None).
#
# Result: no winner; the stashed ambiguity error surfaces.
@overload
def convert(x: Int32) -> Int32:
    return x * 2


@overload
def convert(x: int) -> Int32:
    return Int32(0)


def main() -> None:
    xs: list[Int32] = [1, 2, 3]
    print(apply(convert, xs))  # tpyc: error(/ambiguous/i)


main()
