# Same hazard at a return boundary: binding a tuple with a durable reference
# member to a local then returning it copies the member; rejected at sema.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def make(b: Box) -> tuple[Int32, Box]:
    t = (1, b)
    return t  # tpyc: error(/cannot yet alias it across a return/)


def main() -> None:
    b = Box(Int32(7))
    pair = make(b)
    print(pair[1].val)


main()
