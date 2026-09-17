# A borrow-returning @property read off a TEMPORARY receiver: the getter hands
# back a reference into an object destroyed at the end of the statement, so the
# decl has no render and rejects. The METHOD spelling of the same read is a
# conceded warn-and-emit tier (BUGS.md#readonly-borrow-of-temporary-receiver),
# and a for-each over it still lowers unwarned, iterating the destroyed object
# (BUGS.md#property-off-temporary-receiver-iterated). Neither is this subject.
from tpy import Own, int32


class H:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    @property
    def items(self) -> list[int32]:
        return self.xs


def mk() -> Own[H]:
    return H()


def main() -> None:
    p = mk().items  # tpyc: warning(/borrows from temporary receiver/) error(/decl.slot_type/)
    print(len(p))


main()
