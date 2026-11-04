# Error: an open-T for-head unpack target rebound outside the loop body has
# no reference form to lower (BUGS.md#generic-borrow-local-rebind-view-instantiation).
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def rebind[T](ps: list[tuple[T, int32]], y: T) -> T:
    s = 0
    for x, n in ps:  # tpyc: error(/not yet supported by C\+\+ code generation \(stmt.tuple_unpack\)/)
        s += n
    else:
        # the rebind in the loop's else clause
        x = y
    return x


def main() -> None:
    r = rebind([(P(1), 1)], P(5))
    print(r.n)


main()
