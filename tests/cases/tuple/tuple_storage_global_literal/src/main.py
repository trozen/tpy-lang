# A module GLOBAL of pointer-repr tuple type initialised from a fresh tuple
# LITERAL -- the storage slot and the literal already agree, so the
# `tuple_to_storage` lift over it is an identity wrap; this pins that spelling.
# Storage form is harmless HERE because both elements are fresh -- there is
# nothing to alias. The general rule is not: a tuple global built from an
# existing object copies it, and an alias into one follows a rebind
# (BUGS.md#global-tuple-ref-storage-form).
# Cell is @nocopy so a silent copy into the global slot is a build error, and
# main() mutates through the slot before reading, so the write is observed.
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
