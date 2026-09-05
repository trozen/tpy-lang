# A container ELEMENT lvalue returns bare at a borrow slot, but a TUPLE
# element does not: std::get on a borrow-form tuple yields the element
# POINTER, so that shape keeps rejecting.
from tpy import Int32, Own


class Holder:
    pair: tuple[list[Int32], list[Int32]]

    def __init__(self, a: Own[list[Int32]], b: Own[list[Int32]]) -> None:
        self.pair = (a, b)

    def first(self) -> list[Int32]:
        return self.pair[0]  # tpyc: error(/return\.record_source\.subscript\.borrow/)


def main() -> None:
    h = Holder([1, 2], [3, 4])
    print(len(h.first()))


main()
