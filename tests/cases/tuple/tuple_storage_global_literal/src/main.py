# A module GLOBAL of tuple type initialised from a fresh tuple LITERAL owns
# its fresh element: sema marks it `Own` on the binding, the slot is storage
# and the literal is spelled in storage form, no lift. (A tuple of NAMES is
# the other form -- a tuple of pointer slots that aliases; a mixed one from a
# call still copies, BUGS.md#global-tuple-ref-storage-form.)
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
