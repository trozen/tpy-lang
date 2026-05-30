# Regression: a non-value union (Dog | Cat) passed to an `async def` factory --
# the await/emplace path must union-wrap it (pointer-variant borrow form).
# Body uses only an isinstance discriminant check on purpose: a post-await
# field access on the union arg would dangle (BUGS.md async rvalue-temp emplace
# lifetime gap), so do NOT add one here.
import asyncio


class Dog:
    def __init__(self) -> None:
        pass


class Cat:
    def __init__(self) -> None:
        pass


async def describe(a: Dog | Cat) -> str:
    await asyncio.sleep(0)
    if isinstance(a, Dog):
        return "dog"
    return "cat"


async def main() -> None:
    print(await describe(Dog()))
    print(await describe(Cat()))


asyncio.run(main())
