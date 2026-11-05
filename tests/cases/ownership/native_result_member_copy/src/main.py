# A member of a bodyless binding's by-value record result warns its copy at an
# owning sink, a scalar member and a tuple member alike.
__tpy__ = {"includes": ["native_types.hpp"]}
from tpy import int32, Own
from tpy.extern import native


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class R:
    item: A
    pair: tuple[A, int32]

    def __init__(self, a: Own[A], b: Own[A]) -> None:
        self.item = a
        self.pair = (b, 1)


# Bare `-> R` on a FREE native means "by value, provenance unknown" (it
# may be a copy of `r` or `r` itself), so a member of its result warns at
# an owning sink; `-> Own[R]` would say "new object" and not warn. A method
# reads bare `-> R` as a borrow: the two conventions should be one
# (BUGS.md#free-native-bare-return-by-value).
@native("nb::copy_of")
def copy_of(r: R) -> R: ...


def main() -> None:
    r = R(A(1), A(2))
    xs: list[A] = []
    ys: list[tuple[A, int32]] = []
    # The scalar member and its tuple twin: both copy, both warned.
    xs.append(copy_of(r).item)  # tpyc: warning(/copies A into owned storage/)
    ys.append(copy_of(r).pair)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    xs[0].n = 5
    ys[0][0].n = 6
    print("scalar", r.item.n, xs[0].n)
    print("tuple", r.pair[0].n, ys[0][0].n)


main()
