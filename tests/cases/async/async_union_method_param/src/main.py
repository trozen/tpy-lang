# Regression: a non-value union param on an `async def` METHOD (not just a free
# function) must use the pointer-variant borrow form. `describe` mutates self so
# it is not readonly (the readonly-method union param shape is a known bug).
import asyncio


class Dog:
    def __init__(self) -> None:
        pass


class Cat:
    def __init__(self) -> None:
        pass


class Shelter:
    seen: int

    def __init__(self) -> None:
        self.seen = 0

    async def describe(self, a: Dog | Cat) -> str:
        self.seen += 1
        await asyncio.sleep(0)
        if isinstance(a, Dog):
            return "dog"
        return "cat"


async def main() -> None:
    s = Shelter()
    print(await s.describe(Dog()))
    print(await s.describe(Cat()))
    print(s.seen)


asyncio.run(main())
