# A ternary of an existing list and a fresh one coerced to a `Span`
# parameter: a view of the fresh arm's slot could outlive the block, so the
# coercion takes no slot and the argument is refused
# (BUGS.md#reference-ternary-position-gaps).
from tpy import int32, Span


def total(xs: Span[int32]) -> int32:
    s = 0
    for x in xs:
        s += x
    return s


def f(xs: list[int32], c: bool) -> None:
    print(total(xs if c else [1, 2]))  # tpyc: error(/ifexpr\.ref_mixed_position/)


def main() -> None:
    f([5], True)


main()
