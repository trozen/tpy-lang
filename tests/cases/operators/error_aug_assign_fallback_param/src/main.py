# A parameter of a reference type cannot be rebound (see the `x += v` on an
# `__add__`-only record entry in docs/LANGUAGE_FEATURES.md). `v += k` with no
# __iadd__ falls back to __add__, which rebinds `v`, so it is refused as
# `v = v + k` is.
from tpy import Own, int32


class V:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[V]":
        return V(self.n + k)


def bump(v: V) -> None:
    # the subject: a rebinding += on a parameter
    v += 1  # tpyc: error(/Cannot reassign parameter 'v' of type 'V'/)
    print(v.n)


def main() -> None:
    x = V(1)
    bump(x)
    print(x.n)


main()
