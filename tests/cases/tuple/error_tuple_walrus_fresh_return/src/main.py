# A walrus-bound tuple carries the same owns-fresh member hazard as a
# VarDecl binding: returning it by name would hand out a pointer to the
# freshly constructed member dying with the function.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def ret() -> tuple[Int32, Box]:
    if (t := (1, Box(5)))[0] > 0:
        return t  # tpyc: error(/owns a freshly constructed value/)
    return (0, Box(0))


def main() -> None:
    pass


main()
