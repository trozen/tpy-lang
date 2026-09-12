# A storage-form Own-element tuple passed to a `tuple&&` slot while the binding
# is STILL LIVE afterwards: that needs a decay-copy of the tuple, which the
# move-in arm does not render, so it is rejected.
from tpy import int32, Own, copy


class Box:
    val: int32

    def __init__(self, val: int32) -> None:
        self.val = val


def sink(p: tuple[Own[Box], int32]) -> int32:
    return p[1]


def still_live(b: Box) -> int32:
    pair = (copy(b), 0)
    r = sink(pair)  # tpyc: error(/call\.arg_shape/)
    return r + pair[1]


def main() -> None:
    print(still_live(Box(1)))


main()
