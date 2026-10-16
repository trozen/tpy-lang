# Regression: `dict[T, V]` field where T (the hashed key) is declared
# LATER than the enclosing record. Exercises the dict-key branch of both
# the sema flag-forwarding (the hashable gate at validate_type's NominalType
# branch fires on dict keys too) and the record order's hash-key edge
# (`_inheritance_order`, which walks typ.type_args[0] for dict keys).
from tpy import int32, uint64


class Holder:
    counts: dict["Point", int32]

    def __init__(self) -> None:
        self.counts = {}


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __hash__(self) -> uint64:
        return uint64(self.x)

    def __eq__(self, other: "Point") -> bool:
        return self.x == other.x


def main() -> None:
    h = Holder()
    h.counts[Point(1)] = 10
    h.counts[Point(2)] = 20
    h.counts[Point(1)] = 30
    print(len(h.counts))
    print(h.counts[Point(1)])


main()
