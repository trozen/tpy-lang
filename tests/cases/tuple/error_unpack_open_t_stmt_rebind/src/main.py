# Error: a rebound open-T statement unpack target has no reference form to
# lower (BUGS.md#generic-borrow-local-rebind-view-instantiation).
from tpy import int32


def rebind[T](t: tuple[int32, T], y: T) -> int32:
    # the target `x` the next line rebinds
    i, x = t  # tpyc: error(/not yet supported by C\+\+ code generation \(stmt.tuple_unpack\)/)
    x = y
    return i


def main() -> None:
    print(rebind((1, "a"), "z"))


main()
