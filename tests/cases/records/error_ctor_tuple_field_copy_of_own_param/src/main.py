# `copy()` of an Own PARAM as a tuple-field element in a constructor's member
# init: the copy row takes plain params, so this member init rejects.
from tpy import Int32, Own, copy


class Box:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class M:
    pair: tuple[Int32, Box]
    other: Box

    def __init__(self, b: Own[Box]) -> None:
        self.pair = (1, copy(b))  # tpyc: error(/mil/)
        self.other = b


def main() -> None:
    m = M(Box(3))
    print(m.pair[0], m.other.v)


main()
