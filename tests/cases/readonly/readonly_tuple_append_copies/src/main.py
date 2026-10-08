# An owning argument copies a readonly tuple whole, as it copies a readonly
# scalar: the appended element is independent of the source afterwards.
from tpy import int32, readonly


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def keep_scalar(b: readonly[Box], ys: list[Box]) -> None:
    ys.append(b)  # tpyc: warning(/copies Box into owned storage; use copy\(\)/)


def keep(t: readonly[tuple[Box, int32]], xs: list[tuple[Box, int32]]) -> None:
    # The subject: the same copy as the scalar above, per reference element.
    xs.append(t)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\); use copy\(\)/)


def main() -> None:
    b = Box(1)
    ys: list[Box] = []
    keep_scalar(b, ys)
    xs: list[tuple[Box, int32]] = []
    keep((b, 2), xs)
    b.n = 9
    print("source:", b.n)
    print("scalar copy:", ys[0].n)
    kept = xs[0]
    print("tuple copy:", kept[0].n, kept[1])


main()
