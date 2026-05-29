# `args[1:]` on a non-value *args populated via *list unpack -- direct-mode
# storage (contiguous T*). Same slice machinery as indirect-mode (returns
# another varargs preserving the mode), but exercises the direct path.
from tpy import Int32


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def sum_middle(*items: Box) -> Int32:  # tpyc: ok
    n: Int32 = 0
    for b in items[1:3]:
        n += b.val
    return n


def main() -> None:
    xs: list[Box] = []
    xs.append(Box(10))
    xs.append(Box(20))
    xs.append(Box(30))
    xs.append(Box(40))
    print(sum_middle(*xs))  # items[1:3] -> Box(20) + Box(30) = 50


main()
