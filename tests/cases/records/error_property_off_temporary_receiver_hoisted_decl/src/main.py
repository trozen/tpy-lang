# The BRANCH-FIRST (hoisted) declaration of a borrow-returning @property read
# off a TEMPORARY receiver: a local first bound inside a `try` body is
# predeclared above it, so the slot outlives the statement that kills the
# receiver. The straight-line position is
# `error_property_off_temporary_receiver`; both take the same sink and the
# same tag, which is the point -- the verdict is the sink's, not the
# statement's.
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
    try:
        v = mk().items  # tpyc: error(/local_decl.lends_from_temporary/)
        print(len(v))
    except ValueError:
        print("no")


main()
