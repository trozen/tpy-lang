# A ternary return takes whichever arm the condition picks, so a borrow-form
# tuple arm rooted in dying local storage must be rejected like the direct
# form -- even when the other arm is durable.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, b)


def ret(h: Holder, c: bool) -> tuple[Int32, Box]:
    items: list[tuple[Int32, Box]] = [(1, Box(5))]
    t = items[0]
    return h.pair if c else t  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
