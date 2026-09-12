# An Own[T] source moved by NAME into a per-element-Own tuple slot, both passed
# (sink) and returned. A @nocopy element forces the boundary to be a MOVE
# (ownership transfer): a silent copy would be a compile error.
from tpy import int32, Own, nocopy


@nocopy
class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def sink(p: tuple[Own[Box], int32]) -> int32:
    return p[0].val + p[1]


def pass_by_name(ob: Own[Box]) -> int32:
    pair = (ob, 0)
    return sink(pair)


def return_by_name(ob: Own[Box]) -> tuple[Own[Box], int32]:
    pair = (ob, 1)
    return pair


def main() -> None:
    print(pass_by_name(Box(5)))
    got, n = return_by_name(Box(7))
    print(got.val + n)


main()
