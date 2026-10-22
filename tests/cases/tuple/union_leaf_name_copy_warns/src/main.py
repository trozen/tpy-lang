# A tuple name's union leaf copied into owned storage warns as its plain-`A`
# twin does: the copy is the documented, warned divergence from CPython.
from tpy import int32


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


class B:
    def __init__(self, v: int32) -> None:
        self.v = v


def union_leaf() -> None:
    ts: list[tuple[A | B, int32]] = []
    ts.append((A(1), 2))
    for t in ts:
        # The list literal copies the `A | B` element of the loop variable.
        ys: list[tuple[A | B, int32]] = [t]  # tpyc: warning(/copies A \| B into owned storage \(tuple element 0\)/)
        print("union", len(ys))


def plain_twin() -> None:
    ps: list[tuple[A, int32]] = [(A(1), 3)]
    for p in ps:
        # The plain-`A` twin: the same copy, the same warning.
        qs: list[tuple[A, int32]] = [p]  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
        print("plain", len(qs), qs[0][0].v)


# Compiled, not called: no body builds a value-union tuple in a lowered body
# yet (its tuple literal rejects).
def value_union(v: tuple[int32 | str, int32]) -> None:
    # A value union copies no object: nothing to warn.
    ws: list[tuple[int32 | str, int32]] = [v]  # tpyc: ok
    print("value", len(ws))


def main() -> None:
    union_leaf()
    plain_twin()


main()
