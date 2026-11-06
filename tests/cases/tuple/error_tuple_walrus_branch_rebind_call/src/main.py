# A branch's walrus rebinding a borrow tuple to an owning call is refused
# (BUGS.md#walrus-pointer-tuple-rebind-rejects).
from tpy import Own, int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    pair: tuple[int32, Box]

    def __init__(self, b: Box) -> None:
        self.pair = (1, b)


def make_pair(n: int32) -> Own[tuple[int32, Box]]:
    return (n, Box(n))


def use(h: Holder, c: bool) -> int32:
    if c:
        t = h.pair
        if t[0] > 0:
            return t[0]
    else:
        # The rebind to the owning call's tuple is the subject.
        if (t := make_pair(9))[0] > 0:  # tpyc: error(/this construct is not yet supported by C\+\+ code generation \(expr.walrus\)/)
            return t[0]
    return 0


print(use(Holder(Box(3)), False))
