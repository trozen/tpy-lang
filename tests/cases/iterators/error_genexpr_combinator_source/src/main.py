# A comprehension over a builtin iterator combinator lowers, but the GENEXPR
# form must keep rejecting: its lambda spells the source's nested `iterator`
# typedef, which the combinator objects do not have.
from tpy import Int32


def f(xs: list[Int32]) -> Int32:
    return sum(x for x in reversed(xs))  # tpyc: error(/genexpr.iterable_shape/)


def main() -> None:
    print(f([1, 2]))


main()
