# The adjacent shape to a field capture in a suspending `match` arm: capturing
# a REFERENCE type into the frame. The frame slot copies, so a mutation through
# the capture would not reach what the subject holds -- rejected rather than
# emitted as a silent divergence from CPython.
from typing import Iterator
from tpy import int32


class Cat:
    lives: int32

    def __init__(self, lives: int32) -> None:
        self.lives = lives


class Holder:
    pet: Cat

    def __init__(self, pet: Cat) -> None:
        self.pet = pet


def gen(h: Holder) -> Iterator[int32]:  # tpyc: error(/not yet supported.*res.match_binding/)
    match h:
        case Holder(pet=Cat(lives=v) as c):
            c.lives += 10
            yield v
            yield c.lives


def main() -> None:
    h = Holder(Cat(3))
    for x in gen(h):
        print(x)
    print(h.pet.lives)


main()
