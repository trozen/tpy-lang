# A tuple local holding an owned element beside a borrowed one, returned into
# Own element slots, warns the borrowed copy and rejects: no whole-tuple lift.
from tpy import int32, Own, nocopy


@nocopy
class N:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def f(p: P) -> tuple[Own[N], Own[P]]:
    t = (N(1), p)
    # Copying `t` whole would copy the N it owns, which cannot be copied.
    return t  # tpyc: warning(/tuple element 1\)/) error(/return.tuple_source/)


def main() -> None:
    n, q = f(P(2))
    print(n.v, q.x)


main()
