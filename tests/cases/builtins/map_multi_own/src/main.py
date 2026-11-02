# Multi-iterable map whose callback takes an `Own[Point]` element: the
# iterable feeding it is an owning element slot, handed over when dead or
# fresh, copied (and warned) when the caller keeps it.
from typing import Callable
from tpy import int32, Own

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def __eq__(self, other: "Point") -> bool:
        return self.x == other.x and self.y == other.y

def combine(p: Point, q: Own[Point]) -> Point:
    p.x = p.x + q.x
    return p

# `q` is read, not consumed: the "never consumed" warning on this line is right
# (an `Own[...]` callback is what makes the iterable an owning element slot).
def add(k: int32, q: Own[Point]) -> int32:  # tpyc: warning(/never consumed/)
    return k + q.x

def make() -> Own[list[Point]]:
    return [Point(1, 0), Point(2, 0)]

def main() -> None:
    # dead: first arg by reference (written through), second handed over.
    pts = [Point(1, 2), Point(3, 4)]
    extras = [Point(10, 0), Point(20, 0)]
    for p in map(combine, pts, extras):  # tpyc: ok
        pass
    print(pts[0].x)  # 11 (1 + 10, modified through reference)
    print(pts[1].x)  # 23 (3 + 20)

    # live: the caller keeps `kept`, so each element is copied.
    kept = make()
    print("live", list(map(add, [10, 20], kept)))  # tpyc: warning(/copies Point elements/)
    print("live", kept[0].x, len(kept))

    # fresh: the call's result is handed over.
    print("fresh", list(map(add, [10, 20], make())))  # tpyc: ok

    # empty local at the owning slot still deduces `list[Point]`. Through an
    # annotated callable: `add` passed directly over an empty list is refused
    # (BUGS.md#own-callback-empty-literal-rejects).
    fn: Callable[[int32, Own[Point]], int32] = add
    xs = []
    print("empty", list(map(fn, [1], xs)))  # tpyc: warning(/copies Point elements/)
    ys = [Point(5, 0)]
    zs = xs if len(ys) > 5 else ys
    print("empty", len(zs), xs == ys)

main()
