# Known limitation: alias=h creates T& ref, not movable even at last use.
# Future: move-through optimization (see MOVE_SEMANTICS_DESIGN.md).
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def bad() -> Own[Handle]:
    h = Handle()
    alias = h
    return alias  # tpyc: error(/@nocopy.*cannot be returned/)


def main():
    bad()


main()
