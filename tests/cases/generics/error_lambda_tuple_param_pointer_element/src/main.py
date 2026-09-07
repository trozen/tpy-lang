# A lambda whose tuple parameter carries a POINTER-repr element (a record) at a
# still-open Fn slot: that parameter spelling is not one the closure renders.
from tpy import Comparable, Fn, Int32, Own


class Box:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def pick[T, K: Comparable](xs: list[T], f: Fn[[T], K]) -> Own[list[K]]:
    out: list[K] = []
    i = 0
    while i < len(xs):
        out.append(f(xs[i]))
        i += 1
    return out


def keys(pairs: list[tuple[Box, Int32]]) -> Own[list[Int32]]:
    return pick(pairs, lambda p: p[1])  # tpyc: error(/call\.generic_arg_shape/)


def main() -> None:
    ps: list[tuple[Box, Int32]] = [(Box(1), 5)]
    for n in keys(ps):
        print(n)


main()
