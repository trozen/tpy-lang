# Field sub-patterns in a `match` whose arms suspend: a GUARDED union arm's
# field capture, and a nested class pattern under a union FIELD, whose
# extraction alias is an emit-drawn temp rather than a frame local.
import asyncio
from typing import Iterator
from tpy import Int32


class Cat:
    lives: Int32

    def __init__(self, lives: Int32) -> None:
        self.lives = lives


class Dog:
    lives: Int32

    def __init__(self, lives: Int32) -> None:
        self.lives = lives


class Holder:
    pet: Cat | Dog

    def __init__(self, pet: Cat | Dog) -> None:
        self.pet = pet  # tpyc: warning(/copies Cat \| Dog into field/)


def guarded(a: Cat | Dog) -> Iterator[Int32]:
    match a:
        case Cat(lives=v) if v > 3:  # a guarded arm's field capture
            yield v
            yield v + 1
        case _:
            yield 0


def guarded_cond(a: Cat | Dog, flag: bool) -> Iterator[Int32]:
    match a:
        case Cat(lives=9) if flag:  # a field CONDITION beside the guard
            yield 9
        case Dog(lives=v):
            yield v
        case _:
            yield -1


def nested(h: Holder) -> Iterator[Int32]:
    match h:
        case Holder(pet=Cat(lives=v)):  # the union field's extraction alias
            yield v
            yield v + 1
        case _:
            yield 0


def nested_shadow(h: Holder) -> Iterator[Int32]:
    # A frame local spelled like the aliased FIELD: the alias name derives
    # from the runtime base, so the two cannot collide.
    pet = Cat(1)
    yield pet.lives
    match h:
        case Holder(pet=Cat(lives=v)):
            yield v
        case _:
            yield 0
    yield pet.lives


async def a_guarded(a: Cat | Dog) -> Int32:
    match a:
        case Cat(lives=v) if v > 3:
            await asyncio.sleep(0)
            return v
        case _:
            return 0


async def amain() -> None:
    print(await a_guarded(Cat(5)))
    print(await a_guarded(Dog(1)))


def main() -> None:
    for v in guarded(Cat(5)):
        print(v)
    for v in guarded(Dog(9)):
        print(v)
    for v in guarded_cond(Cat(9), True):
        print(v)
    for v in guarded_cond(Dog(2), True):
        print(v)
    for v in nested(Holder(Cat(3))):
        print(v)
    for v in nested_shadow(Holder(Cat(7))):
        print(v)
    asyncio.run(amain())


main()
