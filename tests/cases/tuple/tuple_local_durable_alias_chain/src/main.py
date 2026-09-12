# A durable reference member shared along a multi-step alias chain (s = r = t):
# returning the tail still aliases the member, so a post-boundary mutation
# reaches the caller's object.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def f(b: Box) -> tuple[int32, Box]:
    t = (1, b)
    r = t
    s = r
    return s


def main() -> None:
    b = Box(5)
    pair = f(b)
    pair[1].val = 99
    print(b.val)


main()
