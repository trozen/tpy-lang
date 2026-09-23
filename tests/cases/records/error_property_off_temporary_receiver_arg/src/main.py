# The call-ARGUMENT position of a borrow-returning @property read off a
# TEMPORARY receiver. The slot would bind a reference into an object that dies
# at the end of this statement, while what the getter lent can OUTLIVE that
# receiver -- here it lends a GLOBAL, which CPython mutates through -- so the
# copy a by-value slot would take loses every later mutation silently.
# The stop is the rule's own tag, `arg.lends_from_temporary`, at the free-call
# family too (the container rvalue reaches the sink now that the hoisting
# row is shared with the record's).
# The full table -- one sink, one tag, one case each -- is in
# docs/PROPERTY_DESIGN.md, "A read off a TEMPORARY receiver: the sink table".
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


def mutate(xs: list[int32]) -> None:
    xs.append(9)


def main() -> None:
    mutate(mk().items)  # tpyc: error(/arg\.lends_from_temporary/)
    print(len(G))


main()
