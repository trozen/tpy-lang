# A loop variable of a reference type cannot be rebound, through `nonlocal`
# from a nested def as in the loop body (see docs/LANGUAGE_FEATURES.md).
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
            # the subject: a plain rebind of the enclosing loop variable
            v = v + 10  # tpyc: error(/Cannot reassign loop variable 'v' of type 'V'/)
        g()
    print(vs[0].n)


def main() -> None:
    f([V(1)])


main()
