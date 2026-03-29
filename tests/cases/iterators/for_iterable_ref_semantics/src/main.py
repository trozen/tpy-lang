# Iterable[T] and Iterator[T] preserve reference semantics for non-value types.
# Mutations through the loop variable should be visible in the original container.
from typing import Iterable, Iterator

class Point:
    x: int
    y: int
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

def mutate_via_iterable(items: Iterable[Point]) -> None:
    for p in items:
        p.x += 100

def mutate_via_iterator(it: Iterator[Point]) -> None:
    for p in it:
        p.y += 200

def gen_double_x(items: Iterable[Point]) -> Iterator[int]:
    for p in items:
        p.x *= 2
        yield p.x

def main() -> None:
    # Iterable[T] reference semantics
    pts: list[Point] = [Point(1, 2), Point(3, 4)]
    mutate_via_iterable(pts)
    print(pts[0].x, pts[0].y)
    print(pts[1].x, pts[1].y)

    # Iterator[T] reference semantics (iter() returns native_iterator)
    pts2: list[Point] = [Point(10, 20), Point(30, 40)]
    mutate_via_iterator(iter(pts2))
    print(pts2[0].x, pts2[0].y)
    print(pts2[1].x, pts2[1].y)

    # Generator over Iterable[T] -- mutations visible
    pts3: list[Point] = [Point(5, 6)]
    for v in gen_double_x(pts3):
        print(v)
    print(pts3[0].x)

    # Dict iteration through Iterable[str]
    d: dict[str, int] = {"a": 1, "b": 2}
    keys: list[str] = []
    for k in d:
        keys.append(k)
    print(keys[0], keys[1])

main()
