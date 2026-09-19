# The ITERATION position of a borrow-returning @property read off a TEMPORARY
# receiver. The loop would hold begin/end into what the getter lent while the
# receiver dies at the end of the setup statement -- and what was lent can
# OUTLIVE that receiver, so an owning capture would iterate a snapshot where
# CPython iterates the live container and an aliasing one would dangle. It is a
# located reject rather than either render; it used to compile silently into the
# use-after-scope BUGS.md#property-off-temporary-receiver-iterated names, whose
# remaining half is the METHOD spelling (warned, still miscompiling). No
# warning fires here: the getter returns a GLOBAL, so its result borrows from
# nothing the receiver owns and the provenance arm has no loan to file -- which
# is exactly why owning a copy would be wrong.
# The call-ARGUMENT position of the same read is the unannotated leg below,
# rejecting for the same reason one tag over; the decl position is
# tests/cases/records/error_property_off_temporary_receiver.
from tpy import Own, int32

G: list[int32] = [1, 2]


class H:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    # returns storage that OUTLIVES the receiver -- the reason a copy is wrong
    @property
    def items(self) -> list[int32]:
        return G


def mk() -> Own[H]:
    return H()


def mutate(xs: list[int32]) -> None:
    xs.append(9)


def main() -> None:
    n = 0
    for r in mk().items:  # tpyc: error(/foreach.iter_lends_from_temporary/)
        n += r
    print(n)
    # call argument: the same read one position over, unannotated because the
    # compile stops at the first error
    mutate(mk().items)


main()
