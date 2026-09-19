# The METHOD-ARGUMENT position of a borrow-returning @property read off a
# TEMPORARY receiver. The FREE-call spelling of the same position stops one
# gate earlier, at the argument-shape gate (`call.arg_shape.container`)
# (`error_property_off_temporary_receiver_arg`, which also carries the tag
# table); a builtin stub method's argument reaches the sink itself, so this is
# where `arg.lends_from_temporary` is pinned.
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
    acc.append(mk().items)  # tpyc: error(/arg.lends_from_temporary/)
    print(len(acc))


main()
