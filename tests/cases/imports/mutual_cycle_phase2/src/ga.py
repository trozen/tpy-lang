# A generator expression in a cycle member calling the peer's read-only
# function (analyzed later): the genexpr's own FunctionInfo must reach the
# component's mutation-fact propagation.
from tpy import int32
import gb


def total(rows: list[list[int32]]) -> int32:
    return sum(gb.size(r) for r in rows)


def one() -> int32:
    return 1
