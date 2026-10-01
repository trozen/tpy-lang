# A mixed tuple local still read after the call copies its owned element into
# the `tuple[Own[Box], Box]` parameter and warns: the callee's write to p[0]
# lands on that copy (CPython shares the object -- the divergence the warning
# declares, hence no CPython run), while its write through p[1] still reaches
# the caller's `b`.
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk_box(b: Box) -> tuple[Own[Box], Box]:
    return (Box(9), b)


def write_both(p: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    p[0].n = 91
    p[1].n = 90
    return p[0].n


def local_live(b: Box) -> int32:
    t = mk_box(b)
    got = write_both(t)  # tpyc: warning(/copies tuple\[Own\[Box\], Box\] into owned storage/)
    # The copy took the write: `t[0]` still holds 9 (CPython: 91).
    return got + t[0].n


def main() -> None:
    b = Box(0)
    print("local-live", local_live(b), b.n)


main()
