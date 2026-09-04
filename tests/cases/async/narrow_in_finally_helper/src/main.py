# A union narrowing established INSIDE the `finally` of a resumable body:
# the extraction alias belongs to the finally HELPER's own member function,
# which is walked statement by statement outside the frame's CFG blocks.
import asyncio
from typing import Iterator


class Dog:
    barks: int

    def __init__(self) -> None:
        self.barks = 0

    def bark(self) -> str:
        self.barks += 1
        return "woof"


class Cat:
    meows: int

    def __init__(self) -> None:
        self.meows = 0

    def meow(self) -> str:
        self.meows += 1
        return "meow"


async def a_assert(a: Dog | Cat) -> str:
    try:
        await asyncio.sleep(0)
    finally:
        assert isinstance(a, Dog)  # tpyc: ok
        # The alias is a reference into the caller's Dog, so both bumps of
        # `barks` are visible there -- a copy would hide the second one.
        print(a.bark())
        print(a.bark())
    return "done"


async def a_post_if(a: Dog | Cat) -> str:
    try:
        await asyncio.sleep(0)
    finally:
        if isinstance(a, Dog):  # tpyc: ok
            raise ValueError("dog")
        # Narrowed to Cat past the raising branch, for the rest of the helper.
        print(a.meow())
        print(a.meow())
    return "done"


async def a_nested(a: Dog | Cat, b: Dog | Cat) -> str:
    try:
        try:
            await asyncio.sleep(0)
        finally:
            assert isinstance(a, Cat)  # tpyc: ok
            print(a.meow())
    finally:
        # A second helper: its alias must not be confused with the inner one.
        assert isinstance(b, Dog)  # tpyc: ok
        print(b.bark())
    return "done"


def g_assert(a: Dog | Cat) -> Iterator[str]:
    try:
        yield "one"
    finally:
        assert isinstance(a, Cat)  # tpyc: ok
        print(a.meow())


def g_post_if(a: Dog | Cat) -> Iterator[str]:
    try:
        yield "two"
    finally:
        if isinstance(a, Cat):  # tpyc: ok
            raise ValueError("cat")
        print(a.bark())


async def amain() -> None:
    d = Dog()
    print(await a_assert(d))
    print(d.barks)

    c = Cat()
    print(await a_post_if(c))
    print(c.meows)

    c2 = Cat()
    d2 = Dog()
    print(await a_nested(c2, d2))
    print(c2.meows, d2.barks)


def main() -> None:
    c3 = Cat()
    for v in g_assert(c3):
        print(v)
    print(c3.meows)

    d3 = Dog()
    for v in g_post_if(d3):
        print(v)
    print(d3.barks)

    asyncio.run(amain())


main()
