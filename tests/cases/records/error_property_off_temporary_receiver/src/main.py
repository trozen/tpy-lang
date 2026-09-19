# A borrow-returning @property read off a TEMPORARY receiver, at a local decl.
# The getter hands back a BORROW, and what it borrows can OUTLIVE the receiver
# (a global, a longer-lived object) -- CPython aliases it. So the read is
# neither ownable (a copy would lose every later mutation, silently) nor
# bindable as a reference (it would dangle), and the decl has no render.
# The METHOD spelling of the same read is a conceded warn-and-emit tier
# (BUGS.md#readonly-borrow-of-temporary-receiver) and is not this subject.
# The for-each and call-argument positions of the same read are
# tests/cases/records/error_property_off_temporary_receiver_foreach.
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
    p = mk().items  # tpyc: warning(/borrows from temporary receiver/) error(/local_decl.lends_from_temporary/)
    print(len(p))


main()
