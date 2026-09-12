# A fixed-length comprehension of a @nocopy element builds a stack Array by
# aggregate construction -- no copy, no default-construct, no assign. The
# elements are borrowed by iteration, subscript, and param passing; @nocopy
# turns any silent copy into a compile error.
from tpy import int32, Array, nocopy


@nocopy
class Handle:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def bump_all(hs: Array[Handle, 4]) -> None:
    for h in hs:
        h.v += 10


def doubled(hs: Array[Handle, 4]) -> None:
    ys = [Handle(h.v * 2) for h in hs]  # tpyc: type(/Array\[Handle, 4\]/)
    print(ys[0].v, ys[3].v)


def main() -> None:
    xs = [Handle(i) for i in range(4)]  # tpyc: type(/Array\[Handle, 4\]/)
    total = 0
    for h in xs:
        total += h.v
    print(total)
    bump_all(xs)
    print(xs[0].v, xs[3].v)
    doubled(xs)


main()
