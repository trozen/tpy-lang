# The owning-storage fact flows through a ternary binding (the result
# aliases either arm), so laundering owning-call locals through
# `v = t if c else u` does not evade the borrow-form return rejection.
from tpy import int32, Own


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def make_pair(v: int32) -> Own[tuple[int32, Box]]:
    return (v, Box(v))


def pick(c: bool) -> tuple[int32, Box]:
    t = make_pair(5)
    u = make_pair(6)
    v = t if c else u
    return v  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
