# Variable used only in break-path of a loop must still error.
# Liveness fixpoint propagates liveness across iterations, so the
# use inside the break branch is not a provable last use.
from tpy import Int32, Own


class Point:
    x: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def test() -> Int32:
    p = Point()
    p.x = 42
    result = Int32(0)
    for i in range(3):
        if i == 1:
            result = consume(p)  # tpyc: warning(/copies.*into owned storage/)
            break
    return result
