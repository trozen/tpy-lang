# Same durable member at a return boundary: binding a tuple with a durable
# reference member to a local then returning it now ALIASES the member (pointer
# borrow form). Mutating the returned element is visible in the caller's object.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def make(b: Box) -> tuple[Int32, Box]:
    t = (1, b)
    return t


def main() -> None:
    b = Box(7)
    pair = make(b)
    pair[1].val = 99
    print(b.val)


main()
