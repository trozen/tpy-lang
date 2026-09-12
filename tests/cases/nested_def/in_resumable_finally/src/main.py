# Nested defs and finally blocks in resumable bodies: a def DEFINED inside a
# finally, and a def defined in the body but CALLED from a finally.
import asyncio
from typing import Iterator
from tpy import int32


async def def_in_finally() -> int32:
    total = 0
    try:
        await asyncio.sleep(0)
    finally:

        def bump() -> None:
            nonlocal total
            total += 5

        bump()
        bump()
    return total


def gen_def_in_finally() -> Iterator[int32]:
    total = 0
    try:
        yield 1
    finally:

        def bump() -> None:
            nonlocal total
            total += 3

        bump()
    yield total


async def called_from_finally() -> int32:
    count = 0

    def tick() -> None:
        nonlocal count
        count += 1

    try:
        tick()
        await asyncio.sleep(0)
    finally:
        tick()
    return count


async def main() -> None:
    print(await def_in_finally())
    for v in gen_def_in_finally():
        print(v)
    print(await called_from_finally())


asyncio.run(main())
