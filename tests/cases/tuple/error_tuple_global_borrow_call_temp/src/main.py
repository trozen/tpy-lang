# A tuple global bound from a borrowing call whose argument is a TEMPORARY:
# the result would point at a local of module init, so the write is refused,
# as the scalar global `V2 = pick(Box(90), W)` is.
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pick(a: Box, b: Box) -> tuple[Box, Box]:
    return (a, b)


W = Box(2)
P = (W, W)
# the temporary dies with module init; the global would outlive it.
P = pick(Box(90), W)  # tpyc: error(/reseat.borrow_tuple_source/)
print(P[0].n)
