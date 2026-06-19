# A resumable frame (generator/async) iterating a narrowed `bytes | None` /
# `str | None` across a yield/await iterates the contained value.
import asyncio
from typing import Iterator


def each_byte(b: bytes | None) -> Iterator[int]:
    if b is None:
        return
    for x in b:
        yield int(x)


async def count_chars(s: str | None) -> int:
    if s is None:
        return -1
    n = 0
    for _c in s:
        await asyncio.sleep(0)
        n += 1
    return n


async def main_coro() -> None:
    total = 0
    for v in each_byte(b"ab" + b"c"):
        total += v
    print(total)
    print(await count_chars("hello"))


def main() -> None:
    asyncio.run(main_coro())


main()
