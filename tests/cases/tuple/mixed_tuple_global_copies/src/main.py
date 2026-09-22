# PINS A KNOWN-WRONG SHIPPING STATE (BUGS.md#global-tuple-ref-storage-form).
# A MIXED owned+borrow tuple GLOBAL bound from a call keeps the storage form
# and COPIES its borrowed element, where the local twin (`p = make_mixed(V)`)
# aliases it: `mixed[1].n = 44` is lost on V. Silent -- no diagnostic -- which
# is why this case exists and why it cannot run under CPython (prints 44).
# The subject line carries no annotation on purpose: `# tpyc: ok` would assert
# the silence is intended, when it is the defect.
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


V = Box(2)
mixed: tuple[Box, Box] = make_mixed(V)


def main() -> None:
    # module-level statement: the local twin below aliases, the global does not.
    p = make_mixed(V)
    p[1].n = 43
    print("local", V.n)
    V.n = 2
    # WRONG (silent, open): the mixed global copied, so this write is lost.
    mixed[1].n = 44
    print("mixed_write", V.n)


main()
