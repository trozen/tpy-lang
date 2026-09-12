# An empty nested literal borrows its element type from the outer BRACE. A
# movable sibling switches the outer to the reserve+emplace helper, which
# deduces each element from its own argument -- so the bare `{}` has nothing to
# deduce from and the shape must keep rejecting.
from tpy import int32


def rows() -> int32:
    a: list[int32] = [1, 2]
    xs: list[list[int32]] = [a, []]  # tpyc: error(/expr\.container_literal/)
    return len(xs)


def main() -> None:
    print(rows())


main()
