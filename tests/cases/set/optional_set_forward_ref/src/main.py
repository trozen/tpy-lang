# Regression: `set[T] | None` field where T is declared LATER than the
# enclosing record. Exercises the `OptionalType` branch of the wider
# validate_type flag-forwarding fix and the codegen topo-sort's
# hash-element edge through OptionalType. The `own_set_forward_ref`
# sibling exercises the OwnType path; this one pins OptionalType.
from tpy import int32, uint64


class Holder:
    s: set["Point"] | None

    def __init__(self) -> None:
        self.s = None


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __hash__(self) -> uint64:
        return uint64(self.x)

    def __eq__(self, other: "Point") -> bool:
        return self.x == other.x


def main() -> None:
    initial: set[Point] = set()
    initial.add(Point(1))
    initial.add(Point(2))
    initial.add(Point(1))
    h = Holder()
    h.s = initial
    assert h.s is not None
    print(len(h.s))


main()
