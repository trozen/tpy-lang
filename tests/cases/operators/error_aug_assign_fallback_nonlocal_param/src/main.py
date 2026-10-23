# A parameter of a reference type cannot be rebound (see the `x += v` on an
# `__add__`-only record entry in docs/LANGUAGE_FEATURES.md). `v += k` with no
# __iadd__ falls back to __add__, which rebinds `v`; through `nonlocal`, on the
# enclosing function's parameter, it is refused as there.
from tpy import Own, int32


class V:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[V]":
        return V(self.n + k)


def f(v: V) -> None:
    def g() -> None:
        nonlocal v
        # the subject: a rebinding += on the enclosing function's parameter
        v += 10  # tpyc: error(/Cannot reassign parameter 'v' of type 'V'/)
    g()
    print(v.n)


def main() -> None:
    x = V(1)
    f(x)
    print("caller", x.n)


main()
