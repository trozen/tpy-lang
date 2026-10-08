# A module GLOBAL of tuple type initialised from a tuple LITERAL with a fresh
# element: the literal parks in a static of its own layout, built in place,
# and the global is the tuple of the scalar globals' pointer slots aimed at
# it (`std::tuple<int32_t, Cell*>`), as `V = Cell(2)` is `Cell* V` at its
# parked `Cell`; a @nocopy element shows no copy is made. (A tuple of NAMES
# builds the pointer tuple in place, parking nothing.)
from tpy import int32, nocopy


@nocopy
class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


# The subject: both elements are fresh, so the literal is spelled in storage
# form already; the declared tuple type is what puts the slot in pointer repr.
g: tuple[int32, Cell] = (1, Cell(2))  # tpyc: ok


def main() -> None:
    # Mutating through the global slot and reading back proves the element is
    # aliased, not copied, at the lift.
    g[1].v = 9  # tpyc: ok
    print(g[0])
    print(g[1].v)


main()
