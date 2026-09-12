# The RECEIVER of an async method on the escaping-Task route must be a stable
# lvalue or a temporary the frame can seat. A FIELD of a temporary is neither:
# the owner dies at the end of the statement while the coroutine keeps
# `const Summer&` into it. The generator route rejects the same receiver shape
# at lowering, and sema already rejects it at a bound / awaited position.
import asyncio

from tpy import int32, Own


class Summer:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    async def add(self, x: int32) -> int32:
        await asyncio.sleep(0.0)
        return self.base + x


class Pair:
    left: Summer
    right: Summer

    def __init__(self, left: Own[Summer], right: Own[Summer]) -> None:
        self.left = left
        self.right = right


def make_pair(a: int32, b: int32) -> Own[Pair]:
    return Pair(Summer(a), Summer(b))


async def outer() -> int32:
    # The receiver's OWNER is the temporary; `.left` only borrows into it.
    t = asyncio.create_task(make_pair(100, 200).left.add(1))  # tpyc: error(/must be a stable lvalue/)
    await asyncio.sleep(0.0)
    return await t


def main() -> None:
    print("res:", asyncio.run(outer()))


main()
