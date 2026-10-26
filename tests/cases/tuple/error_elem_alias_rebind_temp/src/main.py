# A rebound local first bound to a record element of a TEMPORARY tuple stays
# refused: the reseatable pointer would point into a tuple that dies at the
# end of the statement (BUGS.md#tuple-lend-from-temporary-refused).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def mk() -> tuple[Own[Box], int32]:
    return (Box(1), 2)


def first_then_rebind(q: Box) -> None:
    a = mk()[0]  # tpyc: error(/not yet supported by C\+\+ code generation/)
    a.n = 5
    a = q
    a.n = 7
    print("first_then_rebind", q.n)


first_then_rebind(Box(3))
