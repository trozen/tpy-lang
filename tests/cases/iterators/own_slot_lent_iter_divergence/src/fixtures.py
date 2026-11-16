# The element types, pass-through generic and owning-slot consumers the
# sections share.
from typing import Iterable, Iterator
from tpy import Own, int32, nocopy


def passthru[T](src: Iterable[T]) -> Iterator[T]:
    for x in src:
        yield x


@nocopy
class Tag:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Pt:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def bump(source: Iterable[Own[list[int32]]]) -> None:
    for x in source:
        x.append(99)


def bump_pt(source: Iterable[Own[Pt]]) -> None:
    for p in source:
        p.v += 100


def bump_pairs(source: Iterable[Own[tuple[int32, list[int32]]]]) -> None:
    for p in source:
        print("  pair", p[0])


def take(i: int32, x: Own[list[int32]]) -> Own[list[int32]]:
    x.append(i)
    return x


def keep(x: list[int32]) -> bool:
    return True
