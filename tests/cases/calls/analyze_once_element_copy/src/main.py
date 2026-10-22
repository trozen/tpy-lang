# A container literal argument an overload probe analyzed: the copy of its
# element is warned once, for the winner's slot, the loser probed first. The
# copy is the warned divergence -- the list holds its own object, so a
# mutation after the call is not seen through it (CPython stores the very
# object).
from typing import Any, cast
from tpy import Own, dispatch, int32


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


@dispatch
def keep(xs: int32) -> Own[list[B]]:
    return []


@dispatch
def keep(xs: Own[list[B]]) -> Own[list[B]]:
    return xs


@dispatch
def fa(xs: int32) -> int32:
    return 0


@dispatch
def fa(xs: Own[list[Any]]) -> Own[list[Any]]:
    return xs


def main() -> None:
    p = B(1)
    kept = keep([p])  # tpyc: warning(/copies B into owned storage/)
    # mutate after the boundary: the list's element is a copy
    p.v = 2
    print("element-copy:", kept[0].v, p.v)
    q = B(1)
    # an `Any` element slot copies its payload: warned once
    held = fa([q])  # tpyc: warning(/copies B into Any/)
    q.v = 2
    # mutate after the boundary: the held payload is a copy
    print("any-element-copy:", len(held), cast(B, held[0]).v, q.v)


main()
