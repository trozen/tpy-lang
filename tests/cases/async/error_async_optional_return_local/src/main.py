# An async def returning `T | None` in borrow form (the post-B41 convention)
# cannot return a freshly-constructed local -- the borrow would dangle past the
# await, exactly like the sync sibling. The dangling-return check gates it and
# points at Own[T] | None. This guards the rejection the borrow-form async
# return convention relies on.
import asyncio


class Box:
    val: int
    def __init__(self, v: int) -> None:
        self.val = v


async def make() -> Box | None:
    await asyncio.sleep(0)
    return Box(1)  # tpyc: error(/Cannot return local or temporary.*Box . None/)


def main() -> None:
    asyncio.run(make())


main()
