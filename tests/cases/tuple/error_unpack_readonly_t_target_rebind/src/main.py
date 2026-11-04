# Error: a `readonly[T]` for-head unpack target rebound in the loop body takes
# the scalar `for x in xs: x = y` loop variable's error at `readonly[T]`.
from tpy import int32, readonly


def pick[T](xs: readonly[list[tuple[T, int32]]], y: readonly[T]) -> readonly[T]:
    for x, n in xs:
        # the in-body rebind of the readonly head target
        x = y  # tpyc: error(/Cannot reassign loop variable 'x' of type 'readonly\[T\]'/)
        return x
    raise ValueError("empty")


def main() -> None:
    print(pick([("abc", 1)], "def"))


main()
