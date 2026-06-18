# Inverse guard for the same-short-name union fix: the SAME record imported
# under two names (`Point` and `P2`, both shp.Point) shares one qname, so the
# union `Point | P2` must dedup to a single member -- not be kept as two. A
# single member means the field is read directly, with no isinstance narrowing.
from shp import Point
from shp import Point as P2


def get_x(p: Point | P2) -> int:
    return p.x


def main() -> None:
    print(get_x(Point(42)))


main()
