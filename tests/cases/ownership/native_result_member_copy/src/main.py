# A member of a bodyless binding's by-value record result (`-> Own[R]`) is no
# copy of anything the caller holds: no warning at an owning sink, a scalar
# member and a tuple member alike.
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


# The C++ returns a new `R` by value, so the stub says `-> Own[R]`, as its
# TPy twin would: a member read off the fresh result is no copy of `r`'s.
@native("nb::copy_of")
def copy_of(r: R) -> Own[R]: ...


# A bare `-> R` is a reference, as on the TPy twin: a member of it is the
# argument's own, so an owning sink copies it and warns.
@native("nb::same_of")
def same_of(r: R) -> R: ...


def main() -> None:
    r = R(A(1), A(2))
    xs: list[A] = []
    ys: list[tuple[A, int32]] = []
    # The scalar member and its tuple twin: taken from the fresh result.
    xs.append(copy_of(r).item)  # tpyc: ok
    ys.append(copy_of(r).pair)  # tpyc: ok
    xs[0].n = 5
    ys[0][0].n = 6
    print("scalar", r.item.n, xs[0].n)
    print("tuple", r.pair[0].n, ys[0][0].n)
    # The bare result's members: copies of `r`'s, warned.
    xs.append(same_of(r).item)  # tpyc: warning(/copies A into owned storage/)
    ys.append(same_of(r).pair)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    xs[1].n = 7
    ys[1][0].n = 8
    print("bare", r.item.n, xs[1].n, r.pair[0].n, ys[1][0].n)


main()
