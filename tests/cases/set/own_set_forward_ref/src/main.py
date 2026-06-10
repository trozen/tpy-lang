# Regression: Holder declared BEFORE Point with a field `set["Point"]`
# (string forward ref). Exercises both halves of the fix:
#   1. Sema-side flag-forwarding -- pre-fix, validate_type fired the hashable
#      gate before Point's `__hash__`/`__eq__` were registered, false-rejecting
#      Point.
#   2. Codegen-side class emit ordering -- pre-fix, the topo sort considered
#      only inheritance dependencies, emitting Holder before Point (and its
#      `std::hash<Point>` specialization) which made C++ fail with
#      `static_assert(__is_invocable<const std::hash<Point>&, ...>)`. The
#      hash-element dependency edge added to `sort_records_by_inheritance`
#      now reorders Point ahead of Holder.
from tpy import Int32, UInt64


class Holder:
    s: set["Point"]

    def __init__(self) -> None:
        self.s = set()


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def __hash__(self) -> UInt64:
        return UInt64(self.x)

    def __eq__(self, other: "Point") -> bool:
        return self.x == other.x


def main() -> None:
    h = Holder()
    h.s.add(Point(1))
    h.s.add(Point(2))
    h.s.add(Point(1))
    print(len(h.s))


main()
