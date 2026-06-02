# Narrowing a non-value union param via isinstance inside an async body
# (shared resumable frame with generators) then accessing an arm-specific
# member after a suspension. Mirrors the generator regression on the async
# emit path.
import asyncio


class Dog:
    def sound(self) -> str:
        return "woof"


class Cat:
    def sound(self) -> str:
        return "meow"


async def voice(a: Dog | Cat) -> str:
    await asyncio.sleep(0)
    if isinstance(a, Dog):  # tpyc: ok
        await asyncio.sleep(0)
        return a.sound()
    return a.sound()


async def amain() -> None:
    print(await voice(Dog()))
    print(await voice(Cat()))


asyncio.run(amain())
