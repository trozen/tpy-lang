# The adjacent shape to a guarded union arm's field capture in a suspending
# arm: capturing a REFERENCE-typed field. The frame slot copies, so writes
# through the capture would not reach what the subject holds -- rejected
# rather than emitted as a silent divergence from CPython.
from typing import Iterator
from tpy import Int32


class Pal:
    lives: Int32

    def __init__(self, lives: Int32) -> None:
        self.lives = lives


class Cat:
    pal: Pal

    def __init__(self, pal: Pal) -> None:
        self.pal = pal


class Dog:
    lives: Int32

    def __init__(self, lives: Int32) -> None:
        self.lives = lives


def guarded(a: Cat | Dog) -> Iterator[Int32]:  # tpyc: error(/not yet supported.*res.match_binding/)
    match a:
        # `f` names a reference-typed field, so the frame slot would copy it.
        case Cat(pal=f) if f.lives > 1:
            f.lives = 11
            yield 1
        case _:
            yield 0


def main() -> None:
    c = Cat(Pal(5))
    for v in guarded(c):
        print(v)
    print(c.pal.lives)


main()
