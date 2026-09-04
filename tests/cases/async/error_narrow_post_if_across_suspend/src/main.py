# The adjacent shape to a post-`if` narrowing inside a finally helper: the
# same narrowing at a CFG block position whose scope spans a SUSPENSION. The
# alias would be declared in one resume case and read from another, so the
# construct is rejected rather than emitted out of scope.
import asyncio


class Dog:
    def sound(self) -> str:
        return "woof"


class Cat:
    def sound(self) -> str:
        return "meow"


async def describe(a: Dog | Cat) -> str:  # tpyc: error(/not yet supported.*res.narrowed_resume/)
    if isinstance(a, Dog):
        return "dog"
    await asyncio.sleep(0)
    return a.sound()


async def drive() -> None:
    print(await describe(Cat()))


asyncio.run(drive())
