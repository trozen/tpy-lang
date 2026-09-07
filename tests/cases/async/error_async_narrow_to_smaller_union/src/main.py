# `isinstance(a, (Dog, Cat))` on a three-member union narrows to a SMALLER
# union across a suspension: there is no single extraction local to resume
# under, so this rejects.
import asyncio
from tpy import Int32


class Dog:
    def sound(self) -> str:
        return "woof"


class Cat:
    def sound(self) -> str:
        return "meow"


class Bird:
    def sound(self) -> str:
        return "tweet"


async def step(n: Int32) -> Int32:
    return n + 1


async def pick(a: Dog | Cat | Bird) -> Int32:  # tpyc: error(/res\.narrowed_resume/)
    # The narrowed subject is still a union when the await resumes.
    if isinstance(a, (Dog, Cat)):
        await step(0)
        return 1
    return 2


def main() -> None:
    print(asyncio.run(pick(Dog())))


main()
