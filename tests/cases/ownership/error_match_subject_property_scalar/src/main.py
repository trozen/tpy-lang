# A scalar subject read through a `@property` hop names stored storage the
# scalar tiers do not bind by reference: refused, as before the one subject
# admission (BUGS.md#match-subject-unbound-root-refused).
from tpy import int32


class Holder:
    n: int32

    def __init__(self) -> None:
        self.n = 1


class Owner:
    held: Holder

    def __init__(self) -> None:
        self.held = Holder()

    @property
    def h(self) -> Holder:
        return self.held


def main() -> None:
    o = Owner()
    match o.h.n:  # tpyc: error(/stmt\.match/)
        case 1:
            print("one")
        case _:
            print("other")


main()
