# User-defined generators preserve references via val_or_ref<T> when
# yield type is Ref[T]. Composition between user generators works correctly.
from tpy import int32, Fn
from typing import Iterable, Iterator

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

def my_map[T, U](fn: Fn[[T], U], it: Iterable[T]) -> Iterator[U]:
    for x in it:
        yield fn(x)

def my_enumerate[T](it: Iterable[T]) -> Iterator[tuple[int32, T]]:
    i: int32 = 0
    for x in it:
        yield (i, x)
        i += 1

def identity(p: Point) -> Point:
    return p

def double(v: int32) -> int32:
    return v * 2

def main() -> None:
    pts: list[Point] = [Point(1, 2), Point(3, 4)]
    vals: list[int32] = [10, 20]

    # my_map: non-value type
    for p in my_map(identity, pts):
        print(p)

    # my_map: value type
    for v in my_map(double, vals):
        print(v)

    # my_enumerate over list
    for i, p in my_enumerate(pts):
        print(i, p)

    # Composition: my_enumerate(my_map(...))
    for i, p in my_enumerate(my_map(identity, pts)):
        print(i, p)

    # Composition: builtin enumerate(my_map(...))
    for j, q in enumerate(my_map(identity, pts)):
        print(j, q)

    # Mutation through composed user generators proves reference preservation
    for k, r in my_enumerate(my_map(identity, pts)):
        r.x += 100
    for pt in pts:
        print(pt)

main()
