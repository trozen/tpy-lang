# Returning a borrow-form tuple held by a loop var whose iterable is a
# borrow-returning CALL: the lift takes element addresses into whichever
# source the call relayed, so a source that dies with the function is
# rejected. `pick` borrows from BOTH operands, only the first of which is
# local -- a loop var that kept just one source would miss it.
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def pick(a: list[tuple[int32, Box]],
         b: list[tuple[int32, Box]]) -> list[tuple[int32, Box]]:
    if len(a) > 0:
        return a
    return b


def ret(p: list[tuple[int32, Box]]) -> tuple[int32, Box]:
    local: list[tuple[int32, Box]] = [(1, Box(5))]
    for v in pick(local, p):
        return v  # tpyc: error(/storage owned by the function/)
    return p[0]


def main() -> None:
    pass


main()
