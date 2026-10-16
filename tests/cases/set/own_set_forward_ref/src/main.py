# Regression: Holder declared BEFORE Point with a field `set["Point"]`
# (string forward ref). Exercises both halves of the fix:
#   1. Sema-side flag-forwarding -- pre-fix, validate_type fired the hashable
#      gate before Point's `__hash__`/`__eq__` were registered, false-rejecting
#      Point.
#   2. Codegen -- Holder's `ordered_set<Point>` needs `std::hash<Point>`
#      complete (`static_assert(__is_invocable<...>)`). It is declared ahead
#      of every struct and defined after Point's; the record order still
#      puts the key Point ahead of Holder (`_inheritance_order`'s hash-key
#      edge).
from tpy import int32, uint64


class Holder:
    s: set["Point"]

    def __init__(self) -> None:
        self.s = set()


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
    h.s.add(Point(1))
    h.s.add(Point(2))
    h.s.add(Point(1))
    print(len(h.s))


main()
