# Regression: a non-value union (Dog | Cat) passed to an `async def` factory --
# the await/emplace path must union-wrap it (pointer-variant borrow form). The
# rvalue temps `Dog()` / `Cat()` are hoisted into the awaiter's frame
# (`__coro_arg_N`) so the sub-coro's `variant<Cat*, Dog*>` borrows a frame
# field that outlives the suspension.
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
