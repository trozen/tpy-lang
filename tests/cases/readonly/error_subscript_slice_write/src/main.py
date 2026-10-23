# A slice of a readonly receiver is a readonly view: a write through it is
# refused.
from tpy import int32, readonly


def touch(xs: readonly[list[int32]]) -> None:
    part = xs[0:1]
    part[0] = 9  # tpyc: error(/read-only/)


def main() -> None:
    touch([1, 2])


main()
