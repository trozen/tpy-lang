# The METHOD-ARGUMENT position of a borrow-returning @property read off a
# TEMPORARY receiver. `list.append` keeps a copy of its `Own[list[int32]]`
# argument, which holds no pointer, so the read is the bound twin's borrow --
# `h = mk(); acc.append(h.items)` -- warned as a copy, and stops where the
# twin does, at the method argument-shape gate
# (BUGS.md#getter-result-slot-shape-rejects). A binding that keeps a view of
# its argument refuses it at `arg.lends_from_temporary`
# (records/error_stub_arg_keeps_view_of_temporary).
from typing import Iterator
from tpy import Own, int32

G: list[int32] = [1, 2]


class H:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    @property
    def items(self) -> list[int32]:
        return G


def mk() -> Own[H]:
    return H()


def main() -> None:
    acc: list[list[int32]] = []
    acc.append(mk().items)  # tpyc: error(/method\.arg_shape/)
    print(len(acc))


main()
