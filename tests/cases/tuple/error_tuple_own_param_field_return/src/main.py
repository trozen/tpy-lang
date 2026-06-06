# Returning a borrow-form tuple read from an Own param's field: the lift
# would take element addresses into storage that dies with the param.
from tpy import Int32, Own


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, b)


def ret(h: Own[Holder]) -> tuple[Int32, Box]:
    return h.pair  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
