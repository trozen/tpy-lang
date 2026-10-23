# A loop variable of a reference type cannot be rebound (see the `x += v` on
# an `__add__`-only record entry in docs/LANGUAGE_FEATURES.md). `v += k` with
# no __iadd__ falls back to __add__, which rebinds `v`; through `nonlocal`, on
# the enclosing function's loop variable, it is refused as there.
from tpy import Own, int32


class V:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[V]":
        return V(self.n + k)


def f(vs: list[V]) -> None:
    for v in vs:
        def g() -> None:
            nonlocal v
            # the subject: a rebinding += on the enclosing loop variable
            v += 10  # tpyc: error(/Cannot reassign loop variable 'v' of type 'V'/)
        g()
    print(vs[0].n)


def main() -> None:
    f([V(1)])


main()
