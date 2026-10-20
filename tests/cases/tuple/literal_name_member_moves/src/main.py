# A last-use tuple NAME in a returned / yielded literal and the `u = copy(t)` opt-out
# warn nothing; the render still copies (BUGS.md#tuple-literal-name-member-silent-copy).
from typing import Iterator
from tpy import Own, int32, copy


class A:
    def __init__(self, x: int32) -> None:
        self.x = x


def bump(a: A) -> None:
    a.x = 5


# return: the last-use member takes the move, not warned.
def ret_last() -> tuple[tuple[Own[A], int32], int32]:
    t = (A(1), 2)
    return (t, 3)  # tpyc: ok


# return: the explicit copy and the last-use member, both silent. The copy is
# a separate local: `copy(t)` as the member does not lower here yet
# (BUGS.md#copy-tuple-name-position-rejects).
def ret_optout() -> tuple[tuple[Own[A], int32], tuple[Own[A], int32]]:
    t = (A(1), 2)
    u = copy(t)
    return (u, t)  # tpyc: ok


# yield: the last-use member takes the move, not warned. No consumer of a
# yielded nested tuple lowers today
# (BUGS.md#nested-own-tuple-result-consumers-reject), so the yields are pinned
# by annotations.
def yld_last() -> Iterator[tuple[tuple[Own[A], int32], int32]]:
    t = (A(1), 2)
    yield (t, 3)  # tpyc: ok


# yield: the explicit copy and the last-use member, both silent.
def yld_optout() -> Iterator[tuple[tuple[Own[A], int32], tuple[Own[A], int32]]]:
    t = (A(1), 2)
    u = copy(t)
    yield (u, t)  # tpyc: ok


def main() -> None:
    r = ret_last()
    w = r[0]
    bump(w[0])
    print("ret last", r[0][0].x)
    o = ret_optout()
    v = o[1]
    bump(v[0])
    print("ret optout", o[0][0].x, o[1][0].x)


main()
