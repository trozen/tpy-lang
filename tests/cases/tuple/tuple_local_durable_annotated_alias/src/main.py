# An explicitly-annotated alias binding (u: tuple[...] = t) shares the durable
# member like a plain alias: returning u aliases it, so a post-boundary mutation
# reaches the caller's object.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def f(b: Box) -> tuple[int32, Box]:
    t = (1, b)
    u: tuple[int32, Box] = t
    return u


def main() -> None:
    b = Box(5)
    pair = f(b)
    pair[1].val = 99
    print(b.val)


main()
