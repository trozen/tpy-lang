# A frame `__iter__` that iterates `self` is a 1-cycle: the frame would store
# its own iterator by value (`frame_slot<iter_type_t<Bag>>`), which is
# infinite-size. The emit-ordering sort keeps the self-edge, so this is caught
# as a clean located diagnostic rather than a raw C++ incomplete-type error --
# the boundary this pins is that the same-module gate on those edges does not
# gate away a REAL one.
#
# The diagnostic's wording ("delegated generator source") is shared with the
# `__for_src` delegation shape and reads slightly off for an `__iter__`
# embedding; pinned as-is rather than reworded here.
from typing import Iterator
from tpy import Int32


class Bag:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [1]

    def __iter__(self) -> Iterator[Int32]:  # tpyc: error(/recursive generator delegation/)
        for x in self:
            yield x
            yield x


def main() -> None:
    for v in Bag():
        print(v)


main()
