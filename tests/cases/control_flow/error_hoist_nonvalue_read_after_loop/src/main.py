# The inverse of hoist_nonvalue_inner_scope: a record bound in a loop-body
# if/else and READ AFTER the loop is sema's for-level hoist, whose non-value
# flavor still rejects (BUGS.md#two-site-loop-bind-read-after-loop). The
# if-level hoist must not admit it by accident.
from tpy import Int32


class Flat:
    def __init__(self, n: Int32) -> None:
        self.n = n


def main() -> None:
    for i in range(3):  # tpyc: error(/foreach.hoist_type/)
        if i % 2 == 0:
            f = Flat(i)
        else:
            f = Flat(i + 10)
    print(f.n)


main()
