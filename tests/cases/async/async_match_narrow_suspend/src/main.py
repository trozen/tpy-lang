# match-arm subject narrowing re-established across an await (async shares the
# resumable frame with generators): the matched type is read after the await.
import asyncio


class Dog:
    def sound(self) -> str:
        return "woof"


class Cat:
    def sound(self) -> str:
        return "meow"


async def voice(a: Dog | Cat) -> str:
    match a:
        case Dog():
            await asyncio.sleep(0)
            return a.sound()
        case Cat():
            return a.sound()


async def amain() -> None:
    print(await voice(Dog()))
    print(await voice(Cat()))


asyncio.run(amain())
