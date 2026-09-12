# Named function reference is ambiguous against one Regime C candidate
# (multiple overloads of the function match its Fn shape) but the
# other candidate is rejected for an unrelated reason. The dry
# matcher's ambiguity SemanticError is stashed and surfaces only when
# no candidate ultimately wins.
from tpy import Fn, int32, dispatch


@dispatch
def apply[T, U](f: Fn[[T], U], xs: list[T]) -> int32:
    return int32(0)


@dispatch
def apply[T, U](f: Fn[[T, T], U], xs: list[T]) -> int32:
    return int32(0)


# Two 1-arg `convert` overloads with different param types. Both match
# the 1-param apply candidate's hint Fn[[int32], U] -- the second via
# int32 -> int numeric coercion -- making the dry matcher raise
# "Ambiguous function reference" against THAT candidate.
#
# The 2-param apply candidate has no matching 2-arg `convert` overload,
# so it rejects silently in the dry matcher (returns None).
#
# Result: no winner; the stashed ambiguity error surfaces.
@dispatch
def convert(x: int32) -> int32:
    return x * 2


@dispatch
def convert(x: int) -> int32:
    return int32(0)


def main() -> None:
    xs: list[int32] = [1, 2, 3]
    print(apply(convert, xs))  # tpyc: error(/ambiguous/i)


main()
