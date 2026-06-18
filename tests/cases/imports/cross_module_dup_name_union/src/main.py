# Regression: two classes both named `Point`, imported from different modules,
# must stay distinct in a union (no short-name collapse). The union members
# carry distinct module qnames; isinstance dispatch picks the right member and
# the per-module field (`x` vs `lat`) is read. Each object is MUTATED through
# the union-typed param and observed afterward, so a silent copy or a collapse
# to one member would change the output (this is not a parity-blind read).
from screen import Point
from world import Point as WorldPoint


def shift(p: Point | WorldPoint) -> None:
    if isinstance(p, Point):
        p.x += 1
    if isinstance(p, WorldPoint):
        p.lat += 100


def main() -> None:
    s = Point(10)
    w = WorldPoint(20)
    shift(s)
    shift(w)
    print(s.x)
    print(w.lat)


main()
