# An Optional-record subject whose arms mix a None test, an or-pattern over
# field conditions, and an `as` capture of the inner record. Leaf is @nocopy,
# so the capture binding the subject rather than copying it is what makes the
# case compile at all.
from tpy import Int32, nocopy


@nocopy
class Leaf:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def pick(x: Leaf | None) -> Int32:
    match x:
        case None:
            return 0
        # The or-alternatives compare the inner record's field.
        case Leaf(n=1) | Leaf(n=2):
            return 10
        case Leaf() as v:
            # `v` binds the subject; a copy here would not compile.
            return v.n
    return -1


def main() -> None:
    print(pick(Leaf(2)))
    print(pick(Leaf(7)))
    print(pick(None))


main()
