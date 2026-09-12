# User record inside tuple[T, U] as a dataclass field: exercises the
# resolve_type TupleType recursion and is_user_record propagation that
# post-Phase-F.1 need to walk into tuple element types so Point gets
# _module_qname and default-constructibility / equality dunders resolve.
from dataclasses import dataclass
from tpy import int32


@dataclass
class Point:
    x: int32
    y: int32


@dataclass
class PairHolder:
    pair: tuple[Point, Point]


def main() -> None:
    a = Point(1, 2)
    b = Point(3, 4)
    h = PairHolder((a, b))
    # Field access + printing + equality all go through is_user_record paths
    # on the tuple elements.
    print(h.pair[0].x)
    print(h.pair[1].y)
    print(h == PairHolder((Point(1, 2), Point(3, 4))))


main()
