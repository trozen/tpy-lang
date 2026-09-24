# A select of a name and a fresh object into a `T | None` local rejects: the
# fresh object has no slot a pointer local may outlive (BUGS.md#select-pointer-slot-gaps).
from tpy import int32, Own


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make() -> Own[C]:
    return C(10)


def local_mixed(c: bool) -> None:
    a = C(1)
    # The mixed pair at the Optional local.
    xs: C | None = a if c else make()  # tpyc: error(/not yet supported.*expr\.ifexpr/)
    if xs is not None:
        xs.n += 5
        print(xs.n, a.n)


def main() -> None:
    local_mixed(False)


main()
