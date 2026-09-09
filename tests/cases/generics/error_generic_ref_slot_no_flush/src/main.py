# docs/LANGUAGE_FEATURES.md: "In a position with no statement to hoist a needed
# temp into ... the call is rejected" -- the need here is the `T&` slot's.
from tpy import Int32, Own


class Cell:
    def __init__(self, n: Int32) -> None:
        self.n = n


def anyslot[T](name: str, v: T) -> bool:
    return name != ""


def mk_cell() -> Own[Cell]:
    return Cell(Int32(7))


def main() -> None:
    n = 0
    # The subject: the reference-typed slot's temp cannot be hoisted here.
    while anyslot("w", mk_cell()) and n < 1:  # tpyc: error(/not yet supported/)
        n += 1
    print(n)


main()
